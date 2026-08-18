"""
Shared tool-calling loop.

Each agent role (Planner, Coder, Tester, Reviewer) is a thin wrapper
around this: send a system prompt + task, let the model call tools
across several turns, then require a final structured JSON answer
matching that role's schema (spec section 19: "no free text protocol
between agents").

Why a shared loop instead of one per role: the failure modes are role-
independent (model doesn't call tools, model calls an unknown tool, model
returns malformed JSON at the end) and section 31 requires retry-on-
invalid-response behavior everywhere; keeping this in one place means
that policy is applied uniformly instead of copy-pasted per agent.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Protocol

from agent.llm.base import ChatMessage, ChatResult, LLMClient, LLMError, ToolCall
from agent.logging_setup import AgentLogger
from agent.tool_executor import TOOL_SPECS, ExecResult


class ToolDispatcher(Protocol):
    """Structural type satisfied by both ToolExecutor and
    planner.ReadOnlyToolExecutor — the role loop only needs .execute()."""

    def execute(self, tool_name: str, arguments: dict) -> ExecResult: ...


class AgentResponseError(Exception):
    """Raised when the model never produces a valid final JSON answer
    after all retries — the caller (state machine) must treat this as a
    tool/LLM failure, not silently proceed with a guessed default."""


@dataclass
class RoleLoopResult:
    final_json: dict
    transcript: list[ChatMessage]
    tool_call_count: int


FINAL_ANSWER_MARKER = "FINAL_ANSWER_JSON:"


def _extract_json_block(text: str) -> dict | None:
    """The model is instructed to emit FINAL_ANSWER_JSON: {...} as its last
    message once done calling tools. We look for that marker first, and
    fall back to the last {...} block in the text for models that forget
    the exact marker but still emit valid JSON."""
    if FINAL_ANSWER_MARKER in text:
        candidate = text.split(FINAL_ANSWER_MARKER, 1)[1].strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    matches = re.findall(r"\{.*\}", text, flags=re.DOTALL)
    for candidate in reversed(matches):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


def run_role_loop(
    llm: LLMClient,
    logger: AgentLogger,
    executor: ToolDispatcher,
    system_prompt: str,
    user_prompt: str,
    role_name: str,
    max_turns: int = 25,
    max_response_retries: int = 3,
) -> RoleLoopResult:
    messages: list[ChatMessage] = [
        ChatMessage(role="system", content=system_prompt),
        ChatMessage(role="user", content=user_prompt),
    ]
    tool_call_count = 0

    for turn in range(1, max_turns + 1):
        result: ChatResult | None = None
        last_err: Exception | None = None
        for attempt in range(1, max_response_retries + 1):
            try:
                result = llm.chat(messages, tools=TOOL_SPECS, temperature=0.2)
                break
            except LLMError as e:
                last_err = e
                logger.warning(f"[{role_name}] LLM call failed (attempt {attempt}): {e}")
        if result is None:
            raise AgentResponseError(f"[{role_name}] LLM unreachable after {max_response_retries} attempts: {last_err}")

        messages.append(result.message)

        if result.message.tool_calls:
            for tc in result.message.tool_calls:
                tool_call_count += 1
                exec_result = executor.execute(tc.name, tc.arguments)
                messages.append(
                    ChatMessage(
                        role="tool",
                        content=json.dumps(exec_result.to_dict(), default=str),
                        tool_call_id=tc.id or tc.name,
                    )
                )
            continue  # give the model the tool results, let it continue

        # No tool calls this turn — model believes it's done. Look for the
        # final structured answer.
        parsed = _extract_json_block(result.message.content)
        if parsed is not None:
            return RoleLoopResult(final_json=parsed, transcript=messages, tool_call_count=tool_call_count)

        # Model stopped calling tools but didn't give valid JSON either —
        # nudge it once per spec section 31 ("if LLM response invalid, retry").
        messages.append(
            ChatMessage(
                role="user",
                content=(
                    f"Your last message did not contain a valid JSON object after "
                    f"'{FINAL_ANSWER_MARKER}'. If you are done, respond with ONLY "
                    f"'{FINAL_ANSWER_MARKER}' followed by the required JSON object. "
                    f"If you are not done, continue calling tools."
                ),
            )
        )

    raise AgentResponseError(f"[{role_name}] exceeded max_turns={max_turns} without a final structured answer")
