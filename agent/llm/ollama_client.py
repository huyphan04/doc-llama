"""
Ollama implementation of LLMClient.

Uses Ollama's /api/chat endpoint. Ollama's tool-calling support varies by
model — Qwen2.5-Coder supports it, many others don't — so we always also
instruct the model (via system prompt convention, injected by the caller)
to emit a JSON block as a fallback, and ToolExecutor-side code must be
tolerant of both paths. This client itself stays a thin, honest transport:
it does not silently invent tool_calls that the model didn't actually
request.
"""
from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import requests

from agent.llm.base import (
    ChatMessage,
    ChatResult,
    LLMClient,
    LLMError,
    ToolCall,
    ToolSpec,
    VisionNotSupportedError,
)


class OllamaClient(LLMClient):
    def __init__(
        self,
        base_url: str,
        coding_model: str,
        vision_model: str | None = None,
        request_timeout_seconds: int = 300,
        max_retries: int = 3,
        retry_backoff_seconds: int = 2,
    ):
        if not coding_model:
            raise LLMError("coding_model must be configured")
        self.base_url = base_url.rstrip("/")
        self.coding_model = coding_model
        self.vision_model = vision_model or None
        self.timeout = request_timeout_seconds
        self.max_retries = max_retries
        self.backoff = retry_backoff_seconds

    def _post(self, path: str, payload: dict) -> dict:
        url = f"{self.base_url}{path}"
        last_err: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = requests.post(url, json=payload, timeout=self.timeout)
                resp.raise_for_status()
                return resp.json()
            except (requests.RequestException, json.JSONDecodeError) as e:
                last_err = e
                if attempt < self.max_retries:
                    time.sleep(self.backoff * attempt)
        raise LLMError(f"Ollama request to {path} failed after {self.max_retries} attempts: {last_err}")

    @staticmethod
    def _to_ollama_messages(messages: list[ChatMessage]) -> list[dict]:
        out = []
        for m in messages:
            entry: dict = {"role": m.role, "content": m.content}
            if m.tool_calls:
                entry["tool_calls"] = [
                    {"function": {"name": tc.name, "arguments": tc.arguments}} for tc in m.tool_calls
                ]
            out.append(entry)
        return out

    @staticmethod
    def _to_ollama_tools(tools: list[ToolSpec] | None) -> list[dict] | None:
        if not tools:
            return None
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in tools
        ]

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.2,
        model: str | None = None,
    ) -> ChatResult:
        payload = {
            "model": model or self.coding_model,
            "messages": self._to_ollama_messages(messages),
            "stream": False,
            "options": {"temperature": temperature},
        }
        ollama_tools = self._to_ollama_tools(tools)
        if ollama_tools:
            payload["tools"] = ollama_tools

        data = self._post("/api/chat", payload)

        msg = data.get("message", {})
        tool_calls = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {})
            args = fn.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {"_raw": args}
            tool_calls.append(ToolCall(name=fn.get("name", ""), arguments=args))

        return ChatResult(
            message=ChatMessage(role="assistant", content=msg.get("content", ""), tool_calls=tool_calls),
            raw=data,
            model=payload["model"],
        )

    def vision(self, prompt: str, image_path: str, *, temperature: float = 0.1) -> ChatResult:
        if not self.vision_model:
            raise VisionNotSupportedError(
                "no vision_model configured in config.yaml — visual review step will be skipped"
            )
        img_bytes = Path(image_path).read_bytes()
        b64 = base64.b64encode(img_bytes).decode("ascii")
        payload = {
            "model": self.vision_model,
            "messages": [{"role": "user", "content": prompt, "images": [b64]}],
            "stream": False,
            "options": {"temperature": temperature},
        }
        data = self._post("/api/chat", payload)
        msg = data.get("message", {})
        return ChatResult(
            message=ChatMessage(role="assistant", content=msg.get("content", "")),
            raw=data,
            model=self.vision_model,
        )

    def health_check(self) -> tuple[bool, str]:
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=10)
            resp.raise_for_status()
            models = {m["name"] for m in resp.json().get("models", [])}
        except requests.RequestException as e:
            return False, f"cannot reach Ollama at {self.base_url}: {e}"

        missing = []
        # Ollama model names in /api/tags often include a ":tag" suffix already
        # matching config, but also match on bare name in case config omitted tag.
        def _present(name: str) -> bool:
            return name in models or any(m.split(":")[0] == name.split(":")[0] for m in models)

        if not _present(self.coding_model):
            missing.append(self.coding_model)
        if self.vision_model and not _present(self.vision_model):
            missing.append(self.vision_model)

        if missing:
            return False, f"Ollama reachable but model(s) not pulled: {', '.join(missing)}"
        return True, f"Ollama OK — coding_model={self.coding_model}, vision_model={self.vision_model or 'none'}"
