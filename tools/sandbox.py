"""
Filesystem sandbox — spec section 6.

This is the single most security-critical module in the codebase: every
filesystem tool (list_files, read_file, write_file, edit_file, delete_file)
must route every path through resolve_safe_path() before touching disk.
No other module should call Path.resolve() on user/LLM-supplied paths
directly.

Threats handled:
  - "../" traversal out of workspace root
  - absolute-path escape (e.g. LLM passes "/etc/passwd")
  - symlink escape (a symlink *inside* the workspace pointing outside it)
  - denied filename/dirname anywhere in the path (.env, .ssh, secrets, ...)

Symlink handling: we resolve the path fully (following symlinks) via
Path.resolve(strict=False), then check the *resolved* path is still under
workspace_root. strict=False so we can still validate paths for files that
don't exist yet (write_file creating a new file) — but note this means a
symlink whose target doesn't exist yet can't be caught here; the tools
that create files must never create a symlink themselves (they don't —
write_file/create_file only ever open files in 'w' mode, which does not
follow a pre-existing dangling symlink outside the sandbox because such a
symlink would itself have been rejected when it was created, since it too
must go through this sandbox).
"""
from __future__ import annotations

from pathlib import Path


class SandboxViolation(Exception):
    """Raised when a path would escape the configured workspace root."""


def resolve_safe_path(workspace_root: Path, requested: str, denied_names: list[str] | None = None) -> Path:
    denied_names = denied_names or []
    workspace_root = workspace_root.resolve()

    if not workspace_root.exists():
        workspace_root.mkdir(parents=True, exist_ok=True)

    requested_path = Path(requested)

    # Absolute paths from the LLM are only acceptable if they're already
    # inside the workspace; otherwise this is an explicit escape attempt.
    if requested_path.is_absolute():
        candidate = requested_path
    else:
        candidate = workspace_root / requested_path

    # Resolve symlinks + '..' components. strict=False allows resolving
    # paths to not-yet-created files (needed for write_file/create_file).
    resolved = candidate.resolve(strict=False)

    try:
        resolved.relative_to(workspace_root)
    except ValueError:
        raise SandboxViolation(
            f"path '{requested}' resolves to '{resolved}', which is outside "
            f"the workspace root '{workspace_root}'"
        )

    for part in resolved.relative_to(workspace_root).parts:
        if part in denied_names:
            raise SandboxViolation(f"path '{requested}' touches denied name '{part}'")

    return resolved
