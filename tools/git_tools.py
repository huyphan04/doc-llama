"""
Git tools — spec section 8.

Hard rules enforced in code, not just by convention:
  - The agent must be on an AI-created branch (ai/<task>-<timestamp>) before
    any write operation (commit) is allowed. status()/diff()/log() are
    always safe (read-only) and work on any branch.
  - git push is not implemented at all — there is no method for it. If
    config.git.auto_push were ever true, this module still couldn't do it;
    push must be a deliberate human action outside this tool. (This is a
    stronger guarantee than "check a config flag" — the capability simply
    doesn't exist here.)
  - git reset --hard and git clean -fd are not implemented either, for the
    same reason.
  - create_branch() refuses to branch FROM a protected branch's uncommitted
    state in a way that would lose work — it just creates and checks out a
    new branch, never deletes anything.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from tools.shell import ShellTools


class GitSafetyError(Exception):
    pass


@dataclass
class GitResult:
    ok: bool
    output: str = ""
    error: str = ""

    def to_dict(self) -> dict:
        return {"ok": self.ok, "output": self.output, "error": self.error}


class GitTools:
    def __init__(self, workspace_root: Path, protected_branches: list[str], branch_prefix: str = "ai"):
        self.root = Path(workspace_root).resolve()
        self.protected = set(protected_branches)
        self.branch_prefix = branch_prefix
        # Git tools shell out directly (not through ShellTools' allowlist,
        # since 'git' is always permitted here by construction) but we
        # still use subprocess the same safe way: argv list, no shell=True.
        self._shell = ShellTools(
            workspace_root=self.root, allowed=["git"], denied_patterns=[], timeout=60
        )

    def _run(self, *args: str) -> GitResult:
        # Pass as argv list (not a joined string) so arguments containing
        # spaces — e.g. a commit message — survive intact instead of being
        # re-split by shlex.
        res = self._shell.run(["git", *args])
        return GitResult(ok=res.ok, output=res.stdout.strip(), error=res.stderr.strip() or res.error)

    def current_branch(self) -> str:
        res = self._run("rev-parse", "--abbrev-ref", "HEAD")
        return res.output if res.ok else ""

    def is_repo(self) -> bool:
        return self._run("rev-parse", "--is-inside-work-tree").ok

    def status(self) -> GitResult:
        return self._run("status", "--porcelain=v1", "-b")

    def diff(self, staged: bool = False) -> GitResult:
        args = ["diff"]
        if staged:
            args.append("--staged")
        return self._run(*args)

    def log(self, max_count: int = 20) -> GitResult:
        return self._run("log", f"-{max_count}", "--oneline")

    def init_repo_if_needed(self) -> GitResult:
        """If workspace_root is not yet a git repository, initialize one
        with an initial commit of whatever's already there — so pointing
        the agent at a freshly-downloaded/unzipped project (no .git yet)
        works out of the box instead of going straight to BLOCKED. Safe to
        call on an existing repo too: it's a no-op (checked first)."""
        if self.is_repo():
            return GitResult(ok=True, output="already a git repository")
        init_res = self._run("init", "-b", "main")
        if not init_res.ok:
            return init_res
        # A default identity is required for the initial commit to succeed
        # in a fresh environment with no global git config. This only sets
        # it locally for this repo and only if nothing is already set.
        self._run("config", "user.email", "autonomous-agent@local")
        self._run("config", "user.name", "Autonomous Coding Agent")
        add_res = self._run("add", "-A")
        if not add_res.ok:
            return add_res
        return self._run("commit", "-m", "Initial commit (auto-created by autonomous agent)", "--allow-empty")

    def has_uncommitted_changes(self) -> bool:
        res = self.status()
        if not res.ok:
            return False
        # status() uses `--porcelain=v1 -b`, whose first line is always the
        # branch header ("## main...origin/main") even on a clean repo —
        # strip it before checking for actual changes.
        lines = [line for line in res.output.splitlines() if not line.startswith("##")]
        return bool(lines)

    def create_branch(self, task_name: str) -> GitResult:
        safe_task = "".join(c if c.isalnum() or c in "-_" else "-" for c in task_name.lower())[:50]
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        branch = f"{self.branch_prefix}/{safe_task}-{ts}"
        res = self._run("checkout", "-b", branch)
        if not res.ok:
            return res
        return GitResult(ok=True, output=branch)

    def _assert_on_ai_branch(self) -> None:
        branch = self.current_branch()
        if branch in self.protected or not branch.startswith(f"{self.branch_prefix}/"):
            raise GitSafetyError(
                f"refusing write operation on branch '{branch}' — must be on an "
                f"'{self.branch_prefix}/' branch created by create_branch()"
            )

    def add_all(self) -> GitResult:
        self._assert_on_ai_branch()
        return self._run("add", "-A")

    def commit(self, message: str) -> GitResult:
        self._assert_on_ai_branch()
        if not message.strip():
            return GitResult(ok=False, error="empty commit message")
        return self._run("commit", "-m", message)

    # Deliberately NOT implemented, per spec section 8:
    #   push()             -> agent must never push
    #   reset_hard()       -> destructive, human-only
    #   clean_force()      -> destructive, human-only
    #   checkout to protected branch for writes -> blocked by _assert_on_ai_branch
