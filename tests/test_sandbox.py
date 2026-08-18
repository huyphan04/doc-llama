import os

import pytest

from tools.sandbox import SandboxViolation, resolve_safe_path


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


def test_normal_relative_path_ok(workspace):
    p = resolve_safe_path(workspace, "src/app.py")
    assert p == (workspace / "src" / "app.py").resolve()


def test_dotdot_traversal_blocked(workspace):
    with pytest.raises(SandboxViolation):
        resolve_safe_path(workspace, "../../etc/passwd")


def test_absolute_path_outside_blocked(workspace):
    with pytest.raises(SandboxViolation):
        resolve_safe_path(workspace, "/etc/passwd")


def test_absolute_path_inside_workspace_ok(workspace):
    target = str(workspace / "file.txt")
    p = resolve_safe_path(workspace, target)
    assert p == (workspace / "file.txt").resolve()


def test_denied_name_blocked(workspace):
    with pytest.raises(SandboxViolation):
        resolve_safe_path(workspace, ".env", denied_names=[".env"])


def test_denied_name_nested_blocked(workspace):
    with pytest.raises(SandboxViolation):
        resolve_safe_path(workspace, "config/secrets/keys.txt", denied_names=["secrets"])


def test_symlink_escape_blocked(workspace):
    outside = workspace.parent / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("hidden")
    link = workspace / "escape_link"
    os.symlink(outside, link)
    with pytest.raises(SandboxViolation):
        resolve_safe_path(workspace, "escape_link/secret.txt")


def test_nonexistent_file_still_validated(workspace):
    # write_file needs to validate paths for files that don't exist yet
    p = resolve_safe_path(workspace, "new_dir/new_file.txt")
    assert not p.exists()
    assert p.parent.parent == workspace
