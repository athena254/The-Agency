"""Anthropic provider — Claude Opus, Sonnet and Haiku via the ``anthropic`` SDK."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from agency.llm.config import LLMConfig
from agency.llm.providers.base import BaseProvider

_SUPPORTED_PREFIXES = ("claude-opus", "claude-sonnet", "claude-haiku", "claude-3", "claude-")


class AnthropicProvider(BaseProvider):
    """Generate text with the Anthropic Messages API (async, streaming-capable)."""

    def __init__(
        self,
        config: LLMConfig,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        super().__init__(config)
        self._base_url_override = base_url
        self._api_key_override = api_key
        self._client: Any | None = None

    def _client_or_raise(self) -> Any:
        """Lazily build the ``AsyncAnthropic`` client so imports stay optional."""
        if self._client is not None:
            return self._client
        try:
            from anthropic import AsyncAnthropic  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "The 'anthropic' package is required for AnthropicProvider. "
                "Install with: pip install anthropic"
            ) from exc
        key = self._api_key_override
        if key is None and self._config.api_key is not None:
            key = self._config.api_key.get_secret_value()
        kwargs: dict[str, Any] = {"api_key": key, "timeout": self._config.timeout}
        base_url = self._base_url_override or self._config.base_url
        if base_url:
            kwargs["base_url"] = base_url
        self._client = AsyncAnthropic(**kwargs)
        return self._client

    def _split_messages(
        self, prompt: str, context: dict[str, Any] | None
    ) -> tuple[str | None, list[dict[str, str]]]:
        """Split into (system, messages) — Anthropic takes system separately."""
        system: str | None = None
        if context and context.get("system"):
            system = str(context["system"])
        return system, [{"role": "user", "content": prompt}]

    async def generate(self, prompt: str, context: dict[str, Any] | None = None) -> str:
        """Complete ``prompt`` via ``messages.create``."""
        client = self._client_or_raise()
        system, messages = self._split_messages(prompt, context)
        max_tokens = int((context or {}).get("max_tokens_override", self._config.max_tokens))
        kwargs: dict[str, Any] = {
            "model": self._config.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": self._config.temperature,
        }
        if system:
            kwargs["system"] = system
        try:
            response = await client.messages.create(**kwargs)
        except Exception as exc:
            self._log.error("anthropic_generate_failed", model=self._config.model, error=str(exc))
            raise
        parts = [block.text for block in response.content if getattr(block, "type", "") == "text"]
        text = "".join(parts)
        self._log.debug("anthropic_generated", model=self._config.model, chars=len(text))
        return text

    async def stream(
        self, prompt: str, context: dict[str, Any] | None = None
    ) -> AsyncGenerator[str, None]:
        """Yield deltas from the streaming Messages API."""
        client = self._client_or_raise()
        system, messages = self._split_messages(prompt, context)
        max_tokens = int((context or {}).get("max_tokens_override", self._config.max_tokens))
        kwargs: dict[str, Any] = {
            "model": self._config.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": self._config.temperature,
            "stream": True,
        }
        if system:
            kwargs["system"] = system
        try:
            async with client.messages.stream(**kwargs) as stream:
                async for text in stream.text_stream:
                    yield text
        except Exception as exc:
            self._log.error("anthropic_stream_failed", model=self._config.model, error=str(exc))
            raise

    def capabilities(self) -> dict[str, Any]:
        """Describe Anthropic capabilities."""
        return {
            "provider": "anthropic",
            "model": self._config.model,
            "streaming": True,
            "tools": ["tool_use", "computer_use"],
            "modalities": ["text", "vision"],
            "max_context_tokens": 200_000,
            "supported_prefixes": list(_SUPPORTED_PREFIXES),
            "notes": "Claude Opus / Sonnet / Haiku via the Anthropic Python SDK.",
        }
