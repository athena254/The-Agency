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


async def assert_workers_closed(connections):
    for conn in connections:
        await asyncio.to_thread(conn._thread.join, 2)
        assert not conn._thread.is_alive(), "owned SQLite worker outlived final cleanup"


async def test_concurrent_real_starts_keep_one_lease_and_store_generation(owners, monkeypatch):
    orch = owners()
    stores = [orch._memory_store, orch._evidence_store, orch._audit_log]
    # Isolate the lifecycle race from unrelated non-serialized store initialization.
    for store in stores:
        await store.initialize()
    connections = [store._conn for store in stores]
    entered, proceed = asyncio.Event(), asyncio.Event()
    initialize = orch._memory_store.initialize
    acquire = orchestrator_module.acquire_lattice
    calls, leases, tasks = [], [], []

    async def gated_initialize():
        calls.append("memory")
        await initialize()
        entered.set()
        await proceed.wait()

    async def tracked_acquire():
        lease = await acquire()
        leases.append(lease)
        return lease

    monkeypatch.setattr(orch._memory_store, "initialize", gated_initialize)
    monkeypatch.setattr(orchestrator_module, "acquire_lattice", tracked_acquire)
    try:
        tasks.append(asyncio.create_task(orch.start()))
        await entered.wait()
        tasks.append(asyncio.create_task(orch.start()))
        # FIFO ready-queue barrier: the second call has reached its first await.
        checkpoint = asyncio.Event()
        asyncio.get_running_loop().call_soon(checkpoint.set)
        await checkpoint.wait()
        proceed.set()
        await asyncio.gather(*tasks)
        generation = factory._manager.generation
        shared = orch._lattice
        assert len(generation.tokens) == len(leases) == 1
        assert calls == ["memory"]
        assert [store._conn for store in stores] == connections
        assert orch._lattice_lease is leases[0]
        connections.append(shared.backend._db)
        row = await shared.create_node("task", {"concurrent": True})
        assert await shared.get_node(row) is not None
        await orch.stop()
        assert not generation.tokens
        assert shared.backend._db is None
        assert all(store._conn is None for store in stores)
        assert factory._manager is None
        await assert_workers_closed(connections)
    finally:
        proceed.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await orch.stop()
        # Retain every actual lease so the deliberately broken RED leaks no token.
        for lease in leases:
            await lease.release()
        for conn in connections:
            await conn.close()


@pytest.mark.parametrize(
    ("cancel_stop", "cleanup_error"), [(False, False), (True, False), (True, True)]
)
async def test_real_restart_waits_for_owned_shutdown_drain(
    owners, monkeypatch, cancel_stop, cleanup_error
):
    orch = owners()
    leases, tasks, connections = [], [], []
    acquire = orchestrator_module.acquire_lattice

    async def tracked_acquire():
        lease = await acquire()
        leases.append(lease)
        return lease

    monkeypatch.setattr(orchestrator_module, "acquire_lattice", tracked_acquire)
    await orch.start()
    shared = orch._lattice
    generation = factory._manager.generation
    row = await shared.create_node("task", {"restart": True})
    stores = [orch._memory_store, orch._evidence_store, orch._audit_log]
    connections.extend([shared.backend._db, *(store._conn for store in stores)])
    entered, proceed, restarting = (asyncio.Event() for _ in range(3))
    stop_periodic = orch._memory_lifecycle.stop_periodic

    async def gated_stop():
        monkeypatch.setattr(orch._memory_lifecycle, "stop_periodic", stop_periodic)
        entered.set()
        await proceed.wait()
        await stop_periodic()
        if cleanup_error:
            raise ValueError("secondary cleanup must not replace caller cancellation")

    async def restart():
        restarting.set()
        await orch.start()

    monkeypatch.setattr(orch._memory_lifecycle, "stop_periodic", gated_stop)
    try:
        stopping = asyncio.create_task(orch.stop())
        tasks.append(stopping)
        await entered.wait()
        if cancel_stop:
            for _ in range(2):
                stopping.cancel()
                checkpoint = asyncio.Event()
                asyncio.get_running_loop().call_soon(checkpoint.set)
                await checkpoint.wait()
                assert not stopping.done()
        starting = asyncio.create_task(restart())
        tasks.append(starting)
        await restarting.wait()
        overlapped = starting.done()
        proceed.set()
        if cancel_stop:
            with pytest.raises(asyncio.CancelledError):
                await stopping
        else:
            await stopping
        await starting
        assert orch._lattice_lease is leases[-1], "old cleanup erased the replacement lease"
        assert not overlapped, "restart ran before the preceding shutdown drained"
        assert orch._started
        assert all(store._conn is not None for store in stores)
        assert not generation.tokens
        assert shared.backend._db is None
        replacement = orch._lattice
        replacement_generation = factory._manager.generation
        assert replacement is not shared
        assert len(replacement_generation.tokens) == 1
        assert await replacement.get_node(row) is not None
        connections.extend([replacement.backend._db, *(store._conn for store in stores)])
        for store in stores:
            async with store._conn.execute("SELECT 1") as cursor:
                assert await cursor.fetchone() is not None
        await orch.stop()
        assert not replacement_generation.tokens
        assert replacement.backend._db is None
        assert all(store._conn is None for store in stores)
        assert factory._manager is None
        await assert_workers_closed(connections)
    finally:
        proceed.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await orch.stop()
        for lease in leases:
            await lease.release()
        for conn in connections:
            await conn.close()


@pytest.mark.parametrize(
    ("transition", "waiter", "cancel_waiter"),
    [
        ("start", "stop", False),
        ("stop", "stop", False),
        ("start", "start", True),
        ("start", "stop", True),
        ("stop", "start", True),
        ("stop", "stop", True),
    ],
)
async def test_real_queued_transitions_preserve_current_owner(
    owners, monkeypatch, transition, waiter, cancel_waiter
):
    orch = owners()
    stores = [orch._memory_store, orch._evidence_store, orch._audit_log]
    entered, proceed, attempted = (asyncio.Event() for _ in range(3))
    tasks, connections, leases, generations = [], [], [], []
    acquire = orchestrator_module.acquire_lattice

    async def tracked_acquire():
        lease = await acquire()
        leases.append(lease)
        generations.append(factory._manager.generation)
        connections.extend([lease.lattice.backend._db, *(store._conn for store in stores)])
        return lease

    def remember_stores():
        connections.extend(store._conn for store in stores if store._conn is not None)

    monkeypatch.setattr(orchestrator_module, "acquire_lattice", tracked_acquire)
    row = None
    if transition == "stop":
        await orch.start()
        row = await orch._lattice.create_node("task", {"queued": True})
        remember_stores()
        target, name = orch._memory_lifecycle, "stop_periodic"
    else:
        target, name = orch._memory_store, "initialize"
    original = getattr(target, name)

    async def gated_transition():
        if transition == "start":
            await original()
            remember_stores()
        entered.set()
        await proceed.wait()
        if transition == "stop":
            await original()

    async def queued_transition():
        attempted.set()
        await getattr(orch, waiter)()

    monkeypatch.setattr(target, name, gated_transition)
    try:
        owner = asyncio.create_task(getattr(orch, transition)())
        tasks.append(owner)
        await entered.wait()
        waiting = asyncio.create_task(queued_transition())
        tasks.append(waiting)
        await attempted.wait()
        assert not waiting.done(), "queued lifecycle operation bypassed the active transition"
        if cancel_waiter:
            waiting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiting
            assert not owner.done()
        proceed.set()
        await owner
        if not cancel_waiter:
            await waiting
        active = (transition if cancel_waiter else waiter) == "start"
        assert orch._started is active
        remember_stores()
        if active:
            assert orch._lattice_lease is leases[-1]
            assert len(generations[-1].tokens) == 1
            assert all(store._conn is not None for store in stores)
        else:
            assert orch._lattice_lease is orch._lattice is None
            assert all(store._conn is None for store in stores)
            assert factory._manager is None
        await orch.start()
        remember_stores()
        assert len(factory._manager.generation.tokens) == 1
        if row is None:
            row = await orch._lattice.create_node("task", {"queued": True})
        assert await orch._lattice.get_node(row) is not None
        for store in stores:
            async with store._conn.execute("SELECT 1") as cursor:
                assert await cursor.fetchone() is not None
        await orch.stop()
        assert all(not generation.tokens for generation in generations)
        assert factory._manager is None
        await assert_workers_closed(connections)
    finally:
        proceed.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await orch.stop()
        for lease in leases:
            await lease.release()
        for conn in connections:
            await conn.close()
        await assert_workers_closed(connections)


@pytest.mark.parametrize("cancel_primary", [False, True])
async def test_real_startup_rollback_drains_before_queued_restart(
    owners, monkeypatch, cancel_primary
):
    orch = owners()
    primary = asyncio.CancelledError("startup") if cancel_primary else RuntimeError("startup")
    entered, proceed, attempted = (asyncio.Event() for _ in range(3))
    tasks, leases, connections, generations = [], [], [], []
    acquire = orchestrator_module.acquire_lattice
    build = orch._build_tool_layer
    stop_periodic = orch._memory_lifecycle.stop_periodic
    stores = [orch._memory_store, orch._evidence_store, orch._audit_log]

    async def tracked_acquire():
        lease = await acquire()
        leases.append(lease)
        generations.append(factory._manager.generation)
        connections.extend([lease.lattice.backend._db, *(store._conn for store in stores)])
        return lease

    def fail_tools_once():
        monkeypatch.setattr(orch, "_build_tool_layer", build)
        raise primary

    async def gated_cleanup():
        monkeypatch.setattr(orch._memory_lifecycle, "stop_periodic", stop_periodic)
        entered.set()
        await proceed.wait()
        await stop_periodic()
        raise ValueError("secondary cleanup")

    async def restart():
        attempted.set()
        await orch.start()

    monkeypatch.setattr(orchestrator_module, "acquire_lattice", tracked_acquire)
    monkeypatch.setattr(orch, "_build_tool_layer", fail_tools_once)
    monkeypatch.setattr(orch._memory_lifecycle, "stop_periodic", gated_cleanup)
    try:
        failing = asyncio.create_task(orch.start())
        tasks.append(failing)
        await entered.wait()
        starting = asyncio.create_task(restart())
        tasks.append(starting)
        await attempted.wait()
        for _ in range(2):
            failing.cancel()
            checkpoint = asyncio.Event()
            asyncio.get_running_loop().call_soon(checkpoint.set)
            await checkpoint.wait()
            assert not failing.done()
            assert not starting.done()
        proceed.set()
        with pytest.raises(type(primary)) as caught:
            await failing
        assert caught.value is primary
        await starting
        assert not generations[0].tokens
        assert leases[0].lattice.backend._db is None
        assert orch._lattice_lease is leases[1]
        assert len(generations[1].tokens) == 1
        assert orch._started
        assert all(store._conn is not None for store in stores)
        row = await orch._lattice.create_node("task", {"rollback": True})
        assert await orch._lattice.get_node(row) is not None
        await orch.stop()
        assert all(not generation.tokens for generation in generations)
        assert factory._manager is None
        await assert_workers_closed(connections)
    finally:
        proceed.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await orch.stop()
        for lease in leases:
            await lease.release()
        for conn in connections:
            await conn.close()
        await assert_workers_closed(connections)


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
