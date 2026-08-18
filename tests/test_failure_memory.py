from agent.failure_memory import FailureMemory


def test_no_repeat_on_first_failure():
    fm = FailureMemory(threshold=2)
    fm.record(1, ["build"], ["TypeError in Hero.tsx"])
    assert fm.repeat_count_for_latest() == 1
    assert not fm.should_force_strategy_change()


def test_detects_repeat_with_same_signature():
    fm = FailureMemory(threshold=2)
    fm.record(1, ["build"], ["TypeError in Hero.tsx line 10"])
    fm.record(2, ["browser"], ["console error: overflow"])  # different failure in between
    fm.record(3, ["build"], ["TypeError in Hero.tsx line 10"])  # same as iteration 1
    assert fm.repeat_count_for_latest() == 2
    assert fm.should_force_strategy_change()


def test_different_error_text_is_different_signature():
    fm = FailureMemory(threshold=2)
    fm.record(1, ["build"], ["TypeError in Hero.tsx line 10"])
    fm.record(2, ["build"], ["SyntaxError in Footer.tsx line 5"])
    assert fm.repeat_count_for_latest() == 1
    assert not fm.should_force_strategy_change()


def test_strategy_change_count_and_max():
    fm = FailureMemory(threshold=2)
    fm.note_strategy_change()
    fm.note_strategy_change()
    assert fm.exceeded_max_strategy_changes(2)
    assert not fm.exceeded_max_strategy_changes(3)


def test_summary_includes_warning_when_repeated():
    fm = FailureMemory(threshold=2)
    fm.record(1, ["build"], ["same error"])
    fm.record(2, ["build"], ["same error"])
    summary = fm.summary_for_prompt()
    assert "WARNING" in summary
    assert "Do NOT repeat" in summary


def test_summary_empty_when_no_history():
    fm = FailureMemory()
    assert fm.summary_for_prompt() == ""
