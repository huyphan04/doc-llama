"""
Logging: every event is written to both a human-readable log line and a
machine-readable JSON-lines log, per spec section 20.

We deliberately don't use Python's logging module's default formatter
tricks for the JSON side — we emit our own structured records so tool
calls, state transitions, and LLM calls all carry consistent fields
(iteration, stage, task_id) that a later pass (Reviewer, report generator)
can filter on without regex-scraping text logs.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class JsonLogWriter:
    """Appends one JSON object per line to a .jsonl file. Never raises
    into the caller — a logging failure must not crash the agent."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, record: dict[str, Any]) -> None:
        record = {"ts": datetime.now(timezone.utc).isoformat(), **record}
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, default=str) + "\n")
        except OSError as e:  # pragma: no cover - defensive
            print(f"[logging] failed to write json log: {e}", file=sys.stderr)


class AgentLogger:
    """Facade combining a human-readable stream (console + text file) and
    a JSON-lines structured log. Used by every agent role and tool."""

    def __init__(self, log_dir: Path, level: str = "INFO", json_log: bool = True):
        log_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir = log_dir

        self._logger = logging.getLogger("autonomous_agent")
        self._logger.setLevel(getattr(logging, level.upper(), logging.INFO))
        self._logger.handlers.clear()
        self._logger.propagate = False

        fmt = logging.Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S")

        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(fmt)
        self._logger.addHandler(console)

        text_file = logging.FileHandler(log_dir / "agent.log", encoding="utf-8")
        text_file.setFormatter(fmt)
        self._logger.addHandler(text_file)

        self._json = JsonLogWriter(log_dir / "agent.jsonl") if json_log else None

    def event(self, message: str, level: str = "INFO", **fields: Any) -> None:
        getattr(self._logger, level.lower(), self._logger.info)(message)
        if self._json:
            self._json.write({"level": level, "message": message, **fields})

    def info(self, message: str, **fields: Any) -> None:
        self.event(message, "INFO", **fields)

    def warning(self, message: str, **fields: Any) -> None:
        self.event(message, "WARNING", **fields)

    def error(self, message: str, **fields: Any) -> None:
        self.event(message, "ERROR", **fields)

    def debug(self, message: str, **fields: Any) -> None:
        self.event(message, "DEBUG", **fields)

    def for_iteration(self, iteration_dir: Path) -> "IterationLogger":
        return IterationLogger(self, iteration_dir)


class IterationLogger:
    """Scoped logger for a single iteration — also writes tool_calls.json,
    stdout.log, stderr.log into logs/iteration-NNN/ per spec section 12."""

    def __init__(self, parent: AgentLogger, iteration_dir: Path):
        self.parent = parent
        self.dir = iteration_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self._tool_calls: list[dict[str, Any]] = []

    def log_tool_call(self, tool_name: str, input_: dict[str, Any], output: Any, ok: bool, duration_s: float) -> None:
        entry = {
            "tool": tool_name,
            "input": input_,
            "ok": ok,
            "duration_s": duration_s,
            "output_preview": _preview(output),
        }
        self._tool_calls.append(entry)
        self.parent.debug(f"tool_call {tool_name} ok={ok} ({duration_s:.2f}s)", tool=tool_name, ok=ok)
        self._flush_tool_calls()

    def _flush_tool_calls(self) -> None:
        try:
            with open(self.dir / "tool_calls.json", "w", encoding="utf-8") as f:
                json.dump(self._tool_calls, f, indent=2, default=str)
        except OSError as e:  # pragma: no cover
            self.parent.error(f"failed to write tool_calls.json: {e}")

    def append_stream(self, stream: str, text: str) -> None:
        """stream is 'stdout' or 'stderr'."""
        assert stream in ("stdout", "stderr")
        with open(self.dir / f"{stream}.log", "a", encoding="utf-8") as f:
            f.write(text)
            if not text.endswith("\n"):
                f.write("\n")

    def write_json(self, filename: str, data: Any) -> None:
        with open(self.dir / filename, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)


def _preview(output: Any, limit: int = 500) -> str:
    s = output if isinstance(output, str) else json.dumps(output, default=str)
    return s if len(s) <= limit else s[:limit] + "...[truncated]"
