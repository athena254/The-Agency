"""Hermetic regressions for bounded, privacy-safe DNS preflight."""

from __future__ import annotations

import asyncio
import threading

import httpx
import pytest

from agency.tools.base import ToolContext
from agency.tools.builtin import web


async def test_timed_out_dns_holds_capacity_until_worker_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(web, "_FETCH_DNS_TIMEOUT", 0.05)
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def resolver(host: str) -> list[str]:
        nonlocal calls
        calls += 1
        started.set()
        release.wait()
        return ["8.8.8.8"]

    semaphore = asyncio.Semaphore(1)
    try:
        first = asyncio.create_task(
            web._validate_fetch_url_async("https://example.com", resolver, semaphore)
        )
        assert await asyncio.to_thread(started.wait, 1)
        with pytest.raises(ValueError, match="timed out"):
            await first
        with pytest.raises(ValueError, match="capacity unavailable"):
            await web._validate_fetch_url_async("https://example.com", resolver, semaphore)
        assert calls == 1
    finally:
        release.set()
    await asyncio.wait_for(semaphore.acquire(), timeout=1)
    semaphore.release()


async def test_sync_validator_inside_event_loop_and_private_dns_error() -> None:
    assert web._validate_fetch_url("https://example.com", lambda _: ["8.8.8.8"])

    def secret_resolver(_: str) -> list[str]:
        raise RuntimeError("private-query-marker")

    tool = web.WebFetchTool(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text="x")),
        dns_resolver=secret_resolver,
    )
    result = await tool.run(
        {"url": "https://example.com/article"},
        ToolContext(agent_id="agent-1", task_id="task-1"),
    )
    assert result.ok is False
    assert result.error is not None
    assert "private-query-marker" not in result.error
