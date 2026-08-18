"""
LLM provider abstraction.

Spec section 3 requires that Ollama not be baked into the agent logic, so
that a different backend could replace it later. Every agent role (Planner,
Coder, Tester analysis, Reviewer, vision review) talks to this interface,
never to agent.llm.ollama_client directly.

Two capabilities are modeled separately because they have different
contracts in practice: chat() is text-in/text-in-out with optional
tool-calling, vision() takes an image and returns text. A provider that
can't do vision (e.g. a text-only local model) should raise
VisionNotSupportedError rather than silently degrading — callers (the
Reviewer) need to know to skip visual review rather than trust a bad
default.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class LLMError(Exception):
    """Raised after retries are exhausted or on unrecoverable provider errors."""


class VisionNotSupportedError(LLMError):
    """Raised when vision() is called but no vision_model is configured."""


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON-schema-like dict


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]
    id: str = ""


@dataclass
class ChatMessage:
    role: str  # "system" | "user" | "assistant" | "tool"
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str = ""  # set on role="tool" replies


@dataclass
class ChatResult:
    message: ChatMessage
    raw: Any = None
    model: str = ""


class LLMClient(ABC):
    """Abstract provider interface. Concrete implementation: OllamaClient."""

    @abstractmethod
    def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.2,
        model: str | None = None,
    ) -> ChatResult:
        """Send a chat completion request. `model` overrides the configured
        coding_model for this call (rarely needed). Tools, if provided, may
        or may not be honored depending on provider/model tool-calling
        support — callers must handle a ChatResult with no tool_calls even
        when tools were offered, and fall back to parsing structured JSON
        from message.content."""
        raise NotImplementedError

    @abstractmethod
    def vision(self, prompt: str, image_path: str, *, temperature: float = 0.1) -> ChatResult:
        """Send an image + prompt to the configured vision model."""
        raise NotImplementedError

    @abstractmethod
    def health_check(self) -> tuple[bool, str]:
        """Returns (ok, message). Used by `ai-agent doctor`."""
        raise NotImplementedError
