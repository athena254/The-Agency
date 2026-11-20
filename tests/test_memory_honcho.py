"""Tests for the optional Honcho mirror: SMS stays primary, Honcho degrades."""

from __future__ import annotations

from typing import Any

import pytest
import pytest_asyncio

from agency.config.settings import AgencySettings
from agency.memory.factory import build_memory_backend
from agency.memory.honcho.backend import HonchoMemoryBackend
from agency.memory.honcho.config import build_honcho_client
from agency.memory.sms.models import MemoryItem
from agency.memory.sms.scope import PrincipalMemoryScope, PrincipalScope
from agency.memory.sms.store import MemoryStore


class FakeHonchoClient:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []
        self.raise_on_search = False
        self.is_ready = True

    async def add_message(self, *, peer: str, content: str) -> None:
        self.messages.append((peer, content))

    async def search(self, *, peer: str, query: str, limit: int) -> list[dict[str, Any]]:
        if self.raise_on_search:
            raise RuntimeError("honcho unavailable")
        return [
            {"id": f"h-{i}", "content": text, "metadata": {"tier": "normal"}}
            for i, (owner, text) in enumerate(self.messages)
            if owner == peer and query.lower() in text.lower()
        ][:limit]

    async def ready(self) -> bool:
        return self.is_ready


@pytest_asyncio.fixture
async def client() -> FakeHonchoClient:
    return FakeHonchoClient()


@pytest_asyncio.fixture
async def honcho_backend(tmp_path: Any, client: FakeHonchoClient) -> HonchoMemoryBackend:
    backend = HonchoMemoryBackend(str(tmp_path / "memory.db"), client=client)
    await backend.initialize()
    try:
        yield backend
    finally:
        await backend.close()


async def test_default_factory_uses_sms(tmp_path: Any):
    settings = AgencySettings(memory_backend="sms")
    store = build_memory_backend(str(tmp_path / "memory.db"), settings=settings)
    assert type(store) is MemoryStore


async def test_factory_selects_honcho_when_configured(tmp_path: Any):
    settings = AgencySettings(
        memory_backend="honcho",
        honcho_enabled=True,
        honcho_url="http://localhost:8000",
        honcho_workspace="w",
        honcho_api_key="k",
    )
    store = build_memory_backend(str(tmp_path / "memory.db"), settings=settings)
    assert isinstance(store, HonchoMemoryBackend)
    assert store.honcho_enabled is True


async def test_disabled_honcho_client_is_none():
    settings = AgencySettings(memory_backend="sms")
    assert build_honcho_client(settings) is None
    settings = AgencySettings(
        memory_backend="honcho",
        honcho_enabled=False,
        honcho_url="http://localhost:8000",
        honcho_workspace="w",
        honcho_api_key="k",
    )
    assert build_honcho_client(settings) is None


async def test_write_is_durable_in_sms_and_mirrored(
    honcho_backend: HonchoMemoryBackend, client: FakeHonchoClient
):
    item = await honcho_backend.store(MemoryItem(agent_id="principal-1", content="remember this"))
    assert await honcho_backend.get(item.id) is not None
    assert client.messages == [("principal-1", "remember this")]


async def test_search_prefers_honcho_then_falls_back(
    honcho_backend: HonchoMemoryBackend, client: FakeHonchoClient
):
    await honcho_backend.store(MemoryItem(agent_id="p1", content="alpha beta"))
    found = await honcho_backend.search_text("alpha", agent_id="p1")
    assert found and found[0].id.startswith("h-")

    client.raise_on_search = True
    found = await honcho_backend.search_text("alpha", agent_id="p1")
    assert found and not found[0].id.startswith("h-")


async def test_honcho_backend_stays_principal_bound(honcho_backend: HonchoMemoryBackend):
    principal = PrincipalScope(owner_id="o", transport="local", subject_id="s")
    scope = PrincipalMemoryScope(honcho_backend, principal)
    mine = await scope.write("my private note")
    assert (await scope.query("private"))[0].id == mine.id
    other = await honcho_backend.list_by_agent("other", limit=10)
    assert other == []


async def test_settings_reject_incomplete_honcho():
    with pytest.raises(ValueError):
        AgencySettings(memory_backend="honcho")
