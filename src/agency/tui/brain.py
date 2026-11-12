"""LLM brain for the TUIs. Router picks provider, adapter streams."""

from __future__ import annotations

from collections.abc import AsyncIterator

from agency.llm.adapter import LLMAdapter
from agency.llm.router import LLMRouter


class Brain:
    def __init__(self, model: str | None = None) -> None:
        self.router = LLMRouter()
        self.model = model
        self.adapter = LLMAdapter(model=model) if model else LLMAdapter()

    async def ask(self, prompt: str) -> str:
        ctx: dict[str, object] = {}
        if self.model:
            ctx["model"] = self.model
        return await self.router.route(prompt, ctx)

    async def stream(self, prompt: str) -> AsyncIterator[str]:
        ctx: dict[str, object] = {}
        if self.model:
            ctx["model"] = self.model
        async for chunk in self.router.route_stream(prompt, ctx):
            yield chunk

    def describe(self) -> str:
        adapter = getattr(self.router, "adapter", None) or self.adapter
        provider = getattr(adapter, "_provider", "auto")
        model = getattr(adapter, "_model", "") or self.model or "default"
        return f"{provider}:{model}"
