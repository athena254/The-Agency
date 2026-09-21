"""LLMAdapter — unified async facade over LiteLLM with per-provider fallback.

Resolution order per call:

1. Explicit ``model`` argument (``"provider/model"`` or bare model id).
2. Adapter default from :func:`~agency.llm.config.load_config` (env-driven).
3. Deterministic echo mode when no credentials / SDKs are available.

LiteLLM is used when installed; otherwise calls are delegated to the
concrete provider SDKs in :mod:`agency.llm.providers`.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import structlog

from agency.llm.config import LLMConfig, ProviderKind, load_config
from agency.llm.providers.anthropic import AnthropicProvider
from agency.llm.providers.base import BaseProvider
from agency.llm.providers.echo import EchoProvider
from agency.llm.providers.ollama import OllamaProvider
from agency.llm.providers.openai import OpenAIProvider

logger = structlog.get_logger(__name__)

# Model-id prefixes used to infer the provider when only a bare id is given.
_MODEL_PREFIX_ROUTES: tuple[tuple[str, ProviderKind], ...] = (
    ("claude", ProviderKind.ANTHROPIC),
    ("gpt", ProviderKind.OPENAI),
    ("o1", ProviderKind.OPENAI),
    ("o3", ProviderKind.OPENAI),
    ("openrouter/", ProviderKind.OPENROUTER),
    ("ollama/", ProviderKind.OLLAMA),
    ("llama", ProviderKind.OLLAMA),
    ("mistral", ProviderKind.OLLAMA),
    ("qwen", ProviderKind.OLLAMA),
    ("phi", ProviderKind.OLLAMA),
)


def _infer_provider(model: str, default: ProviderKind) -> ProviderKind:
    lowered = model.lower()
    if "/" in lowered:
        head, _ = lowered.split("/", 1)
        try:
            return ProviderKind(head)
        except ValueError:
            pass
    for prefix, kind in _MODEL_PREFIX_ROUTES:
        if lowered.startswith(prefix):
            return kind
    return default


class LLMAdapter:
    """Async multi-provider LLM facade with streaming and echo fallback.

    Usage::

        adapter = LLMAdapter()  # auto-selects provider from env vars
        text = await adapter.generate("Summarise this log", {"system": "You are terse."})
        async for chunk in adapter.stream("Write a haiku", model="anthropic/claude-haiku-3-5-20241022"):
            print(chunk, end="")
    """

    def __init__(self, config: LLMConfig | None = None) -> None:
        self._config = config or load_config()
        self._log = structlog.get_logger(f"{__name__}.{type(self).__name__}")
        self._providers: dict[ProviderKind, BaseProvider] = {}
        self._litellm: Any | None = None
        try:
            import litellm  # type: ignore[import-not-found]

            self._litellm = litellm
            self._log.debug("litellm_available")
        except ImportError:
            self._log.debug("litellm_unavailable_using_provider_sdks")

    # ------------------------------------------------------------------ #
    # Properties
    # ------------------------------------------------------------------ #

    @property
    def config(self) -> LLMConfig:
        """Default adapter configuration."""
        return self._config

    @property
    def provider_kind(self) -> ProviderKind:
        """Default provider selected from the environment."""
        return self._config.provider

    @property
    def echo_mode(self) -> bool:
        """Whether the adapter has no real credentials and echoes prompts."""
        return self._config.provider is ProviderKind.ECHO

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    async def generate(
        self,
        prompt: str,
        context: dict[str, Any] | None = None,
        model: str | None = None,
    ) -> str:
        """Generate a complete response for ``prompt``.

        Parameters
        ----------
        prompt:
            User instruction / prompt text.
        context:
            Optional context (``system``, generation overrides, ...).
        model:
            Optional override — ``"provider/model"`` or a bare model id.
        """
        resolved_model, kind = self._resolve(model)
        self._log.debug("llm_generate", provider=kind.value, model=resolved_model)
        provider = self._provider_for(kind, resolved_model)
        if self._litellm is not None and kind not in (ProviderKind.ECHO, ProviderKind.LOCAL):
            try:
                return await self._litellm_generate(prompt, context, resolved_model, kind)
            except Exception as exc:  # noqa: BLE001 — LiteLLM failure falls back to SDKs.
                self._log.warning("litellm_failed_falling_back", error=str(exc))
        return await provider.generate(prompt, context)

    async def stream(
        self,
        prompt: str,
        context: dict[str, Any] | None = None,
        model: str | None = None,
    ) -> AsyncGenerator[str, None]:
        """Yield response text incrementally as it arrives.

        Uses LiteLLM streaming when available, otherwise delegates to the
        concrete provider's streaming implementation.
        """
        resolved_model, kind = self._resolve(model)
        self._log.debug("llm_stream", provider=kind.value, model=resolved_model)
        if self._litellm is not None and kind not in (ProviderKind.ECHO, ProviderKind.LOCAL):
            try:
                async for chunk in self._litellm_stream(prompt, context, resolved_model, kind):
                    yield chunk
                return
            except Exception as exc:  # noqa: BLE001 — LiteLLM failure falls back to SDKs.
                self._log.warning("litellm_stream_failed_falling_back", error=str(exc))
        provider = self._provider_for(kind, resolved_model)
        async for chunk in provider.stream(prompt, context):
            yield chunk

    def capabilities(self) -> dict[str, Any]:
        """Describe the active default provider's capabilities."""
        return self._provider_for(self._config.provider, self._config.model).capabilities()

    async def health_check(self) -> bool:
        """Probe the default provider."""
        return await self._provider_for(self._config.provider, self._config.model).health_check()

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _resolve(self, model: str | None) -> tuple[str, ProviderKind]:
        if not model:
            return self._config.model, self._config.provider
        if "/" in model:
            head, rest = model.split("/", 1)
            try:
                return rest or self._config.model, ProviderKind(head.lower())
            except ValueError:
                return model, self._config.provider
        return model, _infer_provider(model, self._config.provider)

    def _provider_for(self, kind: ProviderKind, model: str) -> BaseProvider:
        cached = self._providers.get(kind)
        if cached is not None and cached.model == model:
            return cached
        config = self._scoped_config(kind, model)
        if kind is ProviderKind.OPENAI:
            provider: BaseProvider = OpenAIProvider(config)
        elif kind is ProviderKind.ANTHROPIC:
            provider = AnthropicProvider(config)
        elif kind in (ProviderKind.OLLAMA, ProviderKind.LOCAL):
            provider = OllamaProvider(config)
        elif kind is ProviderKind.OPENROUTER:
            # OpenRouter speaks the OpenAI protocol — reuse the OpenAI provider
            # pointed at the OpenRouter endpoint.
            provider = OpenAIProvider(
                config,
                base_url=config.base_url or "https://openrouter.ai/api/v1",
            )
        else:  # ECHO and anything unknown
            provider = EchoProvider(config)
        self._providers[kind] = provider
        return provider

    def _scoped_config(self, kind: ProviderKind, model: str) -> LLMConfig:
        if kind is self._config.provider and model == self._config.model:
            return self._config
        data = self._config.model_dump()
        data.update(provider=kind, model=model)
        if kind is ProviderKind.OPENROUTER and not data.get("base_url"):
            data["base_url"] = "https://openrouter.ai/api/v1"
        return LLMConfig(**data)

    def _litellm_kwargs(self, context: dict[str, Any] | None, kind: ProviderKind) -> dict[str, Any]:
        ctx = context or {}
        kwargs: dict[str, Any] = {
            "max_tokens": int(ctx.get("max_tokens_override", self._config.max_tokens)),
            "temperature": float(ctx.get("temperature_override", self._config.temperature)),
            "timeout": self._config.timeout,
        }
        if ctx.get("system"):
            kwargs["system"] = str(ctx["system"])
        if self._config.api_key is not None and kind is not ProviderKind.OLLAMA:
            kwargs["api_key"] = self._config.api_key.get_secret_value()
        if self._config.base_url:
            kwargs["base_url"] = self._config.base_url
            kwargs["api_base"] = self._config.base_url
        return kwargs

    async def _litellm_generate(
        self,
        prompt: str,
        context: dict[str, Any] | None,
        model: str,
        kind: ProviderKind,
    ) -> str:
        assert self._litellm is not None
        messages = self._litellm_messages(prompt, context)
        response = await self._litellm.acompletion(
            model=model, messages=messages, **self._litellm_kwargs(context, kind)
        )
        content = response.choices[0].message.content or ""
        self._log.debug("litellm_generated", model=model, chars=len(content))
        return content

    async def _litellm_stream(
        self,
        prompt: str,
        context: dict[str, Any] | None,
        model: str,
        kind: ProviderKind,
    ) -> AsyncGenerator[str, None]:
        assert self._litellm is not None
        messages = self._litellm_messages(prompt, context)
        response = await self._litellm.acompletion(
            model=model, messages=messages, stream=True, **self._litellm_kwargs(context, kind)
        )
        async for chunk in response:
            for choice in chunk.choices:
                delta = getattr(choice.delta, "content", None)
                if delta:
                    yield str(delta)

    @staticmethod
    def _litellm_messages(prompt: str, context: dict[str, Any] | None) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        if context and context.get("system"):
            messages.append({"role": "system", "content": str(context["system"])})
        messages.append({"role": "user", "content": prompt})
        return messages
