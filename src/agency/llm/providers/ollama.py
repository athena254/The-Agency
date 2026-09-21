"""Ollama provider — local models via the ``ollama`` SDK."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from agency.llm.config import LLMConfig
from agency.llm.providers.base import BaseProvider


class OllamaProvider(BaseProvider):
    """Generate text with a local Ollama server (async, streaming-capable)."""

    def __init__(self, config: LLMConfig, host: str | None = None) -> None:
        super().__init__(config)
        self._host_override = host
        self._client: Any | None = None

    def _client_or_raise(self) -> Any:
        """Lazily build the ``AsyncClient`` so imports stay optional."""
        if self._client is not None:
            return self._client
        try:
            from ollama import AsyncClient  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "The 'ollama' package is required for OllamaProvider. "
                "Install with: pip install ollama"
            ) from exc
        host = self._host_override or self._config.base_url or "http://localhost:11434"
        self._client = AsyncClient(host=host, timeout=self._config.timeout)
        return self._client

    async def generate(self, prompt: str, context: dict[str, Any] | None = None) -> str:
        """Complete ``prompt`` via ``chat``."""
        client = self._client_or_raise()
        messages = self._build_messages(prompt, context)
        options = {"temperature": self._config.temperature}
        try:
            response = await client.chat(
                model=self._config.model, messages=messages, options=options
            )
        except Exception as exc:
            self._log.error("ollama_generate_failed", model=self._config.model, error=str(exc))
            raise
        text = response.message.content or ""
        self._log.debug("ollama_generated", model=self._config.model, chars=len(text))
        return text

    async def stream(
        self, prompt: str, context: dict[str, Any] | None = None
    ) -> AsyncGenerator[str, None]:
        """Yield deltas from the streaming ``chat`` endpoint."""
        client = self._client_or_raise()
        messages = self._build_messages(prompt, context)
        options = {"temperature": self._config.temperature}
        try:
            response = await client.chat(
                model=self._config.model, messages=messages, options=options, stream=True
            )
            async for part in response:
                content = part.message.content
                if content:
                    yield content
        except Exception as exc:
            self._log.error("ollama_stream_failed", model=self._config.model, error=str(exc))
            raise

    def capabilities(self) -> dict[str, Any]:
        """Describe Ollama capabilities."""
        return {
            "provider": "ollama",
            "model": self._config.model,
            "streaming": True,
            "tools": [],
            "modalities": ["text"],
            "max_context_tokens": 32_768,
            "notes": "Local models via Ollama; data never leaves the host.",
        }
