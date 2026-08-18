"""
Filesystem tools — spec section 5 & 6.

Every tool here:
  - validates its path(s) through sandbox.resolve_safe_path
  - has a timeout-free but size-bounded contract (large files are truncated,
    never silently OOM the caller)
  - returns a ToolResult (never raises to the caller — errors are captured
    and returned as ok=False, per section 32 "do not hide errors": the
    caller still SEES the error, it's just not an uncaught exception)
  - is logged by the caller (agent/tool_executor.py), not here — this
    module has no logging side effects of its own, keeping it easy to
    unit test in isolation.
"""
from __future__ import annotations

import fnmatch
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tools.sandbox import SandboxViolation, resolve_safe_path


@dataclass
class ToolResult:
    ok: bool
    data: Any = None
    error: str = ""

    def to_dict(self) -> dict:
        return {"ok": self.ok, "data": self.data, "error": self.error}


class FilesystemTools:
    def __init__(self, workspace_root: Path, denied_names: list[str] | None = None, max_file_size_kb: int = 200):
        self.workspace_root = Path(workspace_root).resolve()
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.denied_names = denied_names or []
        self.max_file_size_kb = max_file_size_kb

    def _resolve(self, path: str) -> Path:
        return resolve_safe_path(self.workspace_root, path, self.denied_names)

    def list_files(self, path: str = ".", pattern: str | None = None, max_depth: int = 6) -> ToolResult:
        try:
            root = self._resolve(path)
            if not root.exists():
                return ToolResult(ok=False, error=f"path does not exist: {path}")
            if not root.is_dir():
                return ToolResult(ok=False, error=f"not a directory: {path}")

            results: list[str] = []
            base_depth = len(root.parts)
            skip_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}
            for p in sorted(root.rglob("*")):
                if any(part in skip_dirs for part in p.parts):
                    continue
                depth = len(p.parts) - base_depth
                if depth > max_depth:
                    continue
                rel = str(p.relative_to(self.workspace_root))
                if pattern and not fnmatch.fnmatch(p.name, pattern):
                    continue
                results.append(rel + ("/" if p.is_dir() else ""))
            return ToolResult(ok=True, data=results)
        except SandboxViolation as e:
            return ToolResult(ok=False, error=str(e))
        except OSError as e:
            return ToolResult(ok=False, error=f"OS error: {e}")

    def read_file(self, path: str, start_line: int | None = None, end_line: int | None = None) -> ToolResult:
        try:
            fp = self._resolve(path)
            if not fp.exists():
                return ToolResult(ok=False, error=f"file does not exist: {path}")
            if not fp.is_file():
                return ToolResult(ok=False, error=f"not a file: {path}")

            size_kb = fp.stat().st_size / 1024
            truncated = False
            if size_kb > self.max_file_size_kb:
                truncated = True

            try:
                text = fp.read_text(encoding="utf-8", errors="replace")
            except (UnicodeDecodeError, OSError) as e:
                return ToolResult(ok=False, error=f"cannot read as text: {e}")

            if start_line is not None or end_line is not None:
                lines = text.splitlines(keepends=True)
                s = (start_line or 1) - 1
                e = end_line if end_line is not None else len(lines)
                content = "".join(lines[max(s, 0):e])
            else:
                content = text

            if truncated:
                max_chars = self.max_file_size_kb * 1024
                content = content[:max_chars]

            return ToolResult(
                ok=True,
                data={"content": content, "truncated": truncated, "total_size_kb": round(size_kb, 1)},
            )
        except SandboxViolation as e:
            return ToolResult(ok=False, error=str(e))
        except OSError as e:
            return ToolResult(ok=False, error=f"OS error: {e}")

    def search_code(self, query: str, path: str = ".", file_glob: str = "*", max_results: int = 100) -> ToolResult:
        try:
            root = self._resolve(path)
            if not root.exists():
                return ToolResult(ok=False, error=f"path does not exist: {path}")

            skip_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}
            matches: list[dict] = []
            for p in sorted(root.rglob(file_glob)):
                if not p.is_file() or any(part in skip_dirs for part in p.parts):
                    continue
                try:
                    text = p.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                for i, line in enumerate(text.splitlines(), start=1):
                    if query in line:
                        matches.append(
                            {"file": str(p.relative_to(self.workspace_root)), "line": i, "text": line.strip()[:200]}
                        )
                        if len(matches) >= max_results:
                            return ToolResult(ok=True, data=matches)
            return ToolResult(ok=True, data=matches)
        except SandboxViolation as e:
            return ToolResult(ok=False, error=str(e))
        except OSError as e:
            return ToolResult(ok=False, error=f"OS error: {e}")

    def write_file(self, path: str, content: str, overwrite: bool = True) -> ToolResult:
        try:
            fp = self._resolve(path)
            if fp.exists() and not overwrite:
                return ToolResult(ok=False, error=f"file already exists and overwrite=False: {path}")
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(content, encoding="utf-8")
            return ToolResult(ok=True, data={"path": str(fp.relative_to(self.workspace_root)), "bytes": len(content)})
        except SandboxViolation as e:
            return ToolResult(ok=False, error=str(e))
        except OSError as e:
            return ToolResult(ok=False, error=f"OS error: {e}")

    def edit_file(self, path: str, old_text: str, new_text: str) -> ToolResult:
        """Exact string replacement, requires old_text to appear exactly once —
        same discipline as the str_replace tool this agent itself is built with,
        chosen deliberately so the Coder can't ambiguously clobber code."""
        try:
            fp = self._resolve(path)
            if not fp.exists():
                return ToolResult(ok=False, error=f"file does not exist: {path}")
            text = fp.read_text(encoding="utf-8")
            count = text.count(old_text)
            if count == 0:
                return ToolResult(ok=False, error="old_text not found in file")
            if count > 1:
                return ToolResult(ok=False, error=f"old_text is not unique ({count} occurrences) — widen context")
            new_full = text.replace(old_text, new_text, 1)
            fp.write_text(new_full, encoding="utf-8")
            return ToolResult(ok=True, data={"path": str(fp.relative_to(self.workspace_root))})
        except SandboxViolation as e:
            return ToolResult(ok=False, error=str(e))
        except OSError as e:
            return ToolResult(ok=False, error=f"OS error: {e}")

    def delete_file(self, path: str) -> ToolResult:
        try:
            fp = self._resolve(path)
            if not fp.exists():
                return ToolResult(ok=False, error=f"does not exist: {path}")
            if fp.is_dir():
                shutil.rmtree(fp)
            else:
                fp.unlink()
            return ToolResult(ok=True, data={"deleted": path})
        except SandboxViolation as e:
            return ToolResult(ok=False, error=str(e))
        except OSError as e:
            return ToolResult(ok=False, error=f"OS error: {e}")
