"""
Shell execution — spec section 7.

Policy model: the FIRST TOKEN of the command must be in `allowed`. This is
intentionally simple and conservative — we do not try to parse full shell
grammar to allow "npm" but block "npm && rm -rf /". Instead:
  1. The command is checked against denied_patterns as a raw substring match
     (catches the dangerous compound cases directly, e.g. "rm -rf /").
  2. Then split with shlex; the first token's basename must be in `allowed`.
  3. We run via subprocess with shell=False by default (a list of argv),
     which prevents shell metacharacter tricks (;, &&, |, backticks) from
     chaining an unapproved command onto an approved one. If the caller
     truly needs shell features (pipes), they must pass shell=True
     explicitly AND the resulting joined string must still pass the
     denied_patterns scan — chaining onto a non-allowed binary via `&&`
     when shell=True is still blocked because we scan for `&&`, `;`, `|`
     in the raw string when shell=True and require every segment's first
     token to also be allowed.

This is deliberately not bulletproof against a determined adversarial
input — the honest security boundary is "workspace root sandboxing +
no destructive absolute-path commands", not "provably safe shell". A
model trying to actively attack its own sandbox is out of scope; the goal
is to prevent an LLM from *accidentally* running something destructive
during normal agentic exploration.
"""
from __future__ import annotations

import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


class ShellPolicyViolation(Exception):
    pass


@dataclass
class ShellResult:
    ok: bool
    exit_code: int | None
    stdout: str
    stderr: str
    duration_s: float
    command: str
    timed_out: bool = False
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_s": round(self.duration_s, 2),
            "command": self.command,
            "timed_out": self.timed_out,
            "error": self.error,
        }


class ShellTools:
    def __init__(self, workspace_root: Path, allowed: list[str], denied_patterns: list[str], timeout: int = 300):
        self.workspace_root = Path(workspace_root).resolve()
        self.allowed = set(allowed)
        self.denied_patterns = denied_patterns
        self.default_timeout = timeout

    def _check_policy(self, command: str) -> None:
        lowered = command.lower()
        for pattern in self.denied_patterns:
            if pattern.lower() in lowered:
                raise ShellPolicyViolation(f"command matches denied pattern: {pattern!r}")

        # Use shlex with punctuation_chars so &&, ||, ;, | are tokenized as
        # standalone operators ONLY when they appear unquoted — a ';' or '|'
        # inside a quoted argument (e.g. python3 -c "import time; ...") is
        # correctly kept as part of that argument's text, not treated as a
        # chain operator. This fixes the earlier naive str.split() approach,
        # which broke on any quoted arg containing those characters.
        try:
            lexer = shlex.shlex(command, posix=True, punctuation_chars="&|;")
            lexer.whitespace_split = True
            raw_tokens = list(lexer)
        except ValueError as e:
            raise ShellPolicyViolation(f"could not parse command for policy check: {e}")

        segments: list[list[str]] = [[]]
        chain_ops = {"&&", "||", ";", "|", "&"}
        for tok in raw_tokens:
            if tok in chain_ops:
                segments.append([])
            else:
                segments[-1].append(tok)

        for seg_tokens in segments:
            if not seg_tokens:
                continue
            first = Path(seg_tokens[0]).name
            if first not in self.allowed:
                raise ShellPolicyViolation(
                    f"command '{first}' is not in shell.allowed ({sorted(self.allowed)})"
                )

    def _check_policy_tokens(self, tokens: list[str]) -> None:
        joined = " ".join(tokens)
        lowered = joined.lower()
        for pattern in self.denied_patterns:
            if pattern.lower() in lowered:
                raise ShellPolicyViolation(f"command matches denied pattern: {pattern!r}")
        if not tokens:
            return
        first = Path(tokens[0]).name
        if first not in self.allowed:
            raise ShellPolicyViolation(f"command '{first}' is not in shell.allowed ({sorted(self.allowed)})")

    def run(self, command: str | list[str], cwd: str | None = None, timeout: int | None = None, max_output_chars: int = 20000) -> ShellResult:
        """Accepts either a shell-style string (parsed with shlex — best
        effort, may mis-split complex quoting) or an argv list (exact,
        preferred whenever the caller already has discrete arguments, e.g.
        git_tools building `git commit -m <message>` — this avoids a whole
        class of quoting bugs where an argument containing spaces gets
        re-split incorrectly)."""
        timeout = timeout or self.default_timeout
        work_dir = self.workspace_root
        if cwd:
            candidate = (self.workspace_root / cwd).resolve()
            try:
                candidate.relative_to(self.workspace_root)
            except ValueError:
                return ShellResult(
                    ok=False, exit_code=None, stdout="", stderr="", duration_s=0.0,
                    command=str(command), error=f"cwd '{cwd}' escapes workspace root",
                )
            work_dir = candidate

        command_str = command if isinstance(command, str) else " ".join(shlex.quote(t) for t in command)

        # For list-form commands we can check policy on tokens directly
        # (avoids re-parsing our own shlex.quote output).
        try:
            if isinstance(command, list):
                self._check_policy_tokens(command)
            else:
                self._check_policy(command_str)
        except ShellPolicyViolation as e:
            return ShellResult(
                ok=False, exit_code=None, stdout="", stderr="", duration_s=0.0,
                command=command_str, error=str(e),
            )

        if isinstance(command, list):
            tokens = command
        else:
            try:
                tokens = shlex.split(command)
            except ValueError as e:
                return ShellResult(
                    ok=False, exit_code=None, stdout="", stderr="", duration_s=0.0,
                    command=command_str, error=f"could not parse command: {e}",
                )
        start = time.monotonic()
        try:
            proc = subprocess.run(
                tokens,
                cwd=str(work_dir),
                shell=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            duration = time.monotonic() - start
            stdout = proc.stdout[:max_output_chars]
            stderr = proc.stderr[:max_output_chars]
            return ShellResult(
                ok=proc.returncode == 0,
                exit_code=proc.returncode,
                stdout=stdout,
                stderr=stderr,
                duration_s=duration,
                command=command,
            )
        except subprocess.TimeoutExpired as e:
            duration = time.monotonic() - start
            return ShellResult(
                ok=False, exit_code=None,
                stdout=(e.stdout or "")[:max_output_chars] if isinstance(e.stdout, str) else "",
                stderr=(e.stderr or "")[:max_output_chars] if isinstance(e.stderr, str) else "",
                duration_s=duration, command=command_str, timed_out=True,
                error=f"command timed out after {timeout}s",
            )
        except FileNotFoundError as e:
            return ShellResult(
                ok=False, exit_code=None, stdout="", stderr="", duration_s=time.monotonic() - start,
                command=command_str, error=f"executable not found: {e}",
            )
        except OSError as e:
            return ShellResult(
                ok=False, exit_code=None, stdout="", stderr="", duration_s=time.monotonic() - start,
                command=command_str, error=f"OS error launching process: {e}",
            )



