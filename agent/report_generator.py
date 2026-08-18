"""
Final report — spec section 21.

Renders reports/<task_id>.md from a completed RunResult. Deliberately
avoids absolute claims like "100% perfect" / "guaranteed" / "bug free"
per spec section 21's explicit instruction, and always ends with
READY_FOR_HUMAN_REVIEW or BLOCKED — never a claim of production-readiness
the agent can't actually back up (spec section 22).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from agent.orchestrator import RunResult
from agent.task_parser import Task

FORBIDDEN_PHRASES = ["100% perfect", "guaranteed", "bug free", "bug-free", "production-ready", "flawless"]


def _fmt_duration(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def _last_tester_report(result: RunResult):
    for it in reversed(result.iterations):
        if it.tester_report:
            return it.tester_report
    return None


def _last_browser_results(result: RunResult):
    for it in reversed(result.iterations):
        if it.browser_results:
            return it.browser_results
    return []


def generate_report(task: Task, result: RunResult) -> str:
    started = datetime.fromtimestamp(result.started_at, tz=timezone.utc).isoformat()
    finished = datetime.fromtimestamp(result.finished_at, tz=timezone.utc).isoformat()
    duration = _fmt_duration(result.finished_at - result.started_at)

    lines: list[str] = []
    lines.append(f"# Report: {task.title}")
    lines.append("")
    lines.append(f"**Task:** {task.task_id}")
    lines.append(f"**Status:** {result.final_status}")
    lines.append(f"**Started:** {started}")
    lines.append(f"**Finished:** {finished}")
    lines.append(f"**Duration:** {duration}")
    lines.append(f"**Iterations:** {len(result.iterations)}")
    lines.append(f"**Git branch:** `{result.branch_name}`")
    lines.append("")

    if result.final_status == "BLOCKED":
        lines.append("## Why blocked")
        lines.append("")
        lines.append(result.block_reason or "(no reason recorded)")
        lines.append("")

    # Files changed — from the last iteration's coder summary
    all_files_changed: set[str] = set()
    all_files_created: set[str] = set()
    for it in result.iterations:
        all_files_changed.update(it.coder_summary.get("files_changed", []) or [])
        all_files_created.update(it.coder_summary.get("files_created", []) or [])
    lines.append("## Files changed")
    lines.append("")
    if all_files_changed or all_files_created:
        for f in sorted(all_files_created):
            lines.append(f"- `{f}` (created)")
        for f in sorted(all_files_changed):
            lines.append(f"- `{f}` (modified)")
    else:
        lines.append("(none recorded)")
    lines.append("")

    # Tests
    lines.append("## Tests")
    lines.append("")
    tester = _last_tester_report(result)
    if tester:
        for c in tester.checks:
            lines.append(f"- **{c.name}**: {c.status}" + (f" — {c.error[:200]}" if c.status == "FAIL" else ""))
    else:
        lines.append("(no test results recorded)")
    lines.append("")

    # Browser / responsive
    lines.append("## Browser / Responsive")
    lines.append("")
    browser_results = _last_browser_results(result)
    if browser_results:
        for r in browser_results:
            status = "OK" if (r.ok and not r.console_errors and not r.page_errors and not r.horizontal_overflow) else "ISSUES"
            lines.append(f"- **{r.viewport_name}** ({r.width}x{r.height}): {status}")
            if r.horizontal_overflow:
                lines.append(f"  - horizontal overflow: {r.overflow_detail}")
            if r.screenshot_path:
                lines.append(f"  - screenshot: `{r.screenshot_path}`")
    else:
        lines.append("(no browser tests recorded)")
    lines.append("")

    # Console / network errors
    lines.append("## Console errors")
    lines.append("")
    console_errors = [f"[{r.viewport_name}] {c.text}" for r in browser_results for c in r.console_errors]
    if console_errors:
        for e in console_errors:
            lines.append(f"- {e}")
    else:
        lines.append("(none)")
    lines.append("")

    lines.append("## Network errors")
    lines.append("")
    network_errors = [
        f"[{r.viewport_name}] {n.method} {n.url} -> {n.status or n.failure_text}"
        for r in browser_results for n in r.network_failures
    ]
    if network_errors:
        for e in network_errors:
            lines.append(f"- {e}")
    else:
        lines.append("(none)")
    lines.append("")

    # Visual issues
    lines.append("## Visual issues")
    lines.append("")
    visual_issues: list[str] = []
    for it in result.iterations:
        visual_issues.extend(it.visual_issues)
    if visual_issues:
        for v in visual_issues:
            lines.append(f"- {v}")
    else:
        lines.append("(none reported by vision review, or vision review was not configured)")
    lines.append("")

    # Remaining risks — from Planner's original risk list + any unresolved reviewer issues
    lines.append("## Remaining risks")
    lines.append("")
    final_verdict = result.iterations[-1].review_verdict if result.iterations else None
    if final_verdict and final_verdict.issues:
        for i in final_verdict.issues:
            lines.append(f"- {i}")
    else:
        lines.append("(none flagged by the final review — this does not guarantee there are none; human review is still required)")
    lines.append("")

    lines.append("## Recommended human review")
    lines.append("")
    lines.append("- Run `git diff` on the branch above and read every change.")
    lines.append("- Manually check the UI at each viewport before merging.")
    lines.append("- Re-run the full test suite locally if this report is more than a few hours old.")
    lines.append("")

    lines.append("## Git diff summary")
    lines.append("")
    if all_files_changed or all_files_created:
        lines.append(f"{len(all_files_created)} file(s) created, {len(all_files_changed)} file(s) modified. "
                      f"Run `git diff main...{result.branch_name}` for the full diff.")
    else:
        lines.append("No file changes were recorded.")
    lines.append("")

    lines.append("---")
    lines.append("")
    if result.final_status == "READY_FOR_HUMAN_REVIEW":
        lines.append("**STATUS: READY_FOR_HUMAN_REVIEW**")
    else:
        lines.append("**STATUS: BLOCKED**")
    lines.append("")
    lines.append(
        "This report reflects automated checks only. It is not a claim that the implementation "
        "is complete, correct, or ready to merge without review."
    )

    report_text = "\n".join(lines)

    # Defensive check: never let forbidden absolute-certainty language slip
    # into a generated report, even if some future edit accidentally adds it.
    lowered = report_text.lower()
    for phrase in FORBIDDEN_PHRASES:
        if phrase in lowered:
            raise AssertionError(f"report generator produced forbidden phrase '{phrase}' — spec section 21 violation")

    return report_text


def write_report(task: Task, result: RunResult, reports_dir: Path) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"{task.task_id}.md"
    path.write_text(generate_report(task, result), encoding="utf-8")
    return path
