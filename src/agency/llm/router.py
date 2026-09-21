"""LLMRouter — pick the best provider for a task, then delegate to the adapter."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from enum import Enum
from typing import Any

import structlog

from agency.llm.adapter import LLMAdapter
from agency.llm.config import LLMConfig, ProviderKind, load_named_provider

logger = structlog.get_logger(__name__)


def _preset_usable(config: LLMConfig) -> bool:
    """Whether a preset can actually serve requests in this environment."""
    if config.provider is ProviderKind.ECHO:
        return True
    if config.provider in (ProviderKind.OLLAMA, ProviderKind.LOCAL):
        try:
            import ollama  # type: ignore[import-not-found]  # noqa: F401
        except ImportError:
            logger.debug("route_preset_unusable", preset=config.model, reason="ollama_sdk_missing")
            return False
        return True
    if config.api_key is None:
        logger.debug("route_preset_unusable", preset=config.model, reason="api_key_missing")
        return False
    return True


class TaskKind(str, Enum):
    """Coarse task categories used for routing heuristics."""

    REASONING = "reasoning"
    CODE = "code"
    FAST = "fast"
    PRIVATE = "private"
    GENERAL = "general"

    def __str__(self) -> str:
        return self.value


_REASONING_HINTS = (
    "prove",
    "theorem",
    "reason",
    "analyse",
    "analyze",
    "strategy",
    "plan",
    "argue",
    "philosoph",
    "research",
    "opinion",
    "complex",
)
_CODE_HINTS = (
    "code",
    "python",
    "typescript",
    "rust",
    " refactor",
    "debug",
    "function",
    "class ",
    "sql",
    "regex",
    "implement",
    "compile",
)
_FAST_HINTS = ("summarise", "summarize", "tldr", "quick", "short", "headline", "classify", "label")
_PRIVATE_HINTS = ("secret", "private", "internal", "confidential", "offline", "local")


def classify_task(task: str) -> TaskKind:
    """Heuristically classify ``task`` into a :class:`TaskKind`."""
    lowered = task.lower()
    if any(hint in lowered for hint in _PRIVATE_HINTS):
        return TaskKind.PRIVATE
    if any(hint in lowered for hint in _CODE_HINTS):
        return TaskKind.CODE
    if any(hint in lowered for hint in _REASONING_HINTS):
        return TaskKind.REASONING
    if any(hint in lowered for hint in _FAST_HINTS) or len(task.split()) < 12:
        return TaskKind.FAST
    return TaskKind.GENERAL


# Preferred named preset per task kind (first available credential wins).
_TASK_ROUTES: dict[TaskKind, tuple[str, ...]] = {
    TaskKind.REASONING: ("claude-opus", "claude", "gpt4", "gpt"),
    TaskKind.CODE: ("gpt4", "claude", "gpt", "local"),
    TaskKind.FAST: ("claude-haiku", "gpt35", "gpt", "local"),
    TaskKind.PRIVATE: ("local", "ollama", "echo"),
    TaskKind.GENERAL: ("gpt", "claude", "local"),
}


class LLMRouter:
    """Route tasks to the best provider based on task type or model override.

    Usage::

        router = LLMRouter()
        text = await router.route("Refactor this Python function ...", {})
        async for chunk in router.route_stream("Summarise ...", {"model": "claude"}):
            print(chunk, end="")
    """

    def __init__(
        self,
        adapter: LLMAdapter | None = None,
        routes: dict[TaskKind, tuple[str, ...]] | None = None,
    ) -> None:
        self._adapter = adapter or LLMAdapter()
        self._routes = routes or dict(_TASK_ROUTES)
        self._log = structlog.get_logger(f"{__name__}.{type(self).__name__}")
        self._preset_cache: dict[str, LLMAdapter] = {}

    # ------------------------------------------------------------------ #
    # Properties
    # ------------------------------------------------------------------ #

    @property
    def adapter(self) -> LLMAdapter:
        """Default adapter used when no route matches."""
        return self._adapter

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    async def route(self, task: str, context: dict[str, Any] | None = None) -> str:
        """Route ``task`` to the best provider and return the full response."""
        adapter, model = self._select(task, context)
        # An explicit model override is passed through verbatim; otherwise the
        # preset adapter already carries the right model so we pass None.
        override = (context or {}).get("model")
        return await adapter.generate(task, context, model=override or model)

    async def route_stream(
        self, task: str, context: dict[str, Any] | None = None
    ) -> AsyncGenerator[str, None]:
        """Route ``task`` to the best provider and yield output incrementally."""
        adapter, model = self._select(task, context)
        override = (context or {}).get("model")
        async for chunk in adapter.stream(task, context, model=override or model):
            yield chunk

    def explain(self, task: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return the routing decision without executing (for debugging)."""
        adapter, model = self._select(task, context)
        kind = self._kind_of(task, context)
        return {
            "task_kind": kind.value,
            "provider": adapter.config.provider.value,
            "model": model or adapter.config.model,
            "explicit_override": bool((context or {}).get("model")),
        }

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _kind_of(self, task: str, context: dict[str, Any] | None) -> TaskKind:
        if context and context.get("task_kind"):
            try:
                return TaskKind(str(context["task_kind"]).lower())
            except ValueError:
                self._log.warning("unknown_task_kind", value=context["task_kind"])
        return classify_task(task)

    def _select(self, task: str, context: dict[str, Any] | None) -> tuple[LLMAdapter, str | None]:
        ctx = context or {}
        if ctx.get("model"):
            # Explicit override — let the default adapter resolve it.
            self._log.debug("route_explicit_model", model=ctx["model"])
            return self._adapter, None
        if ctx.get("provider"):
            adapter = self._adapter_for_preset(str(ctx["provider"]))
            if adapter is not None:
                self._log.debug("route_explicit_provider", provider=ctx["provider"])
                return adapter, None
        kind = self._kind_of(task, ctx)
        for preset in self._routes.get(kind, ()):
            adapter = self._adapter_for_preset(preset)
            if adapter is not None:
                self._log.debug("route_selected", task_kind=kind.value, preset=preset)
                return adapter, None
        self._log.debug("route_default", task_kind=kind.value)
        return self._adapter, None

    def _adapter_for_preset(self, preset: str) -> LLMAdapter | None:
        cached = self._preset_cache.get(preset)
        if cached is not None:
            return cached
        try:
            config: LLMConfig = load_named_provider(preset, load_dotenv=False)
        except ValueError:
            return None
        if not _preset_usable(config):
            return None  # no credentials / SDK for this preset — try the next route.
        adapter = LLMAdapter(config)
        self._preset_cache[preset] = adapter
        return adapter
