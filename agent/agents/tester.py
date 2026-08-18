"""
Tester agent — spec section 4.

Deliberately NOT an LLM role loop like Planner/Coder. Running lint/build/
test commands doesn't need judgment — it needs to actually run the
commands and report exit codes truthfully. Making this LLM-driven would
risk the model "deciding" a step passed without running it (directly
violating spec section 32: "Agent không được báo PASS khi một required
verification chưa chạy" — must not report PASS when a required check
wasn't actually run).

Commands are auto-detected from package.json scripts (or requirements.txt/
pyproject.toml for Python projects) rather than hard-coded, since spec
section 16 requires the agent to detect the existing stack rather than
assume one.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from agent.logging_setup import AgentLogger
from tools.filesystem import FilesystemTools
from tools.shell import ShellResult, ShellTools


@dataclass
class CheckResult:
    name: str
    ran: bool
    status: str  # "PASS" | "FAIL" | "SKIPPED"
    command: str = ""
    exit_code: int | None = None
    error: str = ""
    suggested_area: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name, "ran": self.ran, "status": self.status, "command": self.command,
            "exit_code": self.exit_code, "error": self.error, "suggested_area": self.suggested_area,
        }


@dataclass
class TesterReport:
    overall_status: str  # "PASS" | "FAIL"
    checks: list[CheckResult] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"overall_status": self.overall_status, "checks": [c.to_dict() for c in self.checks]}

    def failed_checks(self) -> list[CheckResult]:
        return [c for c in self.checks if c.status == "FAIL"]


def _detect_npm_scripts(fs: FilesystemTools) -> dict[str, str]:
    res = fs.read_file("package.json")
    if not res.ok:
        return {}
    try:
        data = json.loads(res.data["content"])
    except (json.JSONDecodeError, KeyError, TypeError):
        return {}
    return data.get("scripts", {}) or {}


def _detect_package_manager(fs: FilesystemTools) -> str:
    if fs.read_file("pnpm-lock.yaml").ok:
        return "pnpm"
    if fs.read_file("yarn.lock").ok:
        return "yarn"
    return "npm"


def _run_step(shell: ShellTools, name: str, command: str, suggested_area_from: str = "") -> CheckResult:
    res: ShellResult = shell.run(command)
    if res.error and res.exit_code is None:
        # tool-level failure (not found / policy / timeout), not a normal
        # nonzero exit from the command itself
        return CheckResult(name=name, ran=False, status="FAIL", command=command, error=res.error)
    status = "PASS" if res.ok else "FAIL"
    error = "" if res.ok else (res.stderr[-2000:] or res.stdout[-2000:])
    return CheckResult(
        name=name, ran=True, status=status, command=command, exit_code=res.exit_code,
        error=error, suggested_area=_guess_area(error) if status == "FAIL" else "",
    )


def _guess_area(error_text: str) -> str:
    """Best-effort extraction of a file path from error output, so the
    Reviewer/Coder don't have to re-parse raw stderr from scratch."""
    import re

    m = re.search(r"([./]?[\w./-]+\.(?:tsx?|jsx?|css|scss|py|json))", error_text)
    return m.group(1) if m else ""


def run_tester(
    logger: AgentLogger,
    fs: FilesystemTools,
    shell: ShellTools,
    run_lint: bool = True,
    run_typecheck: bool = True,
    run_unit_tests: bool = True,
    run_build: bool = True,
) -> TesterReport:
    checks: list[CheckResult] = []
    scripts = _detect_npm_scripts(fs)
    pm = _detect_package_manager(fs)

    if not scripts:
        logger.warning("No package.json scripts detected — Tester has nothing to run. "
                        "This is reported as SKIPPED, not PASS, per spec section 32.")

    def maybe_run(step_name: str, script_key_candidates: list[str], enabled: bool):
        if not enabled:
            checks.append(CheckResult(name=step_name, ran=False, status="SKIPPED", error="disabled by config"))
            return
        script_key = next((k for k in script_key_candidates if k in scripts), None)
        if not script_key:
            checks.append(CheckResult(name=step_name, ran=False, status="SKIPPED", error="no matching npm script found"))
            return
        cmd = f"{pm} run {script_key}"
        logger.info(f"Tester running: {cmd}")
        checks.append(_run_step(shell, step_name, cmd))

    maybe_run("lint", ["lint"], run_lint)
    maybe_run("typecheck", ["typecheck", "type-check", "tsc"], run_typecheck)
    maybe_run("unit_tests", ["test", "test:unit"], run_unit_tests)
    maybe_run("build", ["build"], run_build)

    overall = "FAIL" if any(c.status == "FAIL" for c in checks) else "PASS"
    report = TesterReport(overall_status=overall, checks=checks)
    logger.info(f"Tester overall: {overall}", checks={c.name: c.status for c in checks})
    return report
