"""
`ai-agent doctor` — spec sections 23, 28.

Checks every external dependency the agent needs BEFORE a run starts, so
a failure surfaces as "Ollama not running" at the start of the night
rather than as a cryptic connection error at 2am mid-task. Every check
is independent — one failing check doesn't stop the others from running,
so the operator gets a complete picture in one pass.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from agent.config import Config
from agent.llm import build_llm_client


@dataclass
class DoctorCheck:
    name: str
    ok: bool
    detail: str


def _check_binary(name: str, version_args: list[str] | None = None) -> DoctorCheck:
    path = shutil.which(name)
    if not path:
        return DoctorCheck(name=name, ok=False, detail=f"'{name}' not found on PATH")
    if version_args:
        try:
            proc = subprocess.run([name, *version_args], capture_output=True, text=True, timeout=10)
            version = (proc.stdout or proc.stderr).strip().splitlines()[0] if (proc.stdout or proc.stderr) else "unknown version"
            return DoctorCheck(name=name, ok=True, detail=f"{path} ({version})")
        except (subprocess.SubprocessError, OSError) as e:
            return DoctorCheck(name=name, ok=False, detail=f"found at {path} but failed to run: {e}")
    return DoctorCheck(name=name, ok=True, detail=path)


def _check_playwright_browsers() -> DoctorCheck:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            browser.close()
        return DoctorCheck(name="playwright-chromium", ok=True, detail="chromium launches successfully")
    except ImportError:
        return DoctorCheck(name="playwright-chromium", ok=False, detail="playwright package not installed (pip install playwright)")
    except Exception as e:  # noqa: BLE001 — any launch failure means "not ready", report it
        return DoctorCheck(
            name="playwright-chromium", ok=False,
            detail=f"chromium failed to launch: {e}. Try: playwright install chromium",
        )


def _check_workspace(config: Config) -> DoctorCheck:
    root = config.workspace_root
    try:
        root.mkdir(parents=True, exist_ok=True)
        test_file = root / ".doctor_write_test"
        test_file.write_text("ok")
        test_file.unlink()
        return DoctorCheck(name="workspace", ok=True, detail=f"{root} exists and is writable")
    except OSError as e:
        return DoctorCheck(name="workspace", ok=False, detail=f"{root} is not writable: {e}")


def _check_ollama(config: Config) -> DoctorCheck:
    try:
        client = build_llm_client(config.llm)
    except Exception as e:  # noqa: BLE001
        return DoctorCheck(name="ollama", ok=False, detail=f"could not build LLM client: {e}")
    ok, msg = client.health_check()
    return DoctorCheck(name="ollama", ok=ok, detail=msg)


def run_doctor(config: Config) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    checks.append(_check_ollama(config))
    checks.append(_check_binary("node", ["--version"]))
    checks.append(_check_binary("npm", ["--version"]))
    checks.append(_check_binary("git", ["--version"]))
    checks.append(_check_binary("python3", ["--version"]))
    if "docker" in config.shell.allowed:
        checks.append(_check_binary("docker", ["--version"]))
    checks.append(_check_playwright_browsers())
    checks.append(_check_workspace(config))
    return checks


def format_doctor_report(checks: list[DoctorCheck]) -> str:
    lines = ["Environment check:", ""]
    for c in checks:
        symbol = "OK  " if c.ok else "FAIL"
        lines.append(f"  [{symbol}] {c.name}: {c.detail}")
    lines.append("")
    failed = [c for c in checks if not c.ok]
    if failed:
        lines.append(f"{len(failed)} check(s) failed. Fix these before running a task.")
    else:
        lines.append("All checks passed.")
    return "\n".join(lines)
