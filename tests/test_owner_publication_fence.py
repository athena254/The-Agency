"""Real publication transactions must lose to a prior shutdown fence."""

from __future__ import annotations

import asyncio
import threading

import pytest

from agency.admission.models import OwnerTaskRequest
from tests.test_owner_service import concrete as concrete_fixture
from tests.test_owner_service import make_service, terminal

concrete = concrete_fixture


@pytest.mark.parametrize("window", ["queued-transaction", "uncommitted-writes"])
async def test_stop_fences_publication_at_sqlite_boundary(concrete, monkeypatch, window):
    c = concrete
    service = make_service(c)
    entered, release = threading.Event(), threading.Event()
    transaction = c["store"]._transaction
    event = c["store"]._event

    def hold():
        entered.set()
        if not release.wait(10):
            raise TimeoutError("synthetic publication scheduling gate")

    def transaction_gate(work, *args, **kwargs):
        if window == "queued-transaction" and "commit_turn" in work.__qualname__:
            hold()
        return transaction(work, *args, **kwargs)

    def event_gate(conn, sequence, phase, code):
        event(conn, sequence, phase, code)
        if window == "uncommitted-writes" and phase == "committed":
            hold()

    monkeypatch.setattr(c["store"], "_transaction", transaction_gate)
    monkeypatch.setattr(c["store"], "_event", event_gate)
    stopping = None
    locked = False
    try:
        await service.start()
        _, thread = await service.create_thread(c["owner"], "thread", "Synthetic")
        accepted = await service.submit(
            c["owner"],
            OwnerTaskRequest(request_id="turn", thread_id=thread.id, message="calculate 2+3"),
        )
        async with asyncio.timeout(10):
            while not entered.is_set():
                await asyncio.sleep(0.005)
        await service._gate.acquire()
        locked = True
        stopping = asyncio.create_task(service.stop())
        await asyncio.sleep(0)
        assert service._stopping and not stopping.done()
        release.set()
        async with asyncio.timeout(10):
            await asyncio.shield(service._execution)
        row = await c["store"].get(c["owner"], accepted.task_id)
        assert row.state == "UNCERTAIN", "shutdown permitted queued publication"
        assert await c["store"].list_messages(c["owner"], thread.id) == []
        assert len(c["requests"]) == 2
    finally:
        release.set()
        if locked:
            service._gate.release()
        if stopping is not None:
            await stopping
        await service.stop()


async def test_success_committed_before_stop_is_immutable(concrete):
    c = concrete
    service = make_service(c)
    try:
        await service.start()
        _, thread = await service.create_thread(c["owner"], "thread", "Synthetic")
        accepted = await service.submit(
            c["owner"],
            OwnerTaskRequest(request_id="turn", thread_id=thread.id, message="calculate 2+3"),
        )
        completed = await terminal(service, c["owner"], accepted.task_id)
        assert completed.state == "COMPLETED"
        await service.stop()
        from agency.admission.store import OwnerPilotStore

        reopened = OwnerPilotStore(c["store"]._db_path)
        try:
            await reopened.initialize()
            assert (await reopened.get(c["owner"], accepted.task_id)).state == "COMPLETED"
            assert len(await reopened.list_messages(c["owner"], thread.id)) == 2
        finally:
            await reopened.close()
    finally:
        await service.stop()
