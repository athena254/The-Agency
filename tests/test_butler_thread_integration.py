"""Butler thread integration: isolation, restart, and HTTP auth boundary."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from agency.butler.server import create_app
from agency.butler.service import ButlerService
from agency.butler.threads import ThreadStore
from agency.orchestrator import AgencyOrchestrator


@pytest.mark.asyncio
async def test_thread_context_isolated_and_persistent(tmp_path):
    store_path = str(tmp_path / "threads.db")
    store = ThreadStore(store_path)
    orch = AgencyOrchestrator(memory_db_path=str(tmp_path / "memory.db"))
    service = ButlerService(orchestrator=orch, thread_store=store)
    await service.start()
    try:
        ws = service.create_workspace("alice", "Project")
        first = service.create_thread("alice", ws.id, "Architecture")
        second = service.create_thread("alice", ws.id, "Research")
        assert await service.handle_message("hello architecture", "alice", {"thread_id": first.id})
        assert await service.handle_message("hello research", "alice", {"thread_id": second.id})
        contents = [m.content for m in service.list_thread_messages("alice", first.id)]
        assert "hello architecture" in contents
        assert "hello research" not in contents
        with pytest.raises(KeyError):
            await service.handle_message("steal", "bob", {"thread_id": first.id})
        assert len(service.list_thread_messages("alice", first.id)) == 2
        with pytest.raises(KeyError):
            service.list_thread_messages("bob", first.id)
    finally:
        await service.stop()
        store.close()
    reopened = ThreadStore(store_path)
    try:
        assert len(reopened.list_messages("alice", first.id)) == 2
        assert len(reopened.list_messages("alice", second.id)) == 2
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_http_cannot_select_thread_using_spoofed_sender(tmp_path):
    store = ThreadStore(str(tmp_path / "threads.db"))
    service = ButlerService(
        orchestrator=AgencyOrchestrator(memory_db_path=str(tmp_path / "memory.db")),
        thread_store=store,
    )
    await service.start()
    ws = service.create_workspace("alice", "Project")
    thread = service.create_thread("alice", ws.id, "Private")
    app = create_app()
    app.state.butler = service
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/v1/message",
                json={"message": "private", "sender": "alice", "context": {"thread_id": thread.id}},
            )
            assert response.status_code == 403
            assert service.list_thread_messages("alice", thread.id) == []
    finally:
        await service.stop()
        store.close()


@pytest.mark.asyncio
async def test_http_spoofed_sender_cannot_recall_or_store_legacy_memory(tmp_path, monkeypatch):
    store = ThreadStore(str(tmp_path / "threads.db"))
    service = ButlerService(
        orchestrator=AgencyOrchestrator(memory_db_path=str(tmp_path / "memory.db")),
        thread_store=store,
    )
    await service.start()
    await service.handle_message("private legacy note", "alice", {})

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("anonymous HTTP must not access persistent memory")

    monkeypatch.setattr(service, "_recall_history", forbidden)
    monkeypatch.setattr(service, "_store_turn", forbidden)
    app = create_app()
    app.state.butler = service
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/v1/message",
                json={
                    "message": "repeat my private note",
                    "sender": "alice",
                    "context": {"memory_context": "private legacy note"},
                },
            )
        assert response.status_code == 200
        assert "private legacy note" not in response.json()["response"]
    finally:
        await service.stop()
        store.close()
