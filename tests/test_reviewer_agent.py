from agent.agents.reviewer import ReviewVerdict, _hard_fail_if_missing_required_checks
from agent.agents.tester import CheckResult, TesterReport


def test_pass_overridden_when_build_missing():
    tester = TesterReport(overall_status="PASS", checks=[
        CheckResult(name="lint", ran=True, status="PASS"),
    ])
    verdict = ReviewVerdict(status="PASS", score=90)
    result = _hard_fail_if_missing_required_checks(verdict, tester)
    assert result.status == "FAIL"
    assert any("build" in f for f in result.required_fixes)


def test_pass_overridden_when_build_failed():
    tester = TesterReport(overall_status="FAIL", checks=[
        CheckResult(name="build", ran=True, status="FAIL", error="TypeError in Hero.tsx"),
    ])
    verdict = ReviewVerdict(status="PASS", score=95)
    result = _hard_fail_if_missing_required_checks(verdict, tester)
    assert result.status == "FAIL"


def test_pass_preserved_when_build_passed_and_no_failures():
    tester = TesterReport(overall_status="PASS", checks=[
        CheckResult(name="build", ran=True, status="PASS"),
        CheckResult(name="lint", ran=True, status="PASS"),
    ])
    verdict = ReviewVerdict(status="PASS", score=95)
    result = _hard_fail_if_missing_required_checks(verdict, tester)
    assert result.status == "PASS"


def test_fail_status_not_touched():
    tester = TesterReport(overall_status="PASS", checks=[
        CheckResult(name="build", ran=True, status="PASS"),
    ])
    verdict = ReviewVerdict(status="FAIL", score=40, required_fixes=["some other issue"])
    result = _hard_fail_if_missing_required_checks(verdict, tester)
    assert result.status == "FAIL"
    assert result.required_fixes == ["some other issue"]  # not polluted with build note


def test_blocked_status_not_touched():
    tester = TesterReport(overall_status="FAIL", checks=[])
    verdict = ReviewVerdict(status="BLOCKED", score=0)
    result = _hard_fail_if_missing_required_checks(verdict, tester)
    assert result.status == "BLOCKED"
