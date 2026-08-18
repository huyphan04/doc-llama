"""
Planner agent — spec section 4.

Only permitted to use read-only tools (list_files, read_file, search_code,
git_status, git_diff, git_log). This is enforced two ways:
  1. The system prompt explicitly forbids write/shell/browser tools.
  2. A ReadOnlyToolExecutor wrapper (below) actually blocks any non-read
     tool at the dispatch level, so a model that ignores the prompt still
     can't act on it — "Planner không được tự ý sửa code" is a hard
     constraint, not a suggestion to the model.
"""
from __future__ import annotations

from agent.llm.base import LLMClient
from agent.logging_setup import AgentLogger
from agent.role_loop import FINAL_ANSWER_MARKER, run_role_loop
from agent.task_parser import Task
from agent.tool_executor import ExecResult, ToolExecutor

READ_ONLY_TOOLS = {"list_files", "read_file", "search_code", "git_status", "git_diff", "git_log"}


class ReadOnlyToolExecutor:
    """Wraps a ToolExecutor and refuses any tool not in READ_ONLY_TOOLS.
    Used only by the Planner role."""

    def __init__(self, inner: ToolExecutor):
        self._inner = inner

    def execute(self, tool_name: str, arguments: dict) -> ExecResult:
        if tool_name not in READ_ONLY_TOOLS:
            return ExecResult(
                ok=False,
                error=f"Planner is read-only and may not call '{tool_name}'. "
                f"Allowed: {sorted(READ_ONLY_TOOLS)}. Produce your plan for the Coder to implement instead.",
            )
        return self._inner.execute(tool_name, arguments)


SYSTEM_PROMPT = f"""You are the PLANNER in an autonomous coding agent system.

Your job:
- Read the task and its acceptance criteria.
- Inspect the existing repository (package.json, README, src/, components/, etc).
- Identify the project's stack (React/Next.js/Vue/etc) — do NOT assume, verify by reading files.
- Identify which files are relevant to this task.
- Produce an implementation plan for the CODER agent to execute.

You are READ-ONLY. You may only call: list_files, read_file, search_code, git_status, git_diff, git_log.
You must NEVER attempt to write, edit, or delete files, or run shell commands — those calls will be rejected.
Do not propose migrating frameworks or changing the existing architecture unless the task explicitly asks for it.
Do not propose new dependencies unless clearly necessary; if you do, explain why no dependency-free alternative works.

When you have a complete plan, respond with ONLY:
{FINAL_ANSWER_MARKER}
{{
  "status": "PLANNED",
  "detected_stack": {{"framework": "...", "styling": "...", "package_manager": "...", "notes": "..."}},
  "relevant_files": ["path1", "path2"],
  "plan_steps": ["step 1", "step 2", ...],
  "dependency_requests": [{{"package": "...", "reason": "...", "alternative_without_dependency": "...", "risk": "..."}}],
  "risks": ["..."],
  "verification_plan": {{"lint": true, "typecheck": true, "unit_tests": true, "build": true, "browser": true, "responsive": true}}
}}
"""


def run_planner(
    llm: LLMClient,
    logger: AgentLogger,
    executor: ToolExecutor,
    task: Task,
) -> dict:
    ro_executor = ReadOnlyToolExecutor(executor)
    user_prompt = (
        f"# Task: {task.title}\n\n{task.description}\n\n"
        f"## Requirements\n" + "\n".join(f"- {r}" for r in task.requirements) + "\n\n"
        f"## Acceptance Criteria\n" + "\n".join(f"- {a}" for a in task.acceptance_criteria) + "\n\n"
        f"Inspect the repository now and produce your plan."
    )
    result = run_role_loop(
        llm=llm,
        logger=logger,
        executor=ro_executor,
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        role_name="PLANNER",
    )
    logger.info(f"Planner produced plan with {len(result.final_json.get('plan_steps', []))} steps", tool_calls=result.tool_call_count)
    return result.final_json
