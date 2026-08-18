"""
Failure memory — spec section 12.

Tracks a failure "signature" per iteration (which checks failed + their
error text, hashed) so the state machine can detect when iteration N+2
produced the exact same failure as iteration N — meaning the fix attempted
in between did not work and repeating that same kind of fix again is
unlikely to help. When this happens `should_force_strategy_change()`
returns True and the Coder's next instruction is told explicitly to try a
different approach instead of the same patch.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


def _signature(failed_check_names: list[str], error_texts: list[str]) -> str:
    raw = "|".join(sorted(failed_check_names)) + "::" + "|".join(t[:300] for t in error_texts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass
class IterationOutcome:
    iteration: int
    signature: str
    failed_check_names: list[str]
    strategy_note: str = ""


@dataclass
class FailureMemory:
    threshold: int = 2  # same signature appearing this many times => repeated failure
    history: list[IterationOutcome] = field(default_factory=list)
    strategy_change_count: int = 0

    def record(self, iteration: int, failed_check_names: list[str], error_texts: list[str]) -> IterationOutcome:
        sig = _signature(failed_check_names, error_texts)
        outcome = IterationOutcome(iteration=iteration, signature=sig, failed_check_names=failed_check_names)
        self.history.append(outcome)
        return outcome

    def repeat_count_for_latest(self) -> int:
        if not self.history:
            return 0
        latest_sig = self.history[-1].signature
        return sum(1 for h in self.history if h.signature == latest_sig)

    def should_force_strategy_change(self) -> bool:
        return self.repeat_count_for_latest() >= self.threshold

    def note_strategy_change(self) -> None:
        self.strategy_change_count += 1

    def exceeded_max_strategy_changes(self, max_changes: int) -> bool:
        return self.strategy_change_count >= max_changes

    def summary_for_prompt(self) -> str:
        if not self.history:
            return ""
        recent = self.history[-5:]
        lines = [f"  iteration {h.iteration}: failed={h.failed_check_names} (signature {h.signature})" for h in recent]
        note = ""
        if self.should_force_strategy_change():
            note = (
                "\nWARNING: the same failure signature has repeated. The previous fix attempt(s) "
                "did not resolve this. Do NOT repeat the same fix — investigate more deeply (read "
                "surrounding code, check for a different root cause) and try a genuinely different approach."
            )
        return "Recent iteration outcomes:\n" + "\n".join(lines) + note
