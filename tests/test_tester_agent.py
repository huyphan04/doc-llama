import json

import pytest

from agent.agents.tester import run_tester
from agent.logging_setup import AgentLogger
from tools.filesystem import FilesystemTools
from tools.shell import ShellTools


@pytest.fixture
def node_project(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    pkg = {
        "name": "demo",
        "scripts": {
            "lint": "python3 -c \"import sys; sys.exit(0)\"",
            "build": "python3 -c \"import sys; sys.exit(0)\"",
            "test": "python3 -c \"import sys; sys.exit(1)\"",  # fails on purpose
        },
    }
    (ws / "package.json").write_text(json.dumps(pkg))
    return ws


@pytest.fixture
def logger(tmp_path):
    return AgentLogger(log_dir=tmp_path / "logs", json_log=True)


def test_detects_and_runs_scripts(node_project, logger):
    fs = FilesystemTools(workspace_root=node_project)
    shell = ShellTools(workspace_root=node_project, allowed=["npm", "python3"], denied_patterns=[], timeout=30)
    report = run_tester(logger, fs, shell)
    names = {c.name: c.status for c in report.checks}
    assert names["lint"] == "PASS"
    assert names["build"] == "PASS"
    assert names["unit_tests"] == "FAIL"
    assert report.overall_status == "FAIL"


def test_missing_script_is_skipped_not_passed(tmp_path, logger):
    ws = tmp_path / "ws2"
    ws.mkdir()
    (ws / "package.json").write_text(json.dumps({"name": "x", "scripts": {"build": "echo ok"}}))
    fs = FilesystemTools(workspace_root=ws)
    shell = ShellTools(workspace_root=ws, allowed=["npm", "echo"], denied_patterns=[], timeout=30)
    report = run_tester(logger, fs, shell)
    lint_check = next(c for c in report.checks if c.name == "lint")
    assert lint_check.status == "SKIPPED"
    assert lint_check.ran is False


def test_no_package_json_all_skipped(tmp_path, logger):
    ws = tmp_path / "ws3"
    ws.mkdir()
    fs = FilesystemTools(workspace_root=ws)
    shell = ShellTools(workspace_root=ws, allowed=["npm"], denied_patterns=[], timeout=30)
    report = run_tester(logger, fs, shell)
    assert all(c.status == "SKIPPED" for c in report.checks)


def test_disabled_check_reported_skipped(node_project, logger):
    fs = FilesystemTools(workspace_root=node_project)
    shell = ShellTools(workspace_root=node_project, allowed=["npm", "python3"], denied_patterns=[], timeout=30)
    report = run_tester(logger, fs, shell, run_unit_tests=False)
    unit_check = next(c for c in report.checks if c.name == "unit_tests")
    assert unit_check.status == "SKIPPED"
    assert unit_check.error == "disabled by config"
