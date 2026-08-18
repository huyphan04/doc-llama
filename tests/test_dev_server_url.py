from agent.config import from_dict
from agent.orchestrator import Orchestrator
from agent.task_parser import parse_task_text
from tests.fake_llm import ScriptedLLMClient


def _minimal_config(**agent_overrides):
    raw = {
        "llm": {"provider": "ollama", "coding_model": "fake"},
        "agent": {"max_iterations": 5, **agent_overrides},
        "workspace": {"root": "/tmp/does-not-need-to-exist-for-this-test"},
        "browser": {"enabled": True, "viewports": [{"name": "mobile", "width": 375, "height": 812}]},
        "shell": {"allowed": ["git"]},
    }
    return from_dict(raw)


def test_dev_server_url_defaults_to_config_value():
    cfg = _minimal_config(dev_server_url="http://localhost:4000")
    task = parse_task_text("# Task\nx\n", task_id="t1")
    orch = Orchestrator.__new__(Orchestrator)  # avoid running __init__'s git/logging setup
    resolved = orch._resolve_dev_server_url(cfg, task)
    assert resolved == "http://localhost:4000"


def test_dev_server_url_falls_back_to_builtin_default():
    cfg = _minimal_config()
    task = parse_task_text("# Task\nx\n", task_id="t2")
    orch = Orchestrator.__new__(Orchestrator)
    resolved = orch._resolve_dev_server_url(cfg, task)
    assert resolved == "http://localhost:3000"


def test_dev_server_url_task_override_wins_over_config():
    cfg = _minimal_config(dev_server_url="http://localhost:4000")
    task = parse_task_text(
        "# Task\nx\n\n## Dev Server\n\nhttp://localhost:8080\n", task_id="t3"
    )
    orch = Orchestrator.__new__(Orchestrator)
    resolved = orch._resolve_dev_server_url(cfg, task)
    assert resolved == "http://localhost:8080"
