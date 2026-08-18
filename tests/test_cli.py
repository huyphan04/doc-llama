import json

import pytest

from cli.main import build_parser, cmd_doctor, cmd_report, cmd_status
from cli.run_state import RunState, list_all_states, read_state, write_state


@pytest.fixture
def cli_config(tmp_path):
    config_text = f"""
llm:
  provider: ollama
  base_url: http://localhost:19999
  coding_model: fake-model
agent:
  max_iterations: 5
workspace:
  root: {tmp_path / "ws"}
browser:
  enabled: true
  viewports:
    - name: mobile
      width: 375
      height: 812
shell:
  timeout: 30
  allowed: [npm, git]
git:
  protected_branches: [main, master]
logging:
  dir: {tmp_path / "logs"}
reports:
  dir: {tmp_path / "reports"}
"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_text)
    return config_path


def test_parser_builds_all_subcommands():
    parser = build_parser()
    args = parser.parse_args(["run", "tasks/foo.md"])
    assert args.command == "run"
    assert args.task_file == "tasks/foo.md"

    args = parser.parse_args(["status"])
    assert args.command == "status"

    args = parser.parse_args(["doctor"])
    assert args.command == "doctor"

    args = parser.parse_args(["report", "task-1"])
    assert args.task_id == "task-1"


def test_run_state_roundtrip(tmp_path):
    logs_dir = tmp_path / "logs"
    state = RunState(task_id="t1", status="RUNNING", started_at=100.0, updated_at=0, pid=1234)
    write_state(logs_dir, state)
    loaded = read_state(logs_dir, "t1")
    assert loaded is not None
    assert loaded.status == "RUNNING"
    assert loaded.pid == 1234


def test_run_state_missing_returns_none(tmp_path):
    assert read_state(tmp_path / "logs", "nope") is None


def test_list_all_states(tmp_path):
    logs_dir = tmp_path / "logs"
    write_state(logs_dir, RunState(task_id="a", status="PASS", started_at=1, updated_at=0))
    write_state(logs_dir, RunState(task_id="b", status="BLOCKED", started_at=2, updated_at=0))
    states = list_all_states(logs_dir)
    assert {s.task_id for s in states} == {"a", "b"}


def test_cmd_status_no_runs(cli_config, capsys):
    parser = build_parser()
    args = parser.parse_args(["--config", str(cli_config), "status"])
    rc = cmd_status(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "No tasks have been run yet" in out


def test_cmd_report_missing(cli_config, capsys):
    parser = build_parser()
    args = parser.parse_args(["--config", str(cli_config), "report", "missing-task"])
    rc = cmd_report(args)
    assert rc == 1
    err = capsys.readouterr().err
    assert "No report found" in err


def test_cmd_doctor_reports_ollama_unreachable(cli_config, capsys):
    parser = build_parser()
    args = parser.parse_args(["--config", str(cli_config), "doctor"])
    rc = cmd_doctor(args)
    out = capsys.readouterr().out
    assert "ollama" in out.lower()
    # base_url points at a port nothing is listening on, so this must fail honestly
    assert rc == 1
