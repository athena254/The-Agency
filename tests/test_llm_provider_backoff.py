"""LLM provider backoff: transient 402/429/5xx are retried with backoff.

The retry lives in ``LLMAdapter.generate`` (the dispatcher/executor path) so
a burst of parallel task executions absorbs provider quota windows instead of
failing outright. Non-transient statuses surface immediately.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from agency.llm import adapter as adapter_module
from agency.llm.adapter import LLMAdapter
from agency.llm.config import LLMConfig, ProviderKind


def _adapter() -> LLMAdapter:
    return LLMAdapter(
        config=LLMConfig(
            provider=ProviderKind.OPENAI,
            model="test-model",
            api_key="test-key",
            base_url="http://provider.test/v1",
        )
    )


def _ok() -> httpx.Response:
    return httpx.Response(
        200, json={"choices": [{"message": {"content": "pong"}}]}, request=httpx.Request("POST", "x")
    )


@pytest.mark.asyncio
async def test_retryable_status_is_retried_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(adapter_module, "_PROVIDER_BACKOFF_BASE_S", 0.001)
    posts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posts.append(1)
        if len(posts) < 3:
            return httpx.Response(429, request=request)
        return _ok()

    real_client = httpx.AsyncClient

    def client_factory(**kwargs: object) -> httpx.AsyncClient:
        return real_client(transport=httpx.MockTransport(handler), **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)
    text = await _adapter().generate("ping")
    assert text == "pong"
    assert len(posts) == 3


@pytest.mark.asyncio
async def test_retry_after_header_is_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    delays: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    posts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posts.append(1)
        if len(posts) == 1:
            return httpx.Response(429, headers={"Retry-After": "7"}, request=request)
        return _ok()

    real_client = httpx.AsyncClient

    def client_factory(**kwargs: object) -> httpx.AsyncClient:
        return real_client(transport=httpx.MockTransport(handler), **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)
    text = await _adapter().generate("ping")
    assert text == "pong"
    assert delays == [7.0]  # Retry-After wins over the exponential base


@pytest.mark.asyncio
async def test_persistent_throttle_fails_after_max_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(adapter_module, "_PROVIDER_BACKOFF_BASE_S", 0.001)
    posts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posts.append(1)
        return httpx.Response(402, request=request)

    real_client = httpx.AsyncClient

    def client_factory(**kwargs: object) -> httpx.AsyncClient:
        return real_client(transport=httpx.MockTransport(handler), **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)
    with pytest.raises(httpx.HTTPStatusError):
        await _adapter().generate("ping")
    assert len(posts) == adapter_module._PROVIDER_MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_non_retryable_status_surfaces_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    posts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posts.append(1)
        return httpx.Response(401, request=request)

    real_client = httpx.AsyncClient

    def client_factory(**kwargs: object) -> httpx.AsyncClient:
        return real_client(transport=httpx.MockTransport(handler), **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)
    with pytest.raises(httpx.HTTPStatusError):
        await _adapter().generate("ping")
    assert len(posts) == 1
