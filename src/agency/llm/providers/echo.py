"""Echo provider — deterministic fallback used for tests and offline runs."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from agency.llm.config import LLMConfig, ProviderKind
from agency.llm.providers.base import BaseProvider


class EchoProvider(BaseProvider):
    """Return the prompt as-is (optionally chunked for streaming)."""

    def __init__(self, config: LLMConfig | None = None) -> None:
        super().__init__(
            config
            or LLMConfig(provider=ProviderKind.ECHO, model="echo", temperature=0.0, max_tokens=4096)
        )

    async def generate(self, prompt: str, context: dict[str, Any] | None = None) -> str:
        """Return the prompt unchanged."""
        self._log.debug("echo_generate", prompt_len=len(prompt))
        prefix = ""
        if context and context.get("system"):
            prefix = f"[system: {context['system']}] "
        return f"{prefix}{prompt}"

    async def stream(
        self, prompt: str, context: dict[str, Any] | None = None
    ) -> AsyncGenerator[str, None]:
        """Yield the prompt back in word-sized chunks."""
        text = await self.generate(prompt, context)
        chunk_size = 8
        if context and isinstance(context.get("chunk_size"), int):
            chunk_size = max(1, int(context["chunk_size"]))
        words = text.split(" ")
        chunk: list[str] = []
        for word in words:
            chunk.append(word)
            if len(chunk) >= chunk_size:
                yield " ".join(chunk) + " "
                chunk = []
        if chunk:
            yield " ".join(chunk)

    def capabilities(self) -> dict[str, Any]:
        """Describe echo capabilities."""
        return {
            "provider": ProviderKind.ECHO.value,
            "model": self._config.model,
            "streaming": True,
            "tools": [],
            "modalities": ["text"],
            "max_context_tokens": 1_000_000,
            "notes": "Deterministic test/fallback provider; no network access.",
        }
