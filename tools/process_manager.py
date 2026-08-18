"""
Process management — start_process / stop_process from spec section 5.

This runs long-lived processes (e.g. `npm run dev`) in the background,
captures their stdout/stderr to files (not memory — a dev server can run
for the whole night), and gives the caller a readiness check so the
Browser Tester knows when to start hitting the URL.

Design choice: we don't try to parse "server ready" out of arbitrary dev
server output (every framework prints something different). Instead we
poll the configured URL with an HTTP HEAD/GET until it responds or a
timeout elapses — this is framework-agnostic and matches what Playwright
itself will do next anyway.
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import requests


class ProcessError(Exception):
    pass


@dataclass
class ManagedProcess:
    name: str
    command: list[str]
    cwd: Path
    proc: subprocess.Popen
    stdout_path: Path
    stderr_path: Path
    started_at: float = field(default_factory=time.monotonic)

    def is_running(self) -> bool:
        return self.proc.poll() is None

    def exit_code(self) -> Optional[int]:
        return self.proc.poll()


class ProcessManager:
    """Tracks managed background processes by name so start_process/
    stop_process can be called by name across tool calls within one
    iteration (and cleaned up at iteration end even on failure)."""

    def __init__(self, workspace_root: Path, log_dir: Path):
        self.workspace_root = Path(workspace_root).resolve()
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._processes: dict[str, ManagedProcess] = {}

    def start_process(self, name: str, command: list[str], cwd: str = ".", env_extra: dict | None = None) -> dict:
        if name in self._processes and self._processes[name].is_running():
            return {"ok": False, "error": f"process '{name}' is already running (pid {self._processes[name].proc.pid})"}

        work_dir = (self.workspace_root / cwd).resolve()
        try:
            work_dir.relative_to(self.workspace_root)
        except ValueError:
            return {"ok": False, "error": f"cwd '{cwd}' escapes workspace root"}

        stdout_path = self.log_dir / f"{name}.stdout.log"
        stderr_path = self.log_dir / f"{name}.stderr.log"
        env = os.environ.copy()
        if env_extra:
            env.update(env_extra)

        try:
            with open(stdout_path, "wb") as out, open(stderr_path, "wb") as err:
                proc = subprocess.Popen(
                    command,
                    cwd=str(work_dir),
                    stdout=out,
                    stderr=err,
                    env=env,
                    start_new_session=True,  # own process group -> can kill children too
                )
        except (OSError, FileNotFoundError) as e:
            return {"ok": False, "error": f"failed to start process: {e}"}

        self._processes[name] = ManagedProcess(
            name=name, command=command, cwd=work_dir, proc=proc,
            stdout_path=stdout_path, stderr_path=stderr_path,
        )
        return {"ok": True, "pid": proc.pid, "stdout_log": str(stdout_path), "stderr_log": str(stderr_path)}

    def wait_until_ready(self, url: str, timeout_s: int = 60, poll_interval_s: float = 1.0) -> dict:
        start = time.monotonic()
        last_error = ""
        while time.monotonic() - start < timeout_s:
            try:
                resp = requests.get(url, timeout=5)
                return {"ok": True, "status_code": resp.status_code, "waited_s": round(time.monotonic() - start, 1)}
            except requests.RequestException as e:
                last_error = str(e)
                time.sleep(poll_interval_s)
        return {"ok": False, "error": f"server not ready after {timeout_s}s: {last_error}"}

    def stop_process(self, name: str, timeout_s: int = 10) -> dict:
        mp = self._processes.get(name)
        if not mp:
            return {"ok": False, "error": f"no such process: {name}"}
        if not mp.is_running():
            del self._processes[name]
            return {"ok": True, "already_stopped": True}

        try:
            os.killpg(os.getpgid(mp.proc.pid), signal.SIGTERM)
            try:
                mp.proc.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(mp.proc.pid), signal.SIGKILL)
                mp.proc.wait(timeout=5)
        except ProcessLookupError:
            pass  # already gone
        del self._processes[name]
        return {"ok": True}

    def stop_all(self) -> None:
        """Cleanup hook — must be called at end of every iteration and on
        crash/interrupt, so a dev server never survives past its iteration."""
        for name in list(self._processes.keys()):
            self.stop_process(name)

    def tail_logs(self, name: str, max_chars: int = 5000) -> dict:
        mp = self._processes.get(name)
        if not mp:
            return {"ok": False, "error": f"no such process: {name}"}
        stdout = mp.stdout_path.read_text(encoding="utf-8", errors="replace")[-max_chars:]
        stderr = mp.stderr_path.read_text(encoding="utf-8", errors="replace")[-max_chars:]
        return {"ok": True, "stdout": stdout, "stderr": stderr, "running": mp.is_running(), "exit_code": mp.exit_code()}
