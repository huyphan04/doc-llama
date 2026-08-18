import pytest

from agent.config import ConfigError, from_dict


def test_missing_coding_model_raises():
    with pytest.raises(ConfigError, match="coding_model"):
        from_dict({"llm": {"provider": "ollama"}})


def test_unknown_key_raises():
    with pytest.raises(ConfigError, match="unknown config keys"):
        from_dict({"llm": {"coding_model": "x", "bogus_field": 1}})


def test_browser_enabled_requires_viewports():
    with pytest.raises(ConfigError, match="viewports"):
        from_dict({"llm": {"coding_model": "x"}, "browser": {"enabled": True, "viewports": []}})


def test_valid_config_loads():
    cfg = from_dict(
        {
            "llm": {"coding_model": "qwen2.5-coder:7b", "vision_model": "qwen2.5-vl:7b"},
            "browser": {"enabled": True, "viewports": [{"name": "mobile", "width": 375, "height": 812}]},
        }
    )
    assert cfg.llm.coding_model == "qwen2.5-coder:7b"
    assert cfg.browser.viewports[0].width == 375
    assert cfg.agent.max_iterations == 30  # default preserved


def test_full_config_yaml_loads():
    from agent.config import load

    cfg = load("config.yaml")
    assert cfg.llm.provider == "ollama"
    assert len(cfg.browser.viewports) == 3
    assert cfg.git.auto_push is False
