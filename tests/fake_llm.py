"""
A scripted fake LLMClient for tests — returns pre-programmed responses in
sequence, so we can drive the orchestrator through a realistic scenario
(plan -> implement -> build fails -> fix -> build passes -> browser passes
-> review passes) without any network call or real Ollama instance.

This directly exercises spec section 27's self-test requirement for the
Autonomous Agent itself: "if there's a bug in the Agent itself, fix it,
rerun tests" — these tests are what would have caught orchestrator bugs
before ever pointing this at your real Ollama + real project.
"""
from __future__ import annotations

import json

from agent.llm.base import ChatMessage, ChatResult, LLMClient, ToolCall


class ScriptedLLMClient(LLMClient):
    """Each call to chat() consumes the next item from `script`. An item is
    either a dict (-> final JSON answer, no tool calls) or a list of
    (tool_name, arguments) tuples (-> emit those as tool_calls this turn).
    vision() always returns {"issues": []} unless vision_script is set."""

    def __init__(self, script: list, vision_script: list | None = None):
        self.script = list(script)
        self.vision_script = list(vision_script) if vision_script is not None else None
        self.calls_made = 0

    def chat(self, messages, *, tools=None, temperature=0.2, model=None) -> ChatResult:
        if not self.script:
            raise AssertionError(f"ScriptedLLMClient ran out of script entries after {self.calls_made} calls")
        item = self.script.pop(0)
        self.calls_made += 1

        if isinstance(item, dict):
            content = f"FINAL_ANSWER_JSON:\n{json.dumps(item)}"
            return ChatResult(message=ChatMessage(role="assistant", content=content))

        tool_calls = [ToolCall(name=name, arguments=args, id=f"call_{i}") for i, (name, args) in enumerate(item)]
        return ChatResult(message=ChatMessage(role="assistant", content="", tool_calls=tool_calls))

    def vision(self, prompt: str, image_path: str, *, temperature: float = 0.1) -> ChatResult:
        if self.vision_script:
            item = self.vision_script.pop(0)
        else:
            item = {"issues": []}
        return ChatResult(message=ChatMessage(role="assistant", content=json.dumps(item)))

    def health_check(self):
        return True, "fake client always healthy"
