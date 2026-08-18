from agent.config import LLMConfig
from agent.llm.base import (
    ChatMessage,
    ChatResult,
    LLMClient,
    LLMError,
    ToolCall,
    ToolSpec,
    VisionNotSupportedError,
)
from agent.llm.ollama_client import OllamaClient


def build_llm_client(cfg: LLMConfig) -> LLMClient:
    """Factory keyed on cfg.provider. Add new providers here — nothing
    else in the codebase needs to change since callers only see LLMClient."""
    if cfg.provider == "ollama":
        return OllamaClient(
            base_url=cfg.base_url,
            coding_model=cfg.coding_model,
            vision_model=cfg.vision_model,
            request_timeout_seconds=cfg.request_timeout_seconds,
            max_retries=cfg.max_retries,
            retry_backoff_seconds=cfg.retry_backoff_seconds,
        )
    raise ValueError(f"unknown llm.provider: {cfg.provider!r} (only 'ollama' implemented)")


__all__ = [
    "ChatMessage",
    "ChatResult",
    "LLMClient",
    "LLMError",
    "ToolCall",
    "ToolSpec",
    "VisionNotSupportedError",
    "OllamaClient",
    "build_llm_client",
]
