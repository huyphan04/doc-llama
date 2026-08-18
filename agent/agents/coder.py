"""
Coder agent — spec section 4.

Has access to filesystem tools + run_command (but not browser/process
tools — that's the Browser Tester's job, and not git write ops — commits
happen outside the agent loop under Orchestrator control, so a single bad
Coder turn can't create a stray commit). It receives either the Planner's
plan (first iteration) or a fix instruction (subsequent iterations after
a failure), and returns a structured summary of what it changed.
"""
from __future__ import annotations

from typing import Any

from agent.llm.base import LLMClient
from agent.logging_setup import AgentLogger
from agent.role_loop import FINAL_ANSWER_MARKER, run_role_loop
from agent.tool_executor import ExecResult, ToolExecutor

CODER_ALLOWED_TOOLS = {
    "list_files", "read_file", "search_code",
    "write_file", "edit_file", "delete_file",
    "run_command", "git_status", "git_diff", "git_log",
}


class CoderToolExecutor:
    """Blocks browser/process-management tools for the Coder — those
    belong to the Browser Tester role, keeping responsibilities separated
    per the multi-role architecture in spec section 4."""

    def __init__(self, inner: ToolExecutor):
        self._inner = inner

    def execute(self, tool_name: str, arguments: dict) -> ExecResult:
        if tool_name not in CODER_ALLOWED_TOOLS:
            return ExecResult(
                ok=False,
                error=f"Coder may not call '{tool_name}'. Allowed: {sorted(CODER_ALLOWED_TOOLS)}.",
            )
        return self._inner.execute(tool_name, arguments)


SYSTEM_PROMPT = f"""You are the CODER in an autonomous coding agent system.

Your job: implement the given plan (or fix) by reading and editing files in the workspace.

Rules:
- Follow the existing project architecture. Do not introduce a new framework or restructure
  the project unless explicitly instructed.
- Reuse existing components/utilities where they already solve part of the problem — search
  before writing new code.
- Do not add a new dependency unless the plan explicitly authorizes it.
- Do not break existing functionality — read a file before editing it, and prefer edit_file
  (exact unique replacement) over write_file (full overwrite) for existing files, so you don't
  accidentally delete unrelated code.
- You may run build tools via run_command (e.g. `npm install <authorized-dep>`), but do not run
  test/lint/build commands yourself — the Tester agent does that next; running them yourself
  just wastes turns.

You may call: list_files, read_file, search_code, write_file, edit_file, delete_file, run_command,
git_status, git_diff, git_log.

When you have finished implementing, respond with ONLY:
{FINAL_ANSWER_MARKER}
{{
  "status": "IMPLEMENTED",
  "summary": "what you changed and why",
  "files_changed": ["path1", "path2"],
  "files_created": ["path3"],
  "files_deleted": [],
  "notes_for_reviewer": "anything the reviewer should specifically check"
}}
"""


def run_coder(
    llm: LLMClient,
    logger: AgentLogger,
    executor: ToolExecutor,
    instruction: str,
) -> dict:
    coder_executor = CoderToolExecutor(executor)
    result = run_role_loop(
        llm=llm,
        logger=logger,
        executor=coder_executor,
        system_prompt=SYSTEM_PROMPT,
        user_prompt=instruction,
        role_name="CODER",
        max_turns=40,  # coding needs more turns than planning/review
    )
    logger.info(
        f"Coder finished: {len(result.final_json.get('files_changed', []))} changed, "
        f"{len(result.final_json.get('files_created', []))} created",
        tool_calls=result.tool_call_count,
    )
    return result.final_json


def build_initial_instruction(task_description: str, plan: dict[str, Any]) -> str:
    steps = "\n".join(f"- {s}" for s in plan.get("plan_steps", []))
    relevant = "\n".join(f"- {f}" for f in plan.get("relevant_files", []))
    return (
        f"# Task\n{task_description}\n\n"
        f"# Plan from Planner\n{steps}\n\n"
        f"# Relevant files identified by Planner\n{relevant}\n\n"
        f"Detected stack: {plan.get('detected_stack', {})}\n\n"
        f"Implement this plan now."
    )


def build_fix_instruction(previous_summary: str, required_fixes: list[str], failure_context: str) -> str:
    fixes = "\n".join(f"- {f}" for f in required_fixes)
    return (
        f"# Previous implementation\n{previous_summary}\n\n"
        f"# Verification failed — required fixes\n{fixes}\n\n"
        f"# Failure details\n{failure_context}\n\n"
        f"Fix these issues. Do not re-implement unrelated parts that were already working."
    )
