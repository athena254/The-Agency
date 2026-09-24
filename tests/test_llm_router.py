"""Hermetic contract checks for LLMRouter and the real LLMAdapter API."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from agency.llm.adapter import LLMAdapter
from agency.llm.config import LLMConfig, ProviderKind
from agency.llm.router import LLMRouter, TaskKind


def _router(adapter: LLMAdapter) -> LLMRouter:
    # A non-empty routing map with no usable presets forces the supplied adapter.
    return LLMRouter(adapter=adapter, routes={TaskKind.FAST: ()})


@pytest.mark.asyncio
async def test_route_passes_model_override_through_context(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = LLMAdapter(provider="echo", model="base")
    seen: list[tuple[str, dict[str, Any]]] = []

    async def generate(prompt: str, context: dict[str, Any] | None = None) -> str:
        seen.append((prompt, dict(context or {})))
        return "answer"

    monkeypatch.setattr(adapter, "generate", generate)
    incoming = {"model": "chosen", "request_id": "abc"}
    assert await _router(adapter).route("short task", incoming) == "answer"
    assert seen == [("short task", incoming)]
    assert incoming == {"model": "chosen", "request_id": "abc"}


@pytest.mark.asyncio
async def test_route_stream_uses_actual_adapter_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = LLMAdapter(provider="echo", model="base")
    seen: list[dict[str, Any]] = []

    async def stream(_prompt: str, context: dict[str, Any] | None = None) -> AsyncIterator[str]:
        seen.append(dict(context or {}))
        yield "part"
        yield "two"

    monkeypatch.setattr(adapter, "stream", stream)
    chunks = [chunk async for chunk in _router(adapter).route_stream("short task", {"model": "chosen"})]
    assert chunks == ["part", "two"]
    assert seen == [{"model": "chosen"}]


@pytest.mark.asyncio
async def test_provider_preset_alias_is_normalized_for_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    default = LLMAdapter(provider="echo", model="default")
    selected = LLMAdapter(provider="echo", model="selected")
    router = _router(default)
    monkeypatch.setattr(router, "_adapter_for_preset", lambda preset: selected if preset == "claude" else None)
    seen: list[dict[str, Any]] = []

    async def generate(_prompt: str, context: dict[str, Any] | None = None) -> str:
        seen.append(dict(context or {}))
        return "selected"

    monkeypatch.setattr(selected, "generate", generate)
    request = {"provider": "claude", "trace": "t"}
    assert await router.route("short task", request) == "selected"
    assert seen == [{"provider": "echo", "trace": "t"}]
    assert request == {"provider": "claude", "trace": "t"}


def test_explain_uses_adapter_metadata_and_explicit_model() -> None:
    adapter = LLMAdapter(provider="echo", model="base")
    router = _router(adapter)
    assert router.explain("short task") == {
        "task_kind": "fast", "provider": "echo", "model": "base", "explicit_override": False
    }
    assert router.explain("short task", {"model": "override"}) == {
        "task_kind": "fast", "provider": "echo", "model": "override", "explicit_override": True
    }


def test_echo_preset_construction_uses_config_keyword(monkeypatch: pytest.MonkeyPatch) -> None:
    config = LLMConfig(provider=ProviderKind.ECHO, model="echo")
    monkeypatch.setattr("agency.llm.router.load_named_provider", lambda *_args, **_kwargs: config)
    router = _router(LLMAdapter(provider="echo", model="fallback"))
    selected = router._adapter_for_preset("echo")
    assert selected is not None
    assert selected.provider == "echo"
    assert selected.model == "echo"
    assert router._adapter_for_preset("echo") is selected


@pytest.mark.asyncio
async def test_unconfigured_preset_falls_back_to_local_echo(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = LLMAdapter(provider="echo", model="echo")
    router = LLMRouter(adapter=adapter, routes={TaskKind.FAST: ("missing-preset",)})
    monkeypatch.setattr(router, "_adapter_for_preset", lambda _name: None)
    # Real echo adapter: this path must not call any network provider.
    response = await router.route("short task")
    assert isinstance(response, str) and response


@pytest.mark.asyncio
async def test_unavailable_explicit_local_provider_never_falls_back_remote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    default = LLMAdapter(provider="echo", model="default")
    remote = LLMAdapter(provider="openai", model="remote")
    router = LLMRouter(adapter=default, routes={TaskKind.FAST: ("remote",)})
    calls: list[str] = []

    def preset(name: str) -> LLMAdapter | None:
        calls.append(name)
        return remote if name == "remote" else None

    monkeypatch.setattr(router, "_adapter_for_preset", preset)
    with pytest.raises(ValueError, match="requested provider 'local' is unavailable"):
        await router.route("short task", {"provider": "local"})
    with pytest.raises(ValueError, match="requested provider 'local' is unavailable"):
        _ = [chunk async for chunk in router.route_stream("short task", {"provider": "local"})]
    with pytest.raises(ValueError, match="requested provider 'local' is unavailable"):
        router.explain("short task", {"provider": "local"})
    assert calls == ["local", "local", "local"]


@pytest.mark.asyncio
async def test_explicit_provider_remains_binding_with_model_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    default = LLMAdapter(provider="echo", model="default")
    selected = LLMAdapter(provider="echo", model="selected")
    router = _router(default)
    monkeypatch.setattr(router, "_adapter_for_preset", lambda name: selected if name == "local" else None)
    seen: list[dict[str, Any]] = []

    async def generate(_prompt: str, context: dict[str, Any] | None = None) -> str:
        seen.append(dict(context or {}))
        return "from-local"

    monkeypatch.setattr(selected, "generate", generate)
    monkeypatch.setattr(default, "generate", lambda *_args, **_kwargs: pytest.fail("default used"))
    assert await router.route("short task", {"provider": "local", "model": "chosen"}) == "from-local"
    assert seen == [{"provider": "echo", "model": "chosen"}]
