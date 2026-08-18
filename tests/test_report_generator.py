import time

import pytest

from agent.agents.reviewer import ReviewVerdict
from agent.agents.tester import CheckResult, TesterReport
from agent.orchestrator import IterationRecord, RunResult
from agent.report_generator import FORBIDDEN_PHRASES, generate_report
from agent.task_parser import parse_task_text
from tools.browser import ViewportCheckResult


def make_task():
    return parse_task_text(
        "# Task\nBuild a landing page.\n\n## Acceptance Criteria\n- Build passes.\n", task_id="landing-page"
    )


def test_pass_report_contains_ready_status():
    task = make_task()
    it = IterationRecord(
        iteration=1,
        tester_report=TesterReport(overall_status="PASS", checks=[CheckResult(name="build", ran=True, status="PASS")]),
        coder_summary={"files_changed": ["src/Landing.tsx"], "files_created": []},
        review_verdict=ReviewVerdict(status="PASS", score=95, issues=[], required_fixes=[]),
    )
    result = RunResult(
        task_id="landing-page", final_status="READY_FOR_HUMAN_REVIEW", iterations=[it],
        branch_name="ai/landing-page-20260101-000000", started_at=time.time() - 120, finished_at=time.time(),
    )
    report = generate_report(task, result)
    assert "STATUS: READY_FOR_HUMAN_REVIEW" in report
    assert "src/Landing.tsx" in report
    assert "build" in report.lower()


def test_blocked_report_contains_reason():
    task = make_task()
    result = RunResult(
        task_id="landing-page", final_status="BLOCKED", iterations=[],
        branch_name="ai/landing-page-20260101-000000", started_at=time.time() - 60, finished_at=time.time(),
        block_reason="exceeded max_iterations (30)",
    )
    report = generate_report(task, result)
    assert "STATUS: BLOCKED" in report
    assert "exceeded max_iterations" in report


def test_no_forbidden_absolute_claims():
    task = make_task()
    it = IterationRecord(
        iteration=1,
        tester_report=TesterReport(overall_status="PASS", checks=[CheckResult(name="build", ran=True, status="PASS")]),
        review_verdict=ReviewVerdict(status="PASS", score=100, issues=[], required_fixes=[]),
    )
    result = RunResult(
        task_id="landing-page", final_status="READY_FOR_HUMAN_REVIEW", iterations=[it],
        branch_name="ai/x", started_at=time.time() - 10, finished_at=time.time(),
    )
    report = generate_report(task, result)
    lowered = report.lower()
    for phrase in FORBIDDEN_PHRASES:
        assert phrase not in lowered


def test_includes_browser_and_console_errors():
    task = make_task()
    browser_result = ViewportCheckResult(
        viewport_name="mobile", width=375, height=812, ok=True,
        horizontal_overflow=True, overflow_detail="scrollWidth=500 > clientWidth=375",
    )
    it = IterationRecord(iteration=1, browser_results=[browser_result])
    result = RunResult(
        task_id="landing-page", final_status="BLOCKED", iterations=[it],
        branch_name="ai/x", started_at=time.time() - 10, finished_at=time.time(),
        block_reason="test",
    )
    report = generate_report(task, result)
    assert "horizontal overflow" in report.lower()
    assert "scrollWidth=500" in report


def test_report_never_raises_on_empty_result():
    task = make_task()
    result = RunResult(
        task_id="landing-page", final_status="BLOCKED", iterations=[],
        branch_name="", started_at=time.time(), finished_at=time.time(),
    )
    report = generate_report(task, result)
    assert "landing-page" in report
