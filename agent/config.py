"""
Typed configuration loading.

Design note: everything the spec asks to be configurable (model names,
viewports, iteration limits, shell policy, git policy) lives in config.yaml
and is loaded into typed dataclasses here. Nothing downstream should read
config.yaml directly — they take a Config object. This makes the whole
system testable without touching disk (tests build a Config() in memory).
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ConfigError(Exception):
    """Raised when config.yaml is missing required fields or malformed."""


@dataclass
class LLMConfig:
    provider: str = "ollama"
    base_url: str = "http://localhost:11434"
    coding_model: str = ""
    vision_model: str = ""
    request_timeout_seconds: int = 300
    max_retries: int = 3
    retry_backoff_seconds: int = 2


@dataclass
class AgentConfig:
    max_iterations: int = 30
    auto_commit: bool = False
    auto_push: bool = False
    repeated_failure_threshold: int = 2
    max_strategy_changes: int = 4
    dev_server_url: str = "http://localhost:3000"


@dataclass
class WorkspaceConfig:
    root: str = "./workspace"
    denied_names: list[str] = field(default_factory=lambda: [".env", ".ssh", "secrets", "credentials"])


@dataclass
class LLMContextConfig:
    max_context_files: int = 30
    max_file_size_kb: int = 200


@dataclass
class Viewport:
    name: str
    width: int
    height: int


@dataclass
class BrowserConfig:
    enabled: bool = True
    headless: bool = True
    wait_for_network_idle_ms: int = 3000
    navigation_timeout_ms: int = 30000
    viewports: list[Viewport] = field(default_factory=list)


@dataclass
class ShellConfig:
    timeout: int = 300
    allowed: list[str] = field(default_factory=list)
    denied_patterns: list[str] = field(default_factory=list)


@dataclass
class GitConfig:
    create_branch: bool = True
    branch_prefix: str = "ai"
    auto_commit: bool = False
    auto_push: bool = False
    protected_branches: list[str] = field(default_factory=lambda: ["main", "master"])


@dataclass
class LoggingConfig:
    dir: str = "./logs"
    level: str = "INFO"
    json_log: bool = True


@dataclass
class ReportsConfig:
    dir: str = "./reports"


@dataclass
class ScreenshotsConfig:
    dir: str = "./screenshots"


@dataclass
class Config:
    llm: LLMConfig = field(default_factory=LLMConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    workspace: WorkspaceConfig = field(default_factory=WorkspaceConfig)
    llm_context: LLMContextConfig = field(default_factory=LLMContextConfig)
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    shell: ShellConfig = field(default_factory=ShellConfig)
    git: GitConfig = field(default_factory=GitConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    reports: ReportsConfig = field(default_factory=ReportsConfig)
    screenshots: ScreenshotsConfig = field(default_factory=ScreenshotsConfig)

    # Populated by load(): absolute path config.yaml was loaded from,
    # used to resolve relative paths (workspace.root etc) consistently
    # regardless of the caller's current working directory.
    _base_dir: Path = field(default_factory=lambda: Path.cwd())

    def resolve_path(self, relative: str) -> Path:
        p = Path(relative)
        if p.is_absolute():
            return p
        return (self._base_dir / p).resolve()

    @property
    def workspace_root(self) -> Path:
        return self.resolve_path(self.workspace.root)


def _dc(cls, data: dict[str, Any] | None):
    data = data or {}
    known = {f for f in cls.__dataclass_fields__}
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"{cls.__name__}: unknown config keys {sorted(unknown)}")
    return cls(**data)


def load(path: str | Path = "config.yaml") -> Config:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return from_dict(raw, base_dir=path.resolve().parent)


def from_dict(raw: dict[str, Any], base_dir: Path | None = None) -> Config:
    raw = copy.deepcopy(raw)

    llm = _dc(LLMConfig, raw.get("llm"))
    if not llm.coding_model:
        raise ConfigError("llm.coding_model must be set in config.yaml (no hard-coded default allowed)")

    agent = _dc(AgentConfig, raw.get("agent"))
    workspace = _dc(WorkspaceConfig, raw.get("workspace"))
    llm_context = _dc(LLMContextConfig, raw.get("llm_context"))

    browser_raw = raw.get("browser") or {}
    viewports_raw = browser_raw.pop("viewports", [])
    viewports = [Viewport(**v) for v in viewports_raw]
    browser = _dc(BrowserConfig, browser_raw)
    browser.viewports = viewports
    if browser.enabled and not browser.viewports:
        raise ConfigError("browser.enabled is true but no viewports configured")

    shell = _dc(ShellConfig, raw.get("shell"))
    git = _dc(GitConfig, raw.get("git"))
    logging_cfg = _dc(LoggingConfig, raw.get("logging"))
    reports = _dc(ReportsConfig, raw.get("reports"))
    screenshots = _dc(ScreenshotsConfig, raw.get("screenshots"))

    return Config(
        llm=llm,
        agent=agent,
        workspace=workspace,
        llm_context=llm_context,
        browser=browser,
        shell=shell,
        git=git,
        logging=logging_cfg,
        reports=reports,
        screenshots=screenshots,
        _base_dir=base_dir or Path.cwd(),
    )
