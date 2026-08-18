"""
Reviewer agent — spec section 4.

Explicitly does NOT trust "Coder said implementation complete". It is
handed the raw evidence (tester report, browser check results, git diff,
acceptance criteria, repeated-failure history) and an LLM makes the final
PASS/FAIL/BLOCKED judgment against that evidence. Read-only, same as
Planner — the Reviewer inspects, it does not fix.

If any REQUIRED verification (per the task's acceptance criteria) never
ran, the Reviewer must fail the task rather than pass it — this is
enforced in the prompt AND cross-checked in code (`_hard_fail_if_missing_
required_checks`) because spec section 32 treats "reported PASS without
running a required check" as a severe defect, not something to leave to
model judgment alone.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from agent.agents.planner import ReadOnlyToolExecutor
from agent.agents.tester import TesterReport
from agent.llm.base import LLMClient
from agent.logging_setup import AgentLogger
from agent.role_loop import FINAL_ANSWER_MARKER, run_role_loop
from agent.task_parser import Task
from agent.tool_executor import ToolExecutor
from tools.browser import ViewportCheckResult


@dataclass
class ReviewVerdict:
    status: str  # "PASS" | "FAIL" | "BLOCKED"
    score: int = 0
    issues: list[str] = field(default_factory=list)
    required_fixes: list[str] = field(default_factory=list)
    reasoning: str = ""

    def to_dict(self) -> dict:
        return {
            "status": self.status, "score": self.score, "issues": self.issues,
            "required_fixes": self.required_fixes, "reasoning": self.reasoning,
        }


SYSTEM_PROMPT = f"""You are the REVIEWER in an autonomous coding agent system.

You independently judge whether an implementation is actually done. You do NOT trust the
Coder's self-reported summary — base your verdict only on: tester results, browser results,
screenshots (described to you), git diff, and the task's acceptance criteria.

Critical rule: if a REQUIRED verification for this task's acceptance criteria never ran
(status SKIPPED when it should have run, or missing entirely), you MUST return status FAIL
with that listed in required_fixes — never PASS with an unverified criterion.

You may call read-only tools to inspect further: list_files, read_file, search_code,
git_status, git_diff, git_log.

Respond with ONLY:
{FINAL_ANSWER_MARKER}
{{
  "status": "PASS" | "FAIL" | "BLOCKED",
  "score": 0-100,
  "issues": ["..."],
  "required_fixes": ["..."],
  "reasoning": "..."
}}
"""


def _summarize_browser_results(results: list[ViewportCheckResult]) -> str:
    lines = []
    for r in results:
        status = "OK" if (r.ok and not r.console_errors and not r.page_errors and not r.horizontal_overflow) else "ISSUES"
        lines.append(
            f"- {r.viewport_name} ({r.width}x{r.height}): {status} | "
            f"overflow={r.horizontal_overflow} ({r.overflow_detail}) | "
            f"console_errors={[c.text for c in r.console_errors]} | "
            f"page_errors={r.page_errors} | "
            f"network_failures={[(n.url, n.status) for n in r.network_failures]} | "
            f"error={r.error}"
        )
    return "\n".join(lines) if lines else "(no browser checks ran)"


def run_reviewer(
    llm: LLMClient,
    logger: AgentLogger,
    executor: ToolExecutor,
    task: Task,
    coder_summary: dict,
    tester_report: TesterReport,
    browser_results: list[ViewportCheckResult],
    git_diff_text: str,
    repeated_failure_note: str = "",
) -> ReviewVerdict:
    ro_executor = ReadOnlyToolExecutor(executor)

    user_prompt = (
        f"# Task\n{task.title}\n{task.description}\n\n"
        f"# Acceptance Criteria\n" + "\n".join(f"- {a}" for a in task.acceptance_criteria) + "\n\n"
        f"# Coder's self-reported summary (do not just trust this)\n{coder_summary}\n\n"
        f"# Tester results\n{tester_report.to_dict()}\n\n"
        f"# Browser/responsive results\n{_summarize_browser_results(browser_results)}\n\n"
        f"# Git diff\n```\n{git_diff_text[:8000]}\n```\n\n"
        + (f"# Repeated failure note\n{repeated_failure_note}\n\n" if repeated_failure_note else "")
        + "Review the evidence and give your verdict."
    )

    result = run_role_loop(
        llm=llm,
        logger=logger,
        executor=ro_executor,
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        role_name="REVIEWER",
        max_turns=15,
    )
    d = result.final_json
    verdict = ReviewVerdict(
        status=d.get("status", "FAIL"),
        score=int(d.get("score", 0) or 0),
        issues=list(d.get("issues", [])),
        required_fixes=list(d.get("required_fixes", [])),
        reasoning=str(d.get("reasoning", "")),
    )
    verdict = _hard_fail_if_missing_required_checks(verdict, tester_report)
    logger.info(f"Reviewer verdict: {verdict.status} (score={verdict.score})", issues=verdict.issues)
    return verdict


def _hard_fail_if_missing_required_checks(verdict: ReviewVerdict, tester_report: TesterReport) -> ReviewVerdict:
    """Defense in depth: even if the LLM reviewer says PASS, code-level
    override to FAIL if a check that ran shows FAIL, or if build never ran
    at all — build passing is non-negotiable per acceptance criteria."""
    build_check = next((c for c in tester_report.checks if c.name == "build"), None)
    if verdict.status == "PASS":
        if build_check is None or build_check.status != "PASS":
            verdict.status = "FAIL"
            verdict.required_fixes.append("build check did not run or did not pass — cannot PASS without it")
        failed = tester_report.failed_checks()
        if failed:
            verdict.status = "FAIL"
            verdict.required_fixes.extend(f"{c.name} failed: {c.error[:200]}" for c in failed)
    return verdict
