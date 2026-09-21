"""Abstract base class for all LLM providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from typing import Any

import structlog

from agency.llm.config import LLMConfig


class BaseProvider(ABC):
    """Contract every LLM provider must fulfil."""

    def __init__(self, config: LLMConfig) -> None:
        self._config = config
        self._log = structlog.get_logger(f"{__name__}.{type(self).__name__}")

    @property
    def config(self) -> LLMConfig:
        """Validated provider configuration."""
        return self._config

    @property
    def model(self) -> str:
        """Model identifier served by this provider."""
        return self._config.model

    @abstractmethod
    async def generate(self, prompt: str, context: dict[str, Any] | None = None) -> str:
        """Generate a complete response for ``prompt``."""
        raise NotImplementedError

    @abstractmethod
    async def stream(
        self, prompt: str, context: dict[str, Any] | None = None
    ) -> AsyncGenerator[str, None]:
        """Yield response text incrementally as it arrives."""
        raise NotImplementedError
        yield ""  # pragma: no cover — keeps the function an async generator.

    @abstractmethod
    def capabilities(self) -> dict[str, Any]:
        """Return a JSON-serialisable capability descriptor."""
        raise NotImplementedError

    async def health_check(self) -> bool:
        """Lightweight liveness probe; ``True`` when the provider can serve."""
        try:
            await self.generate("ping", {"max_tokens_override": 1})
        except Exception as exc:  # noqa: BLE001 — probe must never raise.
            self._log.warning("provider_health_check_failed", error=str(exc))
            return False
        return True

    def _build_messages(self, prompt: str, context: dict[str, Any] | None) -> list[dict[str, str]]:
        """Combine ``prompt`` with an optional ``system`` preamble."""
        messages: list[dict[str, str]] = []
        if context:
            system = context.get("system")
            if system:
                messages.append({"role": "system", "content": str(system)})
        messages.append({"role": "user", "content": prompt})
        return messages
