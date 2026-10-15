"""Process-local resource ownership, never peer/security authority."""

from __future__ import annotations

import asyncio
import copy
import threading

import pytest

from agency.lattice import factory
from agency.lattice.api import Lattice
from agency.lattice.models import LatticeConfig


@pytest.fixture(autouse=True)
async def clean_manager():
    await factory.reset_lattice()
    yield
    await factory.reset_lattice()


@pytest.mark.parametrize("backend", ["sqlite", "fallback"])
@pytest.mark.parametrize("persistent", [False, True])
async def test_last_owner_only_shares_state_and_retires(tmp_path, backend, persistent):
    config = LatticeConfig(
        backend=backend, sqlite_path=str(tmp_path / "shared.db") if persistent else ":memory:"
    )
    first = await factory.acquire_lattice(config)
    second = await factory.acquire_lattice(config)
    assert first.lattice is second.lattice
    node = await first.lattice.create_node("task", {"synthetic": True})
    await asyncio.gather(first.release(), copy.copy(first).release(), first.release())
    assert await second.lattice.get_node(node) is not None
    with pytest.raises(RuntimeError, match="lease|managed|ownership"):
        await second.lattice.close()
    with pytest.raises(RuntimeError, match="active|lease"):
        await factory.reset_lattice()
    await second.release()
    replacement = await factory.acquire_lattice(config)
    assert replacement.lattice is not first.lattice
    assert (await replacement.lattice.get_node(node) is not None) == (
        persistent and backend == "sqlite"
    )
    await first.release()
    assert await replacement.lattice.create_node("task", {})
    await replacement.release()


async def test_legacy_pin_and_stable_configuration(tmp_path):
    config = LatticeConfig(sqlite_path=":memory:")
    first = await factory.acquire_lattice(config)
    legacy = await factory.get_lattice(config)
    assert await factory.get_lattice() is legacy is first.lattice
    config.sqlite_path = str(tmp_path / "wrong.db")
    assert legacy.config.sqlite_path == ":memory:"
    with pytest.raises(ValueError, match="config"):
        await factory.acquire_lattice(config)
    await first.release()
    assert await factory.get_lattice() is legacy
    assert await legacy.create_node("task", {})
    await factory.reset_lattice()
    assert await factory.get_lattice(LatticeConfig(sqlite_path=":memory:")) is not legacy


async def test_standalone_remains_caller_owned():
    lattice = Lattice(LatticeConfig(sqlite_path=":memory:"))
    await lattice.initialize()
    await lattice.close()
    await lattice.close()


async def queued(task_call):
    """Start a caller and cross its first suspension without timing sleeps."""
    entered = asyncio.Event()

    async def run():
        entered.set()
        return await task_call()

    task = asyncio.create_task(run())
    await entered.wait()
    return task


@pytest.mark.parametrize("cancel_legacy", [False, True])
async def test_ready_only_publication_and_cancelled_waiter(monkeypatch, cancel_legacy):
    from agency.lattice.backends.sqlite import SQLiteLattice

    entered, proceed = asyncio.Event(), asyncio.Event()
    original = SQLiteLattice.initialize
    candidates = []

    async def initialize(self):
        candidates.append(self)
        entered.set()
        await proceed.wait()
        await original(self)

    monkeypatch.setattr(SQLiteLattice, "initialize", initialize)
    first = asyncio.create_task(factory.acquire_lattice(LatticeConfig(sqlite_path=":memory:")))
    await entered.wait()
    waiter = await queued(factory.acquire_lattice)
    cancelled = await queued(factory.get_lattice if cancel_legacy else factory.acquire_lattice)
    assert not first.done() and not waiter.done() and not cancelled.done()
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    with pytest.raises(RuntimeError, match="active|initializ"):
        await factory.reset_lattice()
    proceed.set()
    a, b = await asyncio.gather(first, waiter)
    assert a.lattice is b.lattice and len(candidates) == 1
    await a.release()
    await b.release()
    # A cancelled legacy getter must not leak its pin.
    assert factory._manager is None


async def test_initializer_failure_never_publishes(monkeypatch):
    from agency.lattice.backends.sqlite import SQLiteLattice

    original = SQLiteLattice.initialize
    candidates = []

    async def initialize(self):
        candidates.append(self)
        await original(self)
        if len(candidates) == 1:
            raise LookupError("synthetic startup")

    monkeypatch.setattr(SQLiteLattice, "initialize", initialize)
    with pytest.raises(LookupError, match="synthetic startup"):
        await factory.get_lattice(LatticeConfig(sqlite_path=":memory:"))
    assert candidates[0]._db is None
    lease = await factory.acquire_lattice(LatticeConfig(sqlite_path=":memory:"))
    assert lease.lattice.backend is candidates[1]
    await lease.release()
    assert factory._manager is None


async def test_repeated_initializer_cancellation_drains_cleanup(monkeypatch):
    from agency.lattice.backends.sqlite import SQLiteLattice

    initialized, close_entered, finish_close = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original_init, original_close = SQLiteLattice.initialize, SQLiteLattice.close
    candidates = []

    async def initialize(self):
        candidates.append(self)
        await original_init(self)
        if len(candidates) == 1:
            initialized.set()
            await asyncio.Event().wait()

    async def close(self):
        if self is candidates[0]:
            close_entered.set()
            await finish_close.wait()
        await original_close(self)

    monkeypatch.setattr(SQLiteLattice, "initialize", initialize)
    monkeypatch.setattr(SQLiteLattice, "close", close)
    task = asyncio.create_task(factory.acquire_lattice(LatticeConfig(sqlite_path=":memory:")))
    await initialized.wait()
    waiter = await queued(lambda: factory.acquire_lattice(LatticeConfig(sqlite_path=":memory:")))
    task.cancel()
    await close_entered.wait()
    task.cancel()
    # Cross another task turn after delivering the second cancellation.
    await queued(lambda: asyncio.sleep(0))
    try:
        assert not task.done() and len(candidates) == 1 and not waiter.done()
    finally:
        finish_close.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        result = await asyncio.wait_for(waiter, 3)
        await result.release()
    assert candidates[0]._db is None


@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.parametrize("retire_with_reset", [False, True])
async def test_final_close_quarantines_and_blocks_queued_acquire(
    monkeypatch, cancel, retire_with_reset
):
    from agency.lattice.backends.sqlite import SQLiteLattice

    lease = await factory.acquire_lattice(LatticeConfig(sqlite_path=":memory:"))
    if retire_with_reset:
        assert await factory.get_lattice() is lease.lattice
        await lease.release()
    entered, proceed = asyncio.Event(), asyncio.Event()
    original = SQLiteLattice.close
    attempts = 0

    async def close(self):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            entered.set()
            await proceed.wait()
            if not cancel:
                raise OSError("synthetic close")
        await original(self)

    monkeypatch.setattr(SQLiteLattice, "close", close)
    release = asyncio.create_task(factory.reset_lattice() if retire_with_reset else lease.release())
    await entered.wait()
    waiter = await queued(factory.acquire_lattice)
    assert not waiter.done()
    if cancel:
        release.cancel()
        await queued(lambda: asyncio.sleep(0))
        release.cancel()
        await queued(lambda: asyncio.sleep(0))
        assert not release.done() and not waiter.done()
    proceed.set()
    with pytest.raises(asyncio.CancelledError if cancel else OSError):
        await release
    with pytest.raises(RuntimeError, match="cleanup"):
        await waiter
    if cancel:
        assert lease.lattice.backend._db is None
    await lease.release()  # consumed token must not retry the failed close
    assert attempts == 1
    await factory.reset_lattice()
    assert attempts == 2
    with pytest.raises(RuntimeError, match="[Mm]anaged"):
        await lease.lattice.initialize()
    fresh = await factory.acquire_lattice(LatticeConfig(sqlite_path=":memory:"))
    assert fresh.lattice is not lease.lattice
    await fresh.release()


async def test_primary_startup_error_survives_failed_cleanup(monkeypatch):
    from agency.lattice.backends.sqlite import SQLiteLattice

    original_init, original_close = SQLiteLattice.initialize, SQLiteLattice.close
    original_error = LookupError("synthetic primary")

    async def initialize(self):
        await original_init(self)
        raise original_error

    async def close(self):
        raise OSError("synthetic cleanup")

    monkeypatch.setattr(SQLiteLattice, "initialize", initialize)
    monkeypatch.setattr(SQLiteLattice, "close", close)
    with pytest.raises(LookupError) as caught:
        await factory.acquire_lattice(LatticeConfig(sqlite_path=":memory:"))
    assert caught.value is original_error
    with pytest.raises(RuntimeError, match="cleanup"):
        await factory.acquire_lattice()
    monkeypatch.setattr(SQLiteLattice, "close", original_close)
    await factory.reset_lattice()


@pytest.mark.parametrize("phase", ["pragma", "migration"])
@pytest.mark.parametrize("cancel", [False, True])
async def test_sqlite_init_failure_rolls_back_actual_connection(monkeypatch, phase, cancel):
    import aiosqlite

    from agency.lattice.backends.sqlite import SQLiteLattice

    backend = SQLiteLattice(":memory:")
    original = aiosqlite.Connection.execute
    connections = []

    async def execute(self, sql, *args, **kwargs):
        if self not in connections:
            connections.append(self)
        if (phase == "pragma" and sql.startswith("PRAGMA")) or (
            phase == "migration" and sql.startswith("CREATE TABLE")
        ):
            raise asyncio.CancelledError() if cancel else ValueError("synthetic schema")
        return await original(self, sql, *args, **kwargs)

    monkeypatch.setattr(aiosqlite.Connection, "execute", execute)
    with pytest.raises(asyncio.CancelledError if cancel else RuntimeError):
        await backend.initialize()
    try:
        assert backend._db is None
        assert connections and all(c._connection is None for c in connections)
    finally:
        for connection in connections:
            await connection.close()
    monkeypatch.setattr(aiosqlite.Connection, "execute", original)
    await backend.initialize()
    assert await backend.create_node("task", {})
    await backend.close()


async def test_real_aiosqlite_connect_cancellation_is_drained(monkeypatch):
    import sqlite3
    import threading

    import aiosqlite

    from agency.lattice.backends.sqlite import SQLiteLattice

    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    finish = threading.Event()
    connections = []
    workers = []
    original = sqlite3.connect
    original_async_connect = aiosqlite.connect

    def async_connect(*args, **kwargs):
        connection = original_async_connect(*args, **kwargs)
        workers.append(connection)
        return connection

    class TrackingConnection(sqlite3.Connection):
        closed = False

        def close(self):
            super().close()
            self.closed = True

    def connect(*args, **kwargs):
        loop.call_soon_threadsafe(entered.set)
        if not finish.wait(3):
            raise TimeoutError("test connect barrier")
        connection = original(*args, factory=TrackingConnection, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", connect)
    monkeypatch.setattr(aiosqlite, "connect", async_connect)
    backend = SQLiteLattice(":memory:")
    task = asyncio.create_task(backend.initialize())
    await asyncio.wait_for(entered.wait(), 3)
    task.cancel()
    await queued(lambda: asyncio.sleep(0))
    task.cancel()
    await queued(lambda: asyncio.sleep(0))
    assert not task.done()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 3)
    # A raw sqlite connection completed in the actual worker must be closed.
    assert len(connections) == 1 and connections[0].closed
    assert backend._db is None
    assert len(workers) == 1
    await asyncio.to_thread(workers[0]._thread.join, 3)
    assert not workers[0]._thread.is_alive()


async def test_explicit_loop_ownership_then_fresh_loop():
    lease = await factory.acquire_lattice(LatticeConfig(sqlite_path=":memory:"))

    async def foreign():
        for operation in (
            factory.acquire_lattice,
            factory.get_lattice,
            factory.reset_lattice,
            lease.release,
        ):
            with pytest.raises(RuntimeError, match="event loop"):
                await operation()

    # Bind both asyncio locks through real contention before the foreign call.
    manager = factory._manager
    assert manager is not None
    backend_lock = lease.lattice.backend._lock
    await backend_lock.acquire()
    operation = await queued(lambda: lease.lattice.create_node("task", {}))
    await manager.lock.acquire()
    waiter = await queued(factory.acquire_lattice)
    try:
        await asyncio.to_thread(lambda: asyncio.run(foreign()))
    finally:
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        manager.lock.release()
        backend_lock.release()
        await operation
    assert await lease.lattice.create_node("task", {})
    await lease.release()

    async def fresh():
        new = await factory.acquire_lattice(LatticeConfig(sqlite_path=":memory:"))
        assert await new.lattice.create_node("task", {})
        await new.release()

    await asyncio.to_thread(lambda: asyncio.run(fresh()))


async def test_lease_copies_cannot_duplicate_or_detach_ownership():
    lease = await factory.acquire_lattice(LatticeConfig(sqlite_path=":memory:"))
    try:
        assert copy.copy(lease) is lease
        assert copy.deepcopy(lease) is lease
        with pytest.raises(TypeError, match="acquire_lattice"):
            factory.LatticeLease()
    finally:
        await lease.release()


async def test_concurrent_final_release_closes_once(monkeypatch):
    from agency.lattice.backends.sqlite import SQLiteLattice

    lease = await factory.acquire_lattice(LatticeConfig(sqlite_path=":memory:"))
    entered, proceed = asyncio.Event(), asyncio.Event()
    original = SQLiteLattice.close
    calls = 0

    async def close(self):
        nonlocal calls
        calls += 1
        entered.set()
        await proceed.wait()
        await original(self)

    monkeypatch.setattr(SQLiteLattice, "close", close)
    release = asyncio.create_task(lease.release())
    await entered.wait()
    duplicate = await queued(lease.release)
    assert not duplicate.done() and calls == 1
    proceed.set()
    await asyncio.gather(release, duplicate)
    assert calls == 1


async def test_sqlite_rollback_failure_preserves_startup_error(monkeypatch):
    import aiosqlite

    from agency.lattice.backends.sqlite import SQLiteLattice

    original_execute, original_close = aiosqlite.Connection.execute, aiosqlite.Connection.close
    backend = SQLiteLattice(":memory:")
    primary = ValueError("synthetic primary")

    async def execute(self, *args, **kwargs):
        raise primary

    async def close(self):
        raise OSError("synthetic rollback")

    monkeypatch.setattr(aiosqlite.Connection, "execute", execute)
    monkeypatch.setattr(aiosqlite.Connection, "close", close)
    with pytest.raises(RuntimeError) as caught:
        await backend.initialize()
    assert caught.value.__cause__ is primary
    assert backend._db is not None
    monkeypatch.setattr(aiosqlite.Connection, "execute", original_execute)
    monkeypatch.setattr(aiosqlite.Connection, "close", original_close)
    await backend.close()


async def test_actual_sqlite_connect_failure_is_clean(tmp_path):
    from agency.lattice.backends.sqlite import SQLiteLattice

    # sqlite cannot open a directory as a database file.
    backend = SQLiteLattice(str(tmp_path))
    with pytest.raises(RuntimeError, match="initialize"):
        await backend.initialize()
    assert backend._db is None
    await backend.close()


async def test_sqlite_repeated_cancellation_keeps_rollback_exclusive(monkeypatch):
    import aiosqlite

    from agency.lattice.backends.sqlite import SQLiteLattice

    backend = SQLiteLattice(":memory:")
    schema_entered, close_entered, finish_close = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original_execute, original_close = aiosqlite.Connection.execute, aiosqlite.Connection.close
    connections = []

    async def execute(self, sql, *args, **kwargs):
        if self not in connections:
            connections.append(self)
        result = await original_execute(self, sql, *args, **kwargs)
        if len(connections) == 1 and sql.startswith("CREATE TABLE"):
            schema_entered.set()
            await asyncio.Event().wait()
        return result

    async def close(self):
        if self is connections[0]:
            close_entered.set()
            await finish_close.wait()
        await original_close(self)

    monkeypatch.setattr(aiosqlite.Connection, "execute", execute)
    monkeypatch.setattr(aiosqlite.Connection, "close", close)
    task = asyncio.create_task(backend.initialize())
    await schema_entered.wait()
    waiter = await queued(backend.initialize)
    task.cancel()
    await close_entered.wait()
    task.cancel()
    await queued(lambda: asyncio.sleep(0))
    try:
        assert not task.done() and not waiter.done() and len(connections) == 1
    finally:
        finish_close.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(waiter, 3)
        await backend.close()
    assert all(c._connection is None for c in connections)


def test_simultaneous_cold_start_has_one_process_owner(monkeypatch):
    from agency.lattice.backends.sqlite import SQLiteLattice

    start = threading.Barrier(2)
    acquired = threading.Barrier(2)
    contender = threading.Event()
    mutex = threading.Lock()
    successes, failures, initialized, connections = [], [], [], []
    original_manager = factory._Manager
    original_initialize = SQLiteLattice.initialize

    class ObservedLock:
        """Expose actual contention without sleeping or a constructor deadlock."""

        def __enter__(self):
            if not mutex.acquire(blocking=False):
                contender.set()
                assert mutex.acquire(timeout=3), "installation lock did not drain"
            return self

        def __exit__(self, *args):
            mutex.release()

    def manager(loop):
        # Before the repair the second constructor signals this gate; after it,
        # the second thread contends on the real installation critical section.
        if not contender.is_set():
            if not mutex.locked():
                with mutex:
                    first = not initialized
                    initialized.append("constructing")
                if not first:
                    contender.set()
            if not contender.is_set():
                assert contender.wait(3), "cold-start contender never arrived"
        return original_manager(loop)

    async def initialize(self):
        await original_initialize(self)
        connections.append(self._db)

    monkeypatch.setattr(factory, "_manager_lock", ObservedLock(), raising=False)
    monkeypatch.setattr(factory, "_Manager", manager)
    monkeypatch.setattr(SQLiteLattice, "initialize", initialize)

    async def worker():
        lease = None
        try:
            start.wait(timeout=3)
            try:
                lease = await factory.acquire_lattice(LatticeConfig(sqlite_path=":memory:"))
                successes.append(lease.lattice)
                node = await lease.lattice.create_node("task", {"cold": True})
                assert await lease.lattice.get_node(node) is not None
            except RuntimeError as exc:
                failures.append(str(exc))
            finally:
                await asyncio.to_thread(acquired.wait, 3)
        finally:
            if lease is not None:
                await lease.release()

    errors = []

    def run():
        try:
            asyncio.run(worker())
        except BaseException as exc:  # noqa: BLE001 - propagate worker failures in the test thread
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(8)
    assert all(not thread.is_alive() for thread in threads)
    for connection in connections:
        connection._thread.join(3)
        assert connection._connection is None and not connection._thread.is_alive()
    assert not errors, errors
    assert len(successes) == 1, "multiple live process-local generations were published"
    assert len(connections) == 1, "foreign loop reached backend initialization"
    assert len(failures) == 1 and "event loop" in failures[0]
    assert factory._manager is None


@pytest.mark.parametrize("backend", ["sqlite", "fallback"])
@pytest.mark.parametrize("fallback_engines", [False, True])
async def test_cached_managed_operations_reject_foreign_loop_before_effects(
    monkeypatch, backend, fallback_engines
):
    import inspect

    from agency.lattice import api

    if fallback_engines:
        monkeypatch.setattr(api, "_optional_engine", lambda *args: None)
    lease = await factory.acquire_lattice(LatticeConfig(backend=backend, sqlite_path=":memory:"))
    cached = lease.lattice
    governance, reputation = cached.governance, cached.reputation
    node = await cached.create_node("task", {"status": "open"})
    proposal = await cached.submit_proposal("synthetic", "policy_change", {})
    args = {
        "create_node": ("task", {}),
        "get_node": (node,),
        "update_node": (node, {"foreign": True}),
        "delete_node": (node,),
        "find_nodes": (),
        "add_edge": (node, node, "depends_on"),
        "get_edges": (node,),
        "remove_edge": ("absent",),
        "traverse": (node,),
        "find_path": (node, node),
        "get_dependencies": (node,),
        "get_dependents": (node,),
        "upsert_vector": ("memory", [("v", [1.0], {})]),
        "search_vectors": ("memory", [1.0]),
        "search_by_text": ("memory", ""),
        "delete_vectors": ("memory", ["v"]),
        "submit_proposal": ("synthetic", "policy_change", {}),
        "cast_vote": ("synthetic", proposal, "abstain"),
        "list_open_proposals": (),
        "get_proposal_status": (proposal,),
        "update_reputation": ("synthetic", True),
        "get_reputation": ("synthetic",),
        "check_quorum": (proposal,),
        "get_events": (),
        "replay_events": (),
        "get_audit_trail": (node,),
        "get_status": (),
        "get_agent_count": (),
        "get_active_tasks": (),
        "register_agent": ("synthetic", "worker", []),
        "create_task": ("synthetic", "test", {}),
        "complete_task": (node, {}),
        "link_evidence": (node, node),
    }
    public_data = {
        name
        for name, method in inspect.getmembers(Lattice, inspect.iscoroutinefunction)
        if not name.startswith("_") and name not in {"initialize", "close"}
    }
    assert set(args) == public_data
    calls = [(name, getattr(cached, name), values) for name, values in args.items()]
    engine_proposal = await governance.submit_proposal("synthetic", "policy_change", {})
    for name, values in {
        "submit_proposal": ("synthetic", "policy_change", {}),
        "cast_vote": ("synthetic", engine_proposal, "abstain"),
        "get_proposal_status": (engine_proposal,),
        "check_quorum": (engine_proposal,),
        **(
            {"resolve_proposal": (engine_proposal,), "list_open_proposals": ()}
            if not fallback_engines
            else {}
        ),
    }.items():
        calls.append(("governance." + name, getattr(governance, name), values))
    rep_args = {"get_reputation": ("synthetic",)}
    if fallback_engines:
        rep_args["update_reputation"] = ("synthetic", True)
    else:
        rep_args.update(
            {
                "record_task_completion": ("synthetic", True),
                "submit_peer_rating": ("rater", "synthetic", 0.5),
                "get_leaderboard": (),
                "vote_weight": ("user",),
            }
        )
    for name, values in rep_args.items():
        calls.append(("reputation." + name, getattr(reputation, name), values))
    if not fallback_engines:
        nested = governance.reputation
        calls.append(("governance.reputation.vote_weight", nested.vote_weight, ("user",)))
    if fallback_engines:
        await reputation.update_reputation("synthetic", True)
    else:
        await reputation.record_task_completion("synthetic", True)
    before = [event.to_dict() for event in await cached.get_events()]
    before_proposal = (await governance.get_proposal_status(engine_proposal)).to_dict()
    before_rep = (await reputation.get_reputation("synthetic")).to_dict()
    lock = getattr(cached.backend, "_lock", None)
    connection = getattr(cached.backend, "_db", None)

    async def foreign():
        outcomes = []
        if lock is not None:
            await lock.acquire()
            writes = [await queued(lambda: cached.create_node("task", {})) for _ in range(2)]
            lock.release()
            outcomes.extend(
                zip(["contended write"] * 2, await asyncio.gather(*writes, return_exceptions=True))
            )
        for name, method, values in calls:
            try:
                result = await method(*values)
            except Exception as exc:  # noqa: BLE001 - assert every foreign operation outcome
                result = exc
            outcomes.append((name, result))
        return [
            (name, repr(result))
            for name, result in outcomes
            if not isinstance(result, RuntimeError) or "event loop" not in str(result)
        ]

    try:
        violations = await asyncio.to_thread(lambda: asyncio.run(foreign()))
        assert not violations, violations
        assert [event.to_dict() for event in await cached.get_events()] == before
        assert (await governance.get_proposal_status(engine_proposal)).to_dict() == before_proposal
        assert (await reputation.get_reputation("synthetic")).to_dict() == before_rep
        if lock is not None:
            assert lock._loop is None
            await lock.acquire()
            writer = await queued(lambda: cached.create_node("task", {"owner": True}))
            reader = await queued(lambda: cached.get_node(node))
            lock.release()
            written, read = await asyncio.gather(writer, reader)
            assert read is not None and await cached.get_node(written) is not None
            assert lock._loop is asyncio.get_running_loop()
        else:
            written, read = await asyncio.gather(
                cached.create_node("task", {"owner": True}), cached.get_node(node)
            )
            assert read is not None and await cached.get_node(written) is not None
    finally:
        await lease.release()
        if connection is not None:
            await asyncio.to_thread(connection._thread.join, 3)
            assert not connection._thread.is_alive()


@pytest.mark.parametrize("fallback_engines", [False, True])
async def test_managed_engine_preserves_default_quorum(monkeypatch, fallback_engines):
    from agency.lattice import api

    if fallback_engines:
        monkeypatch.setattr(api, "_optional_engine", lambda *args: None)
    lease = await factory.acquire_lattice(LatticeConfig(backend="fallback"))
    try:
        proposal = await lease.lattice.governance.submit_proposal("synthetic", "policy_change", {})
        status = await lease.lattice.governance.get_proposal_status(proposal)
        assert status.quorum_required == (0.5 if fallback_engines else 0.66)
    finally:
        await lease.release()


async def test_managed_reputation_preserves_score_helper():
    lease = await factory.acquire_lattice(LatticeConfig(backend="fallback"))
    try:
        reputation = lease.lattice.reputation
        rep = await reputation.record_task_completion("synthetic", True)
        assert reputation.compute_score(rep) == rep.score

        def foreign():
            async def run():
                with pytest.raises(RuntimeError, match="event loop"):
                    reputation.compute_score(rep)

            asyncio.run(run())

        await asyncio.to_thread(foreign)
    finally:
        await lease.release()
