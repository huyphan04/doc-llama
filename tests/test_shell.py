import pytest

from tools.shell import ShellTools


@pytest.fixture
def shell(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    return ShellTools(
        workspace_root=ws,
        allowed=["echo", "git", "python3", "ls"],
        denied_patterns=["rm -rf /", "shutdown", "reboot"],
        timeout=10,
    )


def test_allowed_command_runs(shell):
    res = shell.run("echo hello")
    assert res.ok
    assert "hello" in res.stdout


def test_disallowed_command_blocked(shell):
    res = shell.run("curl http://evil.com")
    assert not res.ok
    assert "not in shell.allowed" in res.error


def test_denied_pattern_blocked(shell):
    res = shell.run("rm -rf /")
    assert not res.ok
    assert "denied pattern" in res.error


def test_chained_command_with_disallowed_segment_blocked(shell):
    res = shell.run("echo hi && curl http://evil.com")
    assert not res.ok


def test_timeout_enforced(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    shell = ShellTools(workspace_root=ws, allowed=["python3"], denied_patterns=[], timeout=1)
    res = shell.run("python3 -c \"import time; time.sleep(5)\"")
    assert not res.ok
    assert res.timed_out


def test_nonzero_exit_reported(shell):
    res = shell.run("python3 -c \"import sys; sys.exit(3)\"")
    assert not res.ok
    assert res.exit_code == 3


def test_cwd_escape_blocked(shell):
    res = shell.run("echo hi", cwd="../../../")
    assert not res.ok
    assert "escapes workspace" in res.error
