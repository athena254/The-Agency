"""Lifecycle regression tests: same-instance initialize/close races."""

import asyncio

import aiosqlite
import pytest

from agency.kernel.audit import AuditEntry, AuditLog, BetaAuditLog


async def test_concurrent_initialize_single_connection(tmp_path, monkeypatch):
    real_connect = aiosqlite.connect
    calls = 0

    def counting_connect(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(aiosqlite, "connect", counting_connect)
    log = AuditLog(tmp_path / "audit.db")
    try:
        import asyncio

        await asyncio.gather(*(log.initialize() for _ in range(3)))
        assert calls == 1, f"expected 1 connection, opened {calls}"
    finally:
        await log.close()


async def test_initialize_vs_close_never_half_published(tmp_path, monkeypatch):
    """Close racing a blocked initialize leaves closed XOR fully ready."""
    real_execute = aiosqlite.Connection.execute
    entered = asyncio.Event()
    release = asyncio.Event()

    async def gate_execute(self, sql, *args, **kwargs):
        if sql == "PRAGMA journal_mode=WAL" and not entered.is_set():
            entered.set()
            await release.wait()
        return await real_execute(self, sql, *args, **kwargs)

    monkeypatch.setattr(aiosqlite.Connection, "execute", gate_execute)
    log = AuditLog(tmp_path / "audit.db")
    init_task = asyncio.ensure_future(log.initialize())
    assert await asyncio.wait_for(entered.wait(), timeout=10) is True
    await asyncio.sleep(0)
    close_task = asyncio.ensure_future(log.close())
    await asyncio.sleep(0)
    release.set()
    await asyncio.wait_for(asyncio.gather(init_task, close_task), timeout=15)
    if log._conn is None:
        with pytest.raises(RuntimeError):
            await log.append(AuditEntry(agent="x", result="allowed"))
    else:
        eid = await log.append(AuditEntry(agent="x", result="allowed"))
        assert (await log.query())[0].entry_id == eid
        await log.close()
        assert log._conn is None


async def test_repeated_close_idempotent(tmp_path):
    log = AuditLog(tmp_path / "audit.db")
    await log.close()
    await log.initialize()
    await log.close()
    await log.close()
    await asyncio.gather(*(log.close() for _ in range(3)))
    assert log._conn is None


async def test_failed_initialize_closes_unpublished(tmp_path, monkeypatch):
    real_execute = aiosqlite.Connection.execute
    closed: list[aiosqlite.Connection] = []
    orig_close = aiosqlite.Connection.close

    async def track_close(self):
        closed.append(self)
        await orig_close(self)

    async def fail_schema(self, sql, *args, **kwargs):
        if isinstance(sql, str) and sql.lstrip().startswith("CREATE TABLE"):
            raise RuntimeError("boom-schema")
        return await real_execute(self, sql, *args, **kwargs)

    monkeypatch.setattr(aiosqlite.Connection, "close", track_close)
    monkeypatch.setattr(aiosqlite.Connection, "execute", fail_schema)
    log = AuditLog(tmp_path / "audit.db")
    with pytest.raises(RuntimeError, match="boom-schema"):
        await log.initialize()
    assert log._conn is None
    assert len(closed) == 1


async def test_close_commit_failure_not_acknowledged(tmp_path, monkeypatch):
    log = AuditLog(tmp_path / "audit.db")
    await log.initialize()
    conn = log._conn
    assert conn is not None

    async def fail_commit():
        raise RuntimeError("boom-commit")

    monkeypatch.setattr(conn, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="boom-commit"):
        await log.close()
    assert log._conn is conn
    monkeypatch.undo()
    await log.close()
    assert log._conn is None


async def test_close_underlying_failure_not_acknowledged(tmp_path, monkeypatch):
    log = AuditLog(tmp_path / "audit.db")
    await log.initialize()
    conn = log._conn
    assert conn is not None

    async def fail_close():
        raise RuntimeError("boom-close")

    monkeypatch.setattr(conn, "close", fail_close)
    with pytest.raises(RuntimeError, match="boom-close"):
        await log.close()
    assert log._conn is conn
    monkeypatch.undo()
    await log.close()
    assert log._conn is None


async def test_beta_close_failure_not_durable_ack(tmp_path, monkeypatch):
    log = BetaAuditLog(tmp_path / "audit.db")
    await log.initialize()
    eid = await log.append_beta(AuditEntry(agent="b", result="allowed"))
    assert eid
    conn = log._conn
    assert conn is not None

    async def fail_commit():
        raise RuntimeError("boom-beta-commit")

    monkeypatch.setattr(conn, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="boom-beta-commit"):
        await log.close()
    assert log._conn is conn
    monkeypatch.undo()
    await log.close()
    assert log._conn is None


async def test_cancel_initialize_no_leak(tmp_path, monkeypatch):
    real_execute = aiosqlite.Connection.execute
    closed: list[aiosqlite.Connection] = []
    orig_close = aiosqlite.Connection.close
    entered = asyncio.Event()
    release = asyncio.Event()

    async def track_close(self):
        closed.append(self)
        await orig_close(self)

    async def gate_execute(self, sql, *args, **kwargs):
        if sql == "PRAGMA journal_mode=WAL" and not entered.is_set():
            entered.set()
            await release.wait()
        return await real_execute(self, sql, *args, **kwargs)

    monkeypatch.setattr(aiosqlite.Connection, "close", track_close)
    monkeypatch.setattr(aiosqlite.Connection, "execute", gate_execute)
    log = AuditLog(tmp_path / "audit.db")
    task = asyncio.ensure_future(log.initialize())
    assert await asyncio.wait_for(entered.wait(), timeout=10) is True
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert log._conn is None
    assert len(closed) == 1
