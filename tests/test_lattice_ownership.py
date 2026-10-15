"""Process-local resource ownership, never peer/security authority."""

from __future__ import annotations

import asyncio
import copy

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
