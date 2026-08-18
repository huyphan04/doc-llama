"""
State machine — spec section 10.

INIT -> INSPECT -> PLAN -> IMPLEMENT -> STATIC_TEST -> BUILD -> START_APP
-> BROWSER_TEST -> VISUAL_REVIEW -> REVIEW -> PASS

On failure at any stage: FAIL -> ANALYZE_FAILURE -> CREATE_FIX_PLAN ->
IMPLEMENT_FIX -> RETEST (loops back into STATIC_TEST).

This module defines the states and valid transitions only — orchestrator.py
drives the actual loop by calling agent functions and moving between these
states. Keeping the state enum + transition table separate from the driving
logic makes it possible to unit test "is this a legal transition" without
spinning up any agents or LLM calls.
"""
from __future__ import annotations

from enum import Enum


class State(str, Enum):
    INIT = "INIT"
    INSPECT = "INSPECT"
    PLAN = "PLAN"
    IMPLEMENT = "IMPLEMENT"
    STATIC_TEST = "STATIC_TEST"
    BUILD = "BUILD"
    START_APP = "START_APP"
    BROWSER_TEST = "BROWSER_TEST"
    VISUAL_REVIEW = "VISUAL_REVIEW"
    REVIEW = "REVIEW"
    ANALYZE_FAILURE = "ANALYZE_FAILURE"
    CREATE_FIX_PLAN = "CREATE_FIX_PLAN"
    IMPLEMENT_FIX = "IMPLEMENT_FIX"
    PASS = "PASS"
    BLOCKED = "BLOCKED"


# Terminal states — the orchestrator loop stops once one of these is reached.
TERMINAL_STATES = {State.PASS, State.BLOCKED}

# Valid forward transitions on success at each state.
_SUCCESS_TRANSITIONS: dict[State, State] = {
    State.INIT: State.INSPECT,
    State.INSPECT: State.PLAN,
    State.PLAN: State.IMPLEMENT,
    State.IMPLEMENT: State.STATIC_TEST,
    State.STATIC_TEST: State.BUILD,
    State.BUILD: State.START_APP,
    State.START_APP: State.BROWSER_TEST,
    State.BROWSER_TEST: State.VISUAL_REVIEW,
    State.VISUAL_REVIEW: State.REVIEW,
    State.REVIEW: State.PASS,
    State.ANALYZE_FAILURE: State.CREATE_FIX_PLAN,
    State.CREATE_FIX_PLAN: State.IMPLEMENT_FIX,
    State.IMPLEMENT_FIX: State.STATIC_TEST,  # RETEST loops back into verification
}

# States where a failure routes into the fix loop instead of terminating.
_FAILURE_TRANSITIONS: dict[State, State] = {
    State.STATIC_TEST: State.ANALYZE_FAILURE,
    State.BUILD: State.ANALYZE_FAILURE,
    State.START_APP: State.ANALYZE_FAILURE,
    State.BROWSER_TEST: State.ANALYZE_FAILURE,
    State.VISUAL_REVIEW: State.ANALYZE_FAILURE,
    State.REVIEW: State.ANALYZE_FAILURE,
}


class IllegalTransitionError(Exception):
    pass


def next_state(current: State, success: bool) -> State:
    if current in TERMINAL_STATES:
        raise IllegalTransitionError(f"cannot transition out of terminal state {current}")

    if success:
        nxt = _SUCCESS_TRANSITIONS.get(current)
        if nxt is None:
            raise IllegalTransitionError(f"no success transition defined from {current}")
        return nxt

    nxt = _FAILURE_TRANSITIONS.get(current)
    if nxt is None:
        raise IllegalTransitionError(f"no failure transition defined from {current} (this stage cannot fail)")
    return nxt
