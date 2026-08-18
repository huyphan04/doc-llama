"""
ToolExecutor — the single point where an LLM tool_call name+arguments
becomes an actual action, and where every call gets logged (spec section
20: "all activity must be logged") and never raises past this boundary
(spec section 32: "do not hide errors" means capture+report, not crash).

This module owns the ToolSpec list handed to LLMClient.chat(tools=...),
so the schema the model sees and the actual dispatch table can't drift
apart — they're generated from the same TOOL_REGISTRY.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from agent.config import Config
from agent.llm.base import ToolSpec
from agent.logging_setup import IterationLogger
from tools.browser import BrowserTools
from tools.filesystem import FilesystemTools
from tools.git_tools import GitSafetyError, GitTools
from tools.process_manager import ProcessManager
from tools.shell import ShellTools


@dataclass
class ExecResult:
    ok: bool
    data: Any = None
    error: str = ""

    def to_dict(self) -> dict:
        return {"ok": self.ok, "data": self.data, "error": self.error}


TOOL_SPECS: list[ToolSpec] = [
    ToolSpec("list_files", "List files under a workspace-relative path.",
             {"type": "object", "properties": {"path": {"type": "string"}, "pattern": {"type": "string"}}, "required": []}),
    ToolSpec("read_file", "Read a file's contents (optionally a line range).",
             {"type": "object", "properties": {"path": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}}, "required": ["path"]}),
    ToolSpec("search_code", "Search for a literal substring across files.",
             {"type": "object", "properties": {"query": {"type": "string"}, "path": {"type": "string"}, "file_glob": {"type": "string"}}, "required": ["query"]}),
    ToolSpec("write_file", "Create or overwrite a file with new content.",
             {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}),
    ToolSpec("edit_file", "Replace an exact, unique substring within a file.",
             {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]}),
    ToolSpec("delete_file", "Delete a file or directory.",
             {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}),
    ToolSpec("run_command", "Run an allow-listed shell command inside the workspace.",
             {"type": "object", "properties": {"command": {"type": "string"}, "cwd": {"type": "string"}, "timeout": {"type": "integer"}}, "required": ["command"]}),
    ToolSpec("git_status", "Show git status of the workspace repo.", {"type": "object", "properties": {}}),
    ToolSpec("git_diff", "Show unstaged git diff.", {"type": "object", "properties": {"staged": {"type": "boolean"}}}),
    ToolSpec("git_log", "Show recent git log.", {"type": "object", "properties": {"max_count": {"type": "integer"}}}),
    ToolSpec("start_process", "Start a long-lived background process (e.g. dev server).",
             {"type": "object", "properties": {"name": {"type": "string"}, "command": {"type": "array", "items": {"type": "string"}}, "cwd": {"type": "string"}}, "required": ["name", "command"]}),
    ToolSpec("stop_process", "Stop a previously started background process.",
             {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}),
    ToolSpec("wait_until_ready", "Poll a URL until it responds or timeout.",
             {"type": "object", "properties": {"url": {"type": "string"}, "timeout_s": {"type": "integer"}}, "required": ["url"]}),
    ToolSpec("browser_check", "Load a URL in a real browser at a given viewport; capture console/network errors, overflow, and a screenshot.",
             {"type": "object", "properties": {"url": {"type": "string"}, "viewport_name": {"type": "string"}, "width": {"type": "integer"}, "height": {"type": "integer"}}, "required": ["url", "viewport_name", "width", "height"]}),
]


class ToolExecutor:
    def __init__(self, config: Config, iteration_logger: IterationLogger):
        self.config = config
        self.log = iteration_logger
        self.fs = FilesystemTools(
            workspace_root=config.workspace_root,
            denied_names=config.workspace.denied_names,
            max_file_size_kb=config.llm_context.max_file_size_kb,
        )
        self.shell = ShellTools(
            workspace_root=config.workspace_root,
            allowed=config.shell.allowed,
            denied_patterns=config.shell.denied_patterns,
            timeout=config.shell.timeout,
        )
        self.git = GitTools(
            workspace_root=config.workspace_root,
            protected_branches=config.git.protected_branches,
            branch_prefix=config.git.branch_prefix,
        )
        self.processes = ProcessManager(
            workspace_root=config.workspace_root, log_dir=config.resolve_path(config.logging.dir) / "processes"
        )
        self.browser = BrowserTools(
            headless=config.browser.headless,
            navigation_timeout_ms=config.browser.navigation_timeout_ms,
            wait_for_network_idle_ms=config.browser.wait_for_network_idle_ms,
        )

        self._dispatch: dict[str, Callable[..., Any]] = {
            "list_files": lambda **kw: self.fs.list_files(**kw),
            "read_file": lambda **kw: self.fs.read_file(**kw),
            "search_code": lambda **kw: self.fs.search_code(**kw),
            "write_file": lambda **kw: self.fs.write_file(**kw),
            "edit_file": lambda **kw: self.fs.edit_file(**kw),
            "delete_file": lambda **kw: self.fs.delete_file(**kw),
            "run_command": lambda **kw: self.shell.run(**kw),
            "git_status": lambda **kw: self.git.status(),
            "git_diff": lambda **kw: self.git.diff(**kw),
            "git_log": lambda **kw: self.git.log(**kw),
            "start_process": lambda **kw: self.processes.start_process(**kw),
            "stop_process": lambda **kw: self.processes.stop_process(**kw),
            "wait_until_ready": lambda **kw: self.processes.wait_until_ready(**kw),
            "browser_check": self._browser_check,
        }

    def _browser_check(self, url: str, viewport_name: str, width: int, height: int) -> Any:
        screenshot_dir = self.log.dir / "screenshots"
        return self.browser.check_viewport(url, viewport_name, width, height, screenshot_dir)

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> ExecResult:
        fn = self._dispatch.get(tool_name)
        if fn is None:
            result = ExecResult(ok=False, error=f"unknown tool: {tool_name}")
            self.log.log_tool_call(tool_name, arguments, result.to_dict(), ok=False, duration_s=0.0)
            return result

        start = time.monotonic()
        try:
            raw = fn(**arguments)
        except GitSafetyError as e:
            result = ExecResult(ok=False, error=f"git safety violation: {e}")
            self.log.log_tool_call(tool_name, arguments, result.to_dict(), ok=False, duration_s=time.monotonic() - start)
            return result
        except TypeError as e:
            result = ExecResult(ok=False, error=f"invalid arguments for {tool_name}: {e}")
            self.log.log_tool_call(tool_name, arguments, result.to_dict(), ok=False, duration_s=time.monotonic() - start)
            return result
        except Exception as e:  # noqa: BLE001 — tool boundary: never let an unexpected error crash the loop
            result = ExecResult(ok=False, error=f"unexpected error in {tool_name}: {type(e).__name__}: {e}")
            self.log.log_tool_call(tool_name, arguments, result.to_dict(), ok=False, duration_s=time.monotonic() - start)
            return result

        duration = time.monotonic() - start

        # Normalize the various tool return shapes (ToolResult, GitResult,
        # ShellResult, dataclass, dict) into a single ExecResult contract.
        if hasattr(raw, "to_dict"):
            d = raw.to_dict()
            result = ExecResult(ok=d.get("ok", False), data=d, error=d.get("error", ""))
        elif isinstance(raw, dict):
            result = ExecResult(ok=raw.get("ok", False), data=raw, error=raw.get("error", ""))
        else:
            result = ExecResult(ok=True, data=raw)

        self.log.log_tool_call(tool_name, arguments, result.to_dict(), ok=result.ok, duration_s=duration)
        return result

    def cleanup(self) -> None:
        """Must be called at the end of every iteration — kills any dev
        server left running so it never survives past its iteration."""
        self.processes.stop_all()
