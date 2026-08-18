"""
Lightweight run-state persistence for the CLI's status/logs/report/stop
commands — spec section 23.

This is intentionally simple: one JSON file per task under
logs/<task_id>/run_state.json, written at the start and end of a run (and
periodically is a future improvement — see README limitations). `resume`
in this MVP is honest about being limited: it can tell you the last known
state and let you re-run the task from scratch, but true mid-iteration
resume (restoring an in-progress Coder/Tester loop) is a Phase 6 item not
implemented yet — see spec section 6 in the README about this.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class RunState:
    task_id: str
    status: str  # "RUNNING" | "READY_FOR_HUMAN_REVIEW" | "BLOCKED" | "ERROR"
    started_at: float
    updated_at: float
    branch_name: str = ""
    current_iteration: int = 0
    pid: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def state_path(logs_dir: Path, task_id: str) -> Path:
    return logs_dir / task_id / "run_state.json"


def write_state(logs_dir: Path, state: RunState) -> None:
    p = state_path(logs_dir, state.task_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    state.updated_at = time.time()
    p.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")


def read_state(logs_dir: Path, task_id: str) -> RunState | None:
    p = state_path(logs_dir, task_id)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return RunState(**data)
    except (json.JSONDecodeError, TypeError, OSError):
        return None


def list_all_states(logs_dir: Path) -> list[RunState]:
    if not logs_dir.exists():
        return []
    states = []
    for child in sorted(logs_dir.iterdir()):
        if child.is_dir():
            s = read_state(logs_dir, child.name)
            if s:
                states.append(s)
    return states
