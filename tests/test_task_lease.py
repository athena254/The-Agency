"""Task claim leases: ownership stamp, heartbeat renewal, lease-aware recovery.

gaps.md #19 (a): startup recovery must not requeue a second live process's
running tasks. Claims carry an owner id + expiry; the dispatcher heartbeats
in-flight tasks; ``requeue_running(expired_only=True)`` requeues only expired
(or pre-lease) claims and leaves live claimers alone.
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta

import pytest

from agency.kernel.task_store import TaskStore
from agency.kernel.tasks import TaskManager, TaskStatus


def _lease_row(db: str, task_id: str) -> tuple[str | None, str | None]:
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT lease_owner, lease_expires_at FROM tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        assert row is not None
        return row[0], row[1]
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_claim_stamps_owner_and_lease_expiry(tmp_path) -> None:
    db = str(tmp_path / "tasks.db")
    async with TaskStore(db, owner_id="proc-a", lease_seconds=60) as store:
        manager = TaskManager(store)
        task = await manager.create_task(title="t", created_by="u")
        claimed = await manager.take_next_pending()
        assert claimed is not None
        assert claimed.status is TaskStatus.RUNNING
        owner, expires = _lease_row(db, task.task_id)
        assert owner == "proc-a"
        assert expires is not None
        expiry = datetime.fromisoformat(expires)
        now = datetime.now(expiry.tzinfo)
        assert now + timedelta(seconds=50) < expiry < now + timedelta(seconds=70)


@pytest.mark.asyncio
async def test_heartbeat_renews_own_running_claim(tmp_path) -> None:
    db = str(tmp_path / "tasks.db")
    async with TaskStore(db, owner_id="proc-a", lease_seconds=30) as store:
        manager = TaskManager(store)
        task = await manager.create_task(title="t", created_by="u")
        await manager.take_next_pending()
        before = datetime.fromisoformat(_lease_row(db, task.task_id)[1])
        await asyncio.sleep(0.01)
        assert await manager.heartbeat_many([task.task_id]) == 1
        after = datetime.fromisoformat(_lease_row(db, task.task_id)[1])
        assert after > before
        # Terminal transition clears the lease and refuses further renewal.
        await manager.update_status(task.task_id, TaskStatus.COMPLETED, output={"summary": "x"})
        assert await manager.heartbeat_many([task.task_id]) == 0
        assert _lease_row(db, task.task_id) == (None, None)


@pytest.mark.asyncio
async def test_heartbeat_ignores_other_owners_and_states(tmp_path) -> None:
    db = str(tmp_path / "tasks.db")
    async with TaskStore(db, owner_id="proc-a", lease_seconds=60) as store_a:
        manager_a = TaskManager(store_a)
        task = await manager_a.create_task(title="t", created_by="u")
        await manager_a.take_next_pending()
    async with TaskStore(db, owner_id="proc-b", lease_seconds=60) as store_b:
        manager_b = TaskManager(store_b)
        # Wrong owner: never renewed.
        assert await manager_b.heartbeat_many([task.task_id]) == 0
        # Wrong state (pending, not RUNNING): never renewed either.
        await store_b.requeue_running(note="requeue", expired_only=False)
        assert await manager_b.heartbeat_many([task.task_id]) == 0


@pytest.mark.asyncio
async def test_recovery_leaves_live_claimer_alone(tmp_path) -> None:
    db = str(tmp_path / "tasks.db")
    async with TaskStore(db, owner_id="proc-a", lease_seconds=120) as store:
        manager = TaskManager(store)
        task = await manager.create_task(title="t", created_by="u")
        await manager.take_next_pending()
    # A recovering process (different owner id): the fresh lease means the
    # claimer is live, so the task must NOT be requeued.
    async with TaskStore(db, owner_id="proc-b", lease_seconds=120) as store_b:
        assert await store_b.requeue_running(note="recover", expired_only=True) == 0
        seen = await TaskManager(store_b).get_task(task.task_id)
        assert seen is not None
        assert seen.status is TaskStatus.RUNNING


@pytest.mark.asyncio
async def test_recovery_requeues_expired_claim(tmp_path) -> None:
    db = str(tmp_path / "tasks.db")
    async with TaskStore(db, owner_id="proc-a", lease_seconds=1) as store:
        manager = TaskManager(store)
        task = await manager.create_task(title="t", created_by="u")
        await manager.take_next_pending()
        await asyncio.sleep(1.1)  # let the lease lapse (1 s TTL)
    async with TaskStore(db, owner_id="proc-b", lease_seconds=120) as store_b:
        assert await store_b.requeue_running(note="recover", expired_only=True) == 1
        manager_b = TaskManager(store_b)
        seen = await manager_b.get_task(task.task_id)
        assert seen is not None
        assert seen.status is TaskStatus.PENDING
        assert _lease_row(db, task.task_id) == (None, None)
        notes = [m for m in seen.messages if m.type == "system"]
        assert notes and "recover" in str(notes[-1].content)
        # The requeued task is claimable again — by either process.
        claimed = await manager_b.take_next_pending()
        assert claimed is not None
        assert claimed.task_id == task.task_id


@pytest.mark.asyncio
async def test_unqualified_recovery_keeps_single_process_semantics(tmp_path) -> None:
    db = str(tmp_path / "tasks.db")
    async with TaskStore(db, owner_id="proc-a", lease_seconds=600) as store:
        manager = TaskManager(store)
        task = await manager.create_task(title="t", created_by="u")
        await manager.take_next_pending()
        # Historical behaviour (single-process deployments): everything RUNNING.
        assert await store.requeue_running(note="recover-all") == 1
        seen = await manager.get_task(task.task_id)
        assert seen is not None
        assert seen.status is TaskStatus.PENDING


@pytest.mark.asyncio
async def test_pre_lease_database_is_migrated_and_recoverable(tmp_path) -> None:
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    try:
        conn.executescript(
            """
            CREATE TABLE tasks (
                task_id    TEXT PRIMARY KEY,
                title      TEXT NOT NULL DEFAULT '',
                status     TEXT NOT NULL DEFAULT 'pending',
                created_by TEXT NOT NULL DEFAULT '',
                input      TEXT NOT NULL DEFAULT '{}',
                output     TEXT NOT NULL DEFAULT '{}',
                error      TEXT,
                parent_id  TEXT,
                priority   INTEGER NOT NULL DEFAULT 0,
                seq        INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT INTO tasks (task_id, title, status, created_by, seq, created_at, updated_at)
            VALUES ('legacy-1', 'old', 'running', 'u', 1,
                    '2026-11-20T00:00:00+00:00', '2026-11-20T00:00:00+00:00');
            """
        )
        conn.commit()
    finally:
        conn.close()
    # Opening the store migrates the schema; the legacy RUNNING row has no
    # lease, so lease-aware recovery treats it as expired and requeues it.
    async with TaskStore(str(db), owner_id="proc-b") as store:
        assert await store.requeue_running(note="recover", expired_only=True) == 1
        manager = TaskManager(store)
        seen = await manager.get_task("legacy-1")
        assert seen is not None
        assert seen.status is TaskStatus.PENDING
        claimed = await manager.take_next_pending()
        assert claimed is not None
        assert _lease_row(str(db), "legacy-1")[0] == "proc-b"


def test_lease_resolution_defaults_and_clamps(monkeypatch: pytest.MonkeyPatch) -> None:
    from agency.kernel.task_store import DEFAULT_LEASE_SECONDS, _resolve_lease_seconds

    monkeypatch.delenv("AGENCY_TASKS_LEASE_SECONDS", raising=False)
    assert _resolve_lease_seconds(None) == DEFAULT_LEASE_SECONDS
    monkeypatch.setenv("AGENCY_TASKS_LEASE_SECONDS", "45")
    assert _resolve_lease_seconds(None) == 45
    monkeypatch.setenv("AGENCY_TASKS_LEASE_SECONDS", "not-a-number")
    assert _resolve_lease_seconds(None) == DEFAULT_LEASE_SECONDS
    monkeypatch.setenv("AGENCY_TASKS_LEASE_SECONDS", "0")
    assert _resolve_lease_seconds(None) == 1
    assert _resolve_lease_seconds(10) == 10


def test_default_owner_id_is_unique_per_instance() -> None:
    from agency.kernel.task_store import _default_owner_id

    first, second = _default_owner_id(), _default_owner_id()
    assert first != second
    assert ":" in first
