"""Actual consumer lifetimes against the shared factory, not fresh-handle doubles."""

from __future__ import annotations

import asyncio
from contextlib import suppress

import pytest

import agency.orchestrator as orchestrator_module
from agency.api import server
from agency.butler.service import ButlerService
from agency.lattice import factory
from agency.lattice.models import LatticeConfig
from agency.orchestrator import AgencyOrchestrator


@pytest.fixture
async def owners(tmp_path, monkeypatch):
    await factory.reset_lattice()
    config = LatticeConfig(sqlite_path=str(tmp_path / "shared.db"))
    monkeypatch.setattr(factory, "get_config", lambda: config)
    instances = []
    closers = []

    def make():
        orch = AgencyOrchestrator(memory_db_path=":memory:")
        instances.append(orch)
        closers.extend(
            [orch._memory_store.close, orch._evidence_store.close, orch._audit_log.close]
        )
        return orch

    yield make
    for orch in instances:
        with suppress(BaseException):
            await orch.stop()
    for close in closers:
        await close()
    await factory.reset_lattice()


@pytest.mark.parametrize("first_stop", [0, 1])
async def test_real_orchestrators_share_restart_and_final_close(owners, first_stop, monkeypatch):
    pair = [owners(), owners()]
    a, b = pair
    await a.start()
    await b.start()
    shared = a._lattice
    assert shared is b._lattice
    assert shared is not None
    backend = shared.backend
    close = backend.close
    closes = []

    async def counted_close():
        closes.append(shared)
        await close()

    monkeypatch.setattr(backend, "close", counted_close)
    lease = a._lattice_lease
    await a.start()
    assert a._lattice_lease is lease
    row = await shared.create_node("task", {"synthetic": "committed"})
    await pair[first_stop].stop()
    assert backend._db is not None
    assert closes == []
    survivor = pair[1 - first_stop]
    assert await survivor._lattice.get_node(row) is not None
    agent = await survivor.register_agent("survivor", "general", ["respond"])
    await survivor.submit_task("synthetic", "still usable", agent.id)
    await pair[first_stop].start()
    assert pair[first_stop]._lattice is shared
    assert await pair[first_stop]._lattice.get_node(row) is not None
    await survivor.stop()
    assert backend._db is not None
    await pair[first_stop].stop()
    assert backend._db is None
    assert closes == [shared]
    assert factory._manager is None
    await a.start()
    assert a._lattice is not shared
    assert await a._lattice.get_node(row) is not None
    await a.stop()


async def test_real_butler_borrows_one_orchestrator_lease_and_restarts(owners):
    orch = owners()
    service = ButlerService(orchestrator=orch)
    try:
        await service.start()
        shared = service.lattice
        assert shared is orch._lattice
        generation = factory._manager.generation
        assert len(generation.tokens) == 1
        assert not generation.legacy_pin
        await service.start()
        assert len(generation.tokens) == 1
        row = await shared.create_node("task", {"synthetic": True})
        await service.stop()
        assert shared.backend._db is None
        assert service.lattice is None
        await service.start()
        assert service.lattice is orch._lattice is not shared
        assert await service.lattice.get_node(row) is not None
        assert len(factory._manager.generation.tokens) == 1
    finally:
        await service.stop()
    assert factory._manager is None


@pytest.mark.parametrize("api_first", [False, True])
async def test_real_api_and_orchestrator_release_independently(owners, api_first):
    orch = owners()
    app = server.create_app()
    await orch.start()
    shared = orch._lattice
    async with server.lifespan(app):
        assert app.state.lattice is shared
        row = await shared.create_node("task", {"synthetic": True})
        generation = factory._manager.generation
        assert len(generation.tokens) == 2
        assert not generation.legacy_pin
        memory, evidence = app.state.memory_store, app.state.evidence_store
        if not api_first:
            await orch.stop()
            assert await app.state.lattice.get_node(row) is not None
            assert shared.backend._db is not None
    assert app.state.lattice is None
    assert app.state.memory_store is app.state.memory_retrieval is None
    assert app.state.evidence_store is None
    assert memory._conn is evidence._conn is None
    if api_first:
        assert await orch._lattice.get_node(row) is not None
        await orch.stop()
    assert shared.backend._db is None
    assert factory._manager is None


class BrokenDiagnostics:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        raise LookupError("diagnostic failed")

    exception = warning


@pytest.mark.parametrize(
    "boundary",
    [
        "evidence_construct",
        "memory_construct",
        "evidence",
        "memory",
        "acquire",
        "post_acquire",
        "yield",
    ],
)
@pytest.mark.parametrize("cancelled", [False, True])
async def test_api_failure_rolls_back_and_preserves_primary(
    owners, monkeypatch, boundary, cancelled
):
    app = server.create_app()
    primary = asyncio.CancelledError("primary") if cancelled else RuntimeError("primary")
    stores, originals, leases = [], [], []
    acquire = server.acquire_lattice

    async def tracked_acquire():
        if boundary == "acquire":
            raise primary
        lease = await acquire()
        leases.append(lease)
        return lease

    monkeypatch.setattr(server, "acquire_lattice", tracked_acquire)
    for name, kind in [("evidence", server.EvidenceStore), ("memory", server.MemoryStore)]:

        def make(*args, _name=name, _kind=kind):
            if boundary == _name + "_construct":
                raise primary
            store = _kind(*args)
            stores.append(store)
            originals.append(store.close)
            initialize, close = store.initialize, store.close

            async def initialize_or_fail():
                await initialize()
                if boundary == _name:
                    raise primary

            async def close_and_fail():
                await close()
                raise ValueError("cleanup")

            monkeypatch.setattr(store, "initialize", initialize_or_fail)
            if _name == "evidence":
                monkeypatch.setattr(store, "close", close_and_fail)
            return store

        monkeypatch.setattr(server, "EvidenceStore" if name == "evidence" else "MemoryStore", make)
    diagnostic = BrokenDiagnostics()
    if boundary == "post_acquire":

        def fail_info(*args, **kwargs):
            raise primary

        monkeypatch.setattr(diagnostic, "info", fail_info)
    monkeypatch.setattr(server, "log", diagnostic)
    try:
        with pytest.raises(type(primary)) as caught:
            async with server.lifespan(app):
                raise primary
        assert caught.value is primary
        assert all(store._conn is None for store in stores)
        assert app.state.lattice is None
        assert app.state.memory_store is app.state.memory_retrieval is None
        assert app.state.evidence_store is None
        assert factory._manager is None
    finally:
        for lease in leases:
            await lease.release()
        for close in originals:
            await close()


@pytest.mark.parametrize("startup", [False, True])
async def test_orchestrator_diagnostics_cannot_skip_cleanup_or_mask_primary(
    owners, monkeypatch, startup
):
    orch = owners()
    primary = RuntimeError("primary")
    close = orch._evidence_store.close

    async def fail_close():
        await close()
        raise primary

    if startup:

        def fail_tools():
            raise primary

        monkeypatch.setattr(orch, "_build_tool_layer", fail_tools)
    else:
        await orch.start()
    monkeypatch.setattr(orch._evidence_store, "close", fail_close)
    monkeypatch.setattr(orch, "_log", BrokenDiagnostics())
    with pytest.raises(RuntimeError) as caught:
        await (orch.start() if startup else orch.stop())
    assert caught.value is primary
    assert orch._memory_store._conn is None
    assert orch._audit_log._conn is None
    assert orch._lattice is orch._tool_driver is orch._tool_registry is None
    assert not orch._started
    assert factory._manager is None


async def test_orchestrator_started_diagnostic_failure_rolls_back(owners, monkeypatch):
    orch = owners()
    primary = RuntimeError("started diagnostic")

    class StartupDiagnostic(BrokenDiagnostics):
        def info(self, event, **kwargs):
            if event == "orchestrator_started":
                raise primary

    monkeypatch.setattr(orch, "_log", StartupDiagnostic())
    with pytest.raises(RuntimeError) as caught:
        await orch.start()
    assert caught.value is primary
    assert not orch._started
    assert orch._lattice is orch._tool_registry is orch._tool_driver is None
    assert orch._memory_store._conn is orch._audit_log._conn is orch._evidence_store._conn is None
    assert factory._manager is None


@pytest.mark.parametrize("consumer", ["api", "orchestrator"])
async def test_repeated_cancellation_drains_owned_cleanup(owners, monkeypatch, consumer):
    orch = owners()
    app = server.create_app()
    entered, proceed, active, exit_lifespan = (asyncio.Event() for _ in range(4))
    original = None

    async def gated_close():
        entered.set()
        await proceed.wait()
        await original()

    if consumer == "orchestrator":
        await orch.start()
        original = orch._memory_lifecycle.stop_periodic
        monkeypatch.setattr(orch._memory_lifecycle, "stop_periodic", gated_close)
        task = asyncio.create_task(orch.stop())
        stores = [orch._memory_store, orch._evidence_store, orch._audit_log]
    else:

        async def run():
            async with server.lifespan(app):
                active.set()
                await exit_lifespan.wait()

        task = asyncio.create_task(run())
        await active.wait()
        stores = [app.state.memory_store, app.state.evidence_store]
        original = app.state.evidence_store.close
        monkeypatch.setattr(app.state.evidence_store, "close", gated_close)
        exit_lifespan.set()
    try:
        await entered.wait()
        for _ in range(2):
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done(), "cleanup must remain owned until its gated close completes"
        proceed.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert all(store._conn is None for store in stores)
        assert factory._manager is None
        if consumer == "api":
            assert app.state.lattice is app.state.memory_store is app.state.evidence_store is None
        else:
            assert orch._lattice is None
            assert not orch._started
    finally:
        proceed.set()
        with suppress(BaseException):
            await task
        for store in stores:
            if consumer == "api" and store is stores[-1]:
                await original()
            else:
                await store.close()


@pytest.mark.parametrize("cancelled", [False, True])
async def test_real_failed_orchestrator_start_leaves_surviving_owner(
    owners, monkeypatch, cancelled
):
    survivor, failed = owners(), owners()
    await survivor.start()
    shared = survivor._lattice
    primary = asyncio.CancelledError("startup") if cancelled else RuntimeError("startup")

    def fail_tools():
        raise primary

    monkeypatch.setattr(failed, "_build_tool_layer", fail_tools)
    with pytest.raises(type(primary)) as caught:
        await failed.start()
    assert caught.value is primary
    assert not failed._started
    assert failed._lattice is failed._lattice_lease is None
    assert (
        failed._memory_store._conn
        is failed._evidence_store._conn
        is failed._audit_log._conn
        is None
    )
    assert len(factory._manager.generation.tokens) == 1
    row = await shared.create_node("task", {"survivor": True})
    assert await shared.get_node(row) is not None
    await survivor.stop()
    assert shared.backend._db is None
    assert factory._manager is None


@pytest.mark.parametrize("consumer", ["api", "orchestrator"])
@pytest.mark.parametrize("cancelled", [False, True])
async def test_final_release_failure_surfaces_and_cleans_independent_stores(
    owners, monkeypatch, consumer, cancelled
):
    orch = owners()
    app = server.create_app()
    primary = asyncio.CancelledError("close") if cancelled else RuntimeError("close")
    backend = None
    original = None
    stores = []

    async def fail_close():
        await original()
        raise primary

    async def install(shared, allocated):
        nonlocal backend, original, stores
        backend, stores = shared.backend, allocated
        original = backend.close
        monkeypatch.setattr(backend, "close", fail_close)

    try:
        with pytest.raises(type(primary)) as caught:
            if consumer == "api":
                async with server.lifespan(app):
                    await install(
                        app.state.lattice, [app.state.memory_store, app.state.evidence_store]
                    )
                    monkeypatch.setattr(server, "log", BrokenDiagnostics())
            else:
                await orch.start()
                await install(
                    orch._lattice, [orch._memory_store, orch._evidence_store, orch._audit_log]
                )
                monkeypatch.setattr(orch, "_log", BrokenDiagnostics())
                await orch.stop()
        assert caught.value is primary
        assert all(store._conn is None for store in stores)
        assert backend._db is None
        assert not factory._manager.generation.ready
        assert not factory._manager.generation.tokens
        with pytest.raises(RuntimeError, match="cleanup required"):
            await factory.acquire_lattice()
        if consumer == "api":
            assert app.state.lattice is app.state.memory_store is app.state.evidence_store is None
        else:
            assert not orch._started
            assert orch._lattice is orch._tool_registry is orch._tool_driver is None
    finally:
        if backend is not None:
            monkeypatch.setattr(backend, "close", original)
        await factory.reset_lattice()


@pytest.mark.parametrize("backend", ["sqlite", "fallback"])
async def test_real_ephemeral_consumers_keep_shared_rows_until_final_release(
    owners, monkeypatch, backend
):
    config = LatticeConfig(backend=backend, sqlite_path=":memory:")
    monkeypatch.setattr(factory, "get_config", lambda: config)
    a, b = owners(), owners()
    await a.start()
    await b.start()
    shared = a._lattice
    row = await shared.create_node("task", {"synthetic": True})
    await a.stop()
    assert b._lattice is shared
    assert await b._lattice.get_node(row) is not None
    await b.stop()
    assert factory._manager is None
    await a.start()
    assert a._lattice is not shared
    assert await a._lattice.get_node(row) is None
    await a.stop()


@pytest.mark.parametrize("consumer", ["api", "orchestrator"])
async def test_post_acquire_property_failure_releases_recorded_lease(owners, monkeypatch, consumer):
    primary = RuntimeError("borrowed handle access")
    leases = []
    module = server if consumer == "api" else orchestrator_module
    acquire = module.acquire_lattice

    class FailingBorrow:
        @property
        def lattice(self):
            raise primary

        async def release(self):
            await leases[-1].release()

    async def acquire_with_failing_borrow():
        leases.append(await acquire())
        return FailingBorrow()

    monkeypatch.setattr(module, "acquire_lattice", acquire_with_failing_borrow)
    try:
        with pytest.raises(RuntimeError) as caught:
            if consumer == "api":
                async with server.lifespan(server.create_app()):
                    pytest.fail("startup must fail")
            else:
                await owners().start()
        assert caught.value is primary
        assert factory._manager is None
    finally:
        for lease in leases:
            await lease.release()


@pytest.mark.parametrize("resource", ["evidence", "memory"])
async def test_api_normal_exit_surfaces_store_cleanup_failure(owners, monkeypatch, resource):
    app = server.create_app()
    primary = RuntimeError("store close")
    stores = []
    with pytest.raises(RuntimeError) as caught:
        async with server.lifespan(app):
            stores = [app.state.evidence_store, app.state.memory_store]
            store = stores[0 if resource == "evidence" else 1]
            close = store.close

            async def fail_close():
                await close()
                raise primary

            monkeypatch.setattr(store, "close", fail_close)
            monkeypatch.setattr(server, "log", BrokenDiagnostics())
    assert caught.value is primary
    assert all(store._conn is None for store in stores)
    assert factory._manager is None
    assert app.state.lattice is app.state.memory_store is app.state.evidence_store is None


@pytest.mark.parametrize("consumer", ["api", "orchestrator"])
async def test_cancelled_acquisition_waiter_does_not_release_peer(owners, monkeypatch, consumer):
    peer, waiting = owners(), owners()
    await peer.start()
    shared = peer._lattice
    entered = asyncio.Event()
    module = server if consumer == "api" else orchestrator_module
    acquire = module.acquire_lattice
    app = server.create_app()

    async def acquire_waiting():
        entered.set()
        return await acquire()

    async def run():
        if consumer == "api":
            async with server.lifespan(app):
                pytest.fail("cancelled startup cannot yield")
        else:
            await waiting.start()

    monkeypatch.setattr(module, "acquire_lattice", acquire_waiting)
    # The actual manager lock, not a replaced acquisition result.
    async with factory._manager.lock:
        task = asyncio.create_task(run())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert len(factory._manager.generation.tokens) == 1
    assert shared.backend._db is not None
    assert await shared.create_node("task", {"survivor": True})
    if consumer == "api":
        assert app.state.lattice is app.state.memory_store is app.state.evidence_store is None
    else:
        assert waiting._memory_store._conn is waiting._evidence_store._conn is None
        assert not waiting._started
        assert waiting._lattice is waiting._lattice_lease is None
    await peer.stop()
    assert factory._manager is None
