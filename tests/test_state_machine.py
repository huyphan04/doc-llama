import pytest

from agent.state_machine import IllegalTransitionError, State, TERMINAL_STATES, next_state


def test_happy_path_sequence():
    path = [State.INIT]
    s = State.INIT
    while s not in TERMINAL_STATES:
        s = next_state(s, success=True)
        path.append(s)
    assert path == [
        State.INIT, State.INSPECT, State.PLAN, State.IMPLEMENT, State.STATIC_TEST,
        State.BUILD, State.START_APP, State.BROWSER_TEST, State.VISUAL_REVIEW,
        State.REVIEW, State.PASS,
    ]


def test_build_failure_routes_to_analyze():
    assert next_state(State.BUILD, success=False) == State.ANALYZE_FAILURE


def test_fix_loop_returns_to_static_test():
    assert next_state(State.ANALYZE_FAILURE, success=True) == State.CREATE_FIX_PLAN
    assert next_state(State.CREATE_FIX_PLAN, success=True) == State.IMPLEMENT_FIX
    assert next_state(State.IMPLEMENT_FIX, success=True) == State.STATIC_TEST


def test_cannot_transition_from_terminal_state():
    with pytest.raises(IllegalTransitionError):
        next_state(State.PASS, success=True)
    with pytest.raises(IllegalTransitionError):
        next_state(State.BLOCKED, success=False)


def test_review_failure_routes_to_analyze():
    assert next_state(State.REVIEW, success=False) == State.ANALYZE_FAILURE


def test_init_cannot_fail():
    with pytest.raises(IllegalTransitionError):
        next_state(State.INIT, success=False)


def test_full_iteration_loop_simulation():
    """Simulates spec section 10's example: build fails, fix, browser fails, fix, all pass."""
    s = State.INIT
    s = next_state(s, True)   # INSPECT
    s = next_state(s, True)   # PLAN
    s = next_state(s, True)   # IMPLEMENT
    s = next_state(s, True)   # STATIC_TEST (pass)
    s = next_state(s, False)  # BUILD fails
    assert s == State.ANALYZE_FAILURE
    s = next_state(s, True)   # CREATE_FIX_PLAN
    s = next_state(s, True)   # IMPLEMENT_FIX
    s = next_state(s, True)   # -> STATIC_TEST (retest)
    assert s == State.STATIC_TEST
    s = next_state(s, True)   # STATIC_TEST pass -> BUILD
    s = next_state(s, True)   # BUILD pass -> START_APP
    s = next_state(s, True)   # START_APP pass -> BROWSER_TEST
    s = next_state(s, False)  # BROWSER_TEST fails
    assert s == State.ANALYZE_FAILURE
