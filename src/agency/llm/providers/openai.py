"""OpenAI provider — GPT-4, GPT-4o and GPT-3.5 via the ``openai`` SDK."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from agency.llm.config import LLMConfig
from agency.llm.providers.base import BaseProvider

_SUPPORTED_PREFIXES = ("gpt-4o", "gpt-4", "gpt-3.5", "o1", "o3")


class OpenAIProvider(BaseProvider):
    """Generate text with OpenAI chat-completions (async, streaming-capable)."""

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
        """Lazily build the ``AsyncOpenAI`` client so imports stay optional."""
        if self._client is not None:
            return self._client
        try:
            from openai import AsyncOpenAI  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "The 'openai' package is required for OpenAIProvider. Install with: pip install openai"
            ) from exc
        key = self._api_key_override
        if key is None and self._config.api_key is not None:
            key = self._config.api_key.get_secret_value()
        self._client = AsyncOpenAI(
            api_key=key,
            base_url=self._base_url_override or self._config.base_url,
            timeout=self._config.timeout,
        )
        return self._client

    async def generate(self, prompt: str, context: dict[str, Any] | None = None) -> str:
        """Complete ``prompt`` via ``chat.completions.create``."""
        client = self._client_or_raise()
        messages = self._build_messages(prompt, context)
        max_tokens = int((context or {}).get("max_tokens_override", self._config.max_tokens))
        try:
            response = await client.chat.completions.create(
                model=self._config.model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=self._config.temperature,
                timeout=self._config.timeout,
            )
        except Exception as exc:
            self._log.error("openai_generate_failed", model=self._config.model, error=str(exc))
            raise
        content = response.choices[0].message.content or ""
        self._log.debug("openai_generated", model=self._config.model, chars=len(content))
        return content

    async def stream(
        self, prompt: str, context: dict[str, Any] | None = None
    ) -> AsyncGenerator[str, None]:
        """Yield deltas from the streaming chat-completions endpoint."""
        client = self._client_or_raise()
        messages = self._build_messages(prompt, context)
        max_tokens = int((context or {}).get("max_tokens_override", self._config.max_tokens))
        try:
            response = await client.chat.completions.create(
                model=self._config.model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=self._config.temperature,
                timeout=self._config.timeout,
                stream=True,
            )
            async for chunk in response:
                for choice in chunk.choices:
                    delta = choice.delta.content
                    if delta:
                        yield delta
        except Exception as exc:
            self._log.error("openai_stream_failed", model=self._config.model, error=str(exc))
            raise

    def capabilities(self) -> dict[str, Any]:
        """Describe OpenAI capabilities."""
        return {
            "provider": "openai",
            "model": self._config.model,
            "streaming": True,
            "tools": ["function_calling", "json_mode"],
            "modalities": ["text", "vision", "audio"],
            "max_context_tokens": 128_000,
            "supported_prefixes": list(_SUPPORTED_PREFIXES),
            "notes": "GPT-4 / GPT-4o / GPT-3.5 via the OpenAI Python SDK.",
        }
