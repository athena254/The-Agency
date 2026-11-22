"""SQLite persistence for :class:`agency.kernel.tasks.TaskManager`.

Tasks and their conversation messages survive process restarts. The store is
shared between server processes (api + butler) via one database file, so a
task submitted through one server is visible and claimable by the other.

Claim protocol
--------------
``claim_next_pending`` runs ``SELECT`` + ``UPDATE`` inside a ``BEGIN
IMMEDIATE`` transaction so two dispatcher processes can never claim the same
task. SQLite WAL keeps readers unblocked while claims serialize.

Claims carry a lease: the claiming process stamps its ``owner_id`` and a
``lease_expires_at`` deadline and renews it via :meth:`heartbeat` while the
task executes (the dispatcher heartbeats its in-flight tasks). Startup
recovery is explicit, not automatic; with ``expired_only=True`` it requeues
only claims whose lease expired (or pre-lease legacy rows), so a second live
process's running tasks are left alone. Safe multi-process recovery therefore
requires every claiming process to run a lease-aware build; a live process
running pre-lease code still has its tasks requeued after the lease TTL.

Status updates are compare-and-set, not last-writer-wins: :meth:`update_task`
guards its UPDATE with the caller's expected status (``BEGIN IMMEDIATE``
holds the write lock across guard check and write), refuses rows that already
reached an immutable terminal state (COMPLETED/FAILED; BLOCKED stays
re-openable per the transition matrix), and returns a typed
:class:`~agency.kernel.tasks.TaskWriteResult` so a late writer observes the
stored record and yields instead of overwriting it.

Single-process deployments may still call :meth:`requeue_running` with
``expired_only=False`` (the historical behaviour) on boot
(``AGENCY_TASKS_RECOVER=1``).
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any, Self
from uuid import uuid4

import aiosqlite
import structlog

from agency.kernel.identity import utcnow
from agency.kernel.tasks import Task, TaskMessage, TaskStatus, TaskWriteResult

log = structlog.get_logger(__name__)

LEASE_SECONDS_ENV_VAR = "AGENCY_TASKS_LEASE_SECONDS"
DEFAULT_LEASE_SECONDS = 300

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
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
CREATE INDEX IF NOT EXISTS idx_tasks_queue
    ON tasks (status, priority DESC, seq ASC);
CREATE INDEX IF NOT EXISTS idx_tasks_created_by
    ON tasks (created_by);
CREATE TABLE IF NOT EXISTS task_messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    TEXT NOT NULL REFERENCES tasks(task_id),
    type       TEXT NOT NULL,
    content    TEXT,
    metadata   TEXT NOT NULL DEFAULT '{}',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_task_messages_task
    ON task_messages (task_id, id);
"""

_TASK_COLUMNS = (
    "task_id, title, status, created_by, input, output, error, "
    "parent_id, priority, seq, created_at, updated_at"
)

_TERMINAL_STATUSES = frozenset({TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.BLOCKED})

#: Rows the compare-and-set guard treats as immutable: no legal transition
#: leaves them (``TaskManager._assert_transition`` is the authority), so a
#: late write to such a row must observe the stored record and yield. BLOCKED
#: is terminal for waiters but deliberately re-openable (BLOCKED -> PENDING
#: is the documented requeue path), so BLOCKED rows stay writable.
_IMMUTABLE_STATUSES = frozenset({TaskStatus.COMPLETED, TaskStatus.FAILED})

_IMMUTABLE_PLACEHOLDERS = ", ".join("?" for _ in _IMMUTABLE_STATUSES)


def _resolve_lease_seconds(lease_seconds: int | None) -> int:
    """Lease TTL in seconds: explicit argument > env > conservative default."""

    raw = lease_seconds
    if raw is None:
        raw_env = os.environ.get(LEASE_SECONDS_ENV_VAR, "").strip()
        try:
            raw = int(raw_env) if raw_env else DEFAULT_LEASE_SECONDS
        except ValueError:
            log.warning("task_store.lease_env_invalid", env=LEASE_SECONDS_ENV_VAR)
            raw = DEFAULT_LEASE_SECONDS
    if raw < 1:
        log.warning("task_store.lease_too_small", value=raw, minimum=1)
        raw = 1
    return raw


def _default_owner_id() -> str:
    """Per-process claim owner: host + pid + random suffix (unique per instance)."""

    host = socket.gethostname() or "unknown-host"
    return f"{host}:{os.getpid()}:{uuid4().hex[:8]}"


def _dump(value: Any) -> str:
    return json.dumps(value if value is not None else {}, default=str)


def _dump_message_content(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, default=str)


def _iso(moment: datetime) -> str:
    return moment.isoformat()


class TaskStore:
    """Async SQLite store for kernel tasks. Follows the MemoryStore lifecycle::

        store = TaskStore("data/tasks.db")
        await store.initialize()
        try:
            ...
        finally:
            await store.close()

    All public methods lazily initialize the schema.
    """

    def __init__(
        self, db_path: str, *, owner_id: str | None = None, lease_seconds: int | None = None
    ) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None
        self._initialized = False
        self._init_lock = asyncio.Lock()
        # One aiosqlite connection is shared by every caller in this process
        # (api intake + dispatcher loop), and aiosqlite only serializes single
        # statements — not multi-statement transactions. Without this lock, a
        # second coroutine can slip its BEGIN IMMEDIATE between another's
        # BEGIN and COMMIT ("cannot start a transaction within a transaction").
        self._tx_lock = asyncio.Lock()
        self._owner_id = owner_id or _default_owner_id()
        self._lease_seconds = _resolve_lease_seconds(lease_seconds)

    async def __aenter__(self) -> Self:
        await self.initialize()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    @property
    def db_path(self) -> str:
        return self._db_path

    @property
    def owner_id(self) -> str:
        """Claim owner stamped on tasks this process claims."""
        return self._owner_id

    @property
    def lease_seconds(self) -> int:
        """Lease TTL granted on each claim and heartbeat renewal."""
        return self._lease_seconds

    @property
    def connection(self) -> aiosqlite.Connection:
        """The live connection. Raises if :meth:`initialize` has not run."""

        if self._conn is None:
            raise RuntimeError("TaskStore is not initialized")
        return self._conn

    async def initialize(self) -> None:
        """Open the connection and create the schema (idempotent)."""

        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            # isolation_level=None -> autocommit; transactions are explicit so
            # claim_next_pending can hold a write lock across SELECT + UPDATE.
            self._conn = await aiosqlite.connect(self._db_path, isolation_level=None)
            self._conn.row_factory = aiosqlite.Row
            await self._conn.execute("PRAGMA journal_mode=WAL")
            await self._conn.execute("PRAGMA busy_timeout=5000")
            await self._conn.executescript(_SCHEMA)
            await self._migrate_lease_columns()
            self._initialized = True
            log.info(
                "task_store.initialized",
                db_path=self._db_path,
                owner_id=self._owner_id,
                lease_seconds=self._lease_seconds,
            )

    async def _migrate_lease_columns(self) -> None:
        """Add the lease columns to pre-lease databases (idempotent)."""

        conn = self.connection
        cursor = await conn.execute("PRAGMA table_info(tasks)")
        existing = {row["name"] for row in await cursor.fetchall()}
        for column in ("lease_owner", "lease_expires_at"):
            if column not in existing:
                await conn.execute(f"ALTER TABLE tasks ADD COLUMN {column} TEXT")

    async def close(self) -> None:
        """Close the connection. Safe to call more than once."""

        if self._conn is not None:
            await self._conn.close()
            self._conn = None
            self._initialized = False
            log.info("task_store.closed", db_path=self._db_path)

    # ------------------------------------------------------------------ #
    # Task rows
    # ------------------------------------------------------------------ #

    async def insert_task(self, task: Task) -> None:
        """Insert ``task``; raises ``sqlite3.IntegrityError`` on duplicate id."""

        await self.initialize()
        async with self._transaction() as conn:
            cursor = await conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM tasks")
            row = await cursor.fetchone()
            seq = int(row[0]) if row is not None else 1
            await conn.execute(
                f"INSERT INTO tasks ({_TASK_COLUMNS}, lease_owner, lease_expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
                (
                    task.task_id,
                    task.title,
                    task.status.value,
                    task.created_by,
                    _dump(task.input),
                    _dump(task.output),
                    task.error,
                    task.parent_id,
                    task.priority,
                    seq,
                    _iso(task.created_at),
                    _iso(task.updated_at),
                ),
            )

    async def get_task(self, task_id: str) -> Task | None:
        """Fetch one task with its conversation, or ``None``."""

        await self.initialize()
        conn = self.connection
        cursor = await conn.execute(
            f"SELECT {_TASK_COLUMNS} FROM tasks WHERE task_id = ?", (task_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        task = _task_from_row(row)
        task.messages = await self.list_messages(task_id)
        return task

    async def list_tasks(
        self,
        *,
        status: TaskStatus | None = None,
        created_by: str | None = None,
    ) -> list[Task]:
        """List tasks ordered by creation time (earliest first)."""

        await self.initialize()
        conn = self.connection
        clauses: list[str] = []
        params: list[Any] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        if created_by is not None:
            clauses.append("created_by = ?")
            params.append(created_by)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        cursor = await conn.execute(
            f"SELECT {_TASK_COLUMNS} FROM tasks{where} ORDER BY created_at ASC, seq ASC", params
        )
        return [_task_from_row(row) for row in await cursor.fetchall()]

    async def update_task(
        self,
        task_id: str,
        *,
        status: TaskStatus | None = None,
        error: str | None = None,
        output: dict[str, Any] | None = None,
        set_error: bool = False,
        expected_status: TaskStatus | None = None,
    ) -> TaskWriteResult:
        """Update mutable task fields under a status compare-and-set.

        The UPDATE runs inside a ``BEGIN IMMEDIATE`` transaction (the same
        discipline as :meth:`claim_next_pending`) with a status guard in the
        WHERE clause, so two processes sharing the database file cannot
        silently overwrite each other's transition: a write whose guard does
        not match is refused, never applied blind.

        ``expected_status`` is the caller's compare-and-set guard — the write
        applies only when the stored status still equals it. Without a guard
        the write still refuses any row that already reached an immutable
        terminal state. COMPLETED/FAILED rows are immutable either way (no
        legal transition leaves them; BLOCKED stays re-openable): a late
        writer gets ``YIELDED_TERMINAL`` and must observe the stored terminal
        record instead of overwriting it.

        ``set_error`` distinguishes "leave error untouched" from "clear it"
        (``error=None`` + ``set_error=True``).

        Returns the typed outcome: ``APPLIED``, ``YIELDED_TERMINAL``,
        ``STALE`` (row exists at a different non-terminal status), or
        ``MISSING``.
        """

        await self.initialize()
        async with self._transaction() as conn:
            assignments: list[str] = ["updated_at = ?"]
            params: list[Any] = [_iso(utcnow())]
            if status is not None:
                assignments.append("status = ?")
                params.append(status.value)
                if status in _TERMINAL_STATUSES:
                    # The lease has served its purpose; keep terminal rows tidy.
                    assignments.append("lease_owner = NULL")
                    assignments.append("lease_expires_at = NULL")
            if set_error:
                assignments.append("error = ?")
                params.append(error)
            if output is not None:
                assignments.append("output = ?")
                params.append(_dump(output))
            if expected_status is not None:
                if expected_status in _IMMUTABLE_STATUSES:
                    # CAS against an immutable row is a write to it.
                    log.warning(
                        "task_store.terminal_write_refused",
                        task_id=task_id,
                        expected=expected_status.value,
                        attempted=status.value if status else None,
                    )
                    return TaskWriteResult.YIELDED_TERMINAL
                guard, guard_params = "AND status = ?", [expected_status.value]
            else:
                # Unguarded writes still never touch immutable rows.
                guard, guard_params = (
                    f"AND status NOT IN ({_IMMUTABLE_PLACEHOLDERS})",
                    [terminal.value for terminal in _IMMUTABLE_STATUSES],
                )
            cursor = await conn.execute(
                f"UPDATE tasks SET {', '.join(assignments)} WHERE task_id = ? {guard}",
                [*params, task_id, *guard_params],
            )
            if cursor.rowcount and cursor.rowcount > 0:
                return TaskWriteResult.APPLIED
            # Refused: classify honestly for the caller (we hold the write
            # lock, so this read sees the row that caused the refusal).
            cursor = await conn.execute("SELECT status FROM tasks WHERE task_id = ?", (task_id,))
            row = await cursor.fetchone()
            if row is None:
                return TaskWriteResult.MISSING
            current = TaskStatus(row["status"])
            if current in _IMMUTABLE_STATUSES:
                log.warning(
                    "task_store.terminal_write_refused",
                    task_id=task_id,
                    current=current.value,
                    attempted=status.value if status else None,
                )
                return TaskWriteResult.YIELDED_TERMINAL
            log.warning(
                "task_store.stale_write_refused",
                task_id=task_id,
                current=current.value,
                expected=expected_status.value if expected_status else None,
                attempted=status.value if status else None,
            )
            return TaskWriteResult.STALE

    async def claim_next_pending(self) -> Task | None:
        """Atomically pop the highest-priority pending task and mark it RUNNING.

        The SELECT and UPDATE run inside one ``BEGIN IMMEDIATE`` transaction,
        so concurrent dispatcher processes serialize on SQLite's write lock.
        The claim is stamped with this store's ``owner_id`` and a lease
        deadline (``lease_seconds`` from now); the claimer renews it via
        :meth:`heartbeat` while the task executes.
        """

        await self.initialize()
        conn = self.connection
        async with self._tx_lock:  # serialize with the other explicit transactions
            await conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = await conn.execute(
                    f"SELECT {_TASK_COLUMNS} FROM tasks WHERE status = ? "
                    "ORDER BY priority DESC, seq ASC LIMIT 1",
                    (TaskStatus.PENDING.value,),
                )
                row = await cursor.fetchone()
                if row is None:
                    await conn.execute("COMMIT")
                    return None
                now = utcnow()
                lease_expires_at = _iso(now + timedelta(seconds=self._lease_seconds))
                await conn.execute(
                    "UPDATE tasks SET status = ?, updated_at = ?, lease_owner = ?, "
                    "lease_expires_at = ? WHERE task_id = ?",
                    (
                        TaskStatus.RUNNING.value,
                        _iso(now),
                        self._owner_id,
                        lease_expires_at,
                        row["task_id"],
                    ),
                )
                await conn.execute("COMMIT")
            except BaseException:
                try:
                    await conn.execute("ROLLBACK")
                except aiosqlite.Error:
                    pass
                raise
        task = _task_from_row(row)
        task.status = TaskStatus.RUNNING
        task.updated_at = now
        task.messages = await self.list_messages(task.task_id)
        return task

    async def heartbeat(self, task_ids: Sequence[str]) -> int:
        """Renew the lease on ``task_ids`` claimed by this store's owner.

        Only rows still RUNNING and stamped with this store's ``owner_id`` are
        renewed; a task that was requeued (or claimed by another process) is
        not touched. Returns the number of renewed leases.
        """

        ids = [task_id for task_id in task_ids if task_id]
        if not ids:
            return 0
        await self.initialize()
        conn = self.connection
        placeholders = ", ".join("?" for _ in ids)
        lease_expires_at = _iso(utcnow() + timedelta(seconds=self._lease_seconds))
        cursor = await conn.execute(
            f"UPDATE tasks SET lease_expires_at = ? WHERE status = ? "
            f"AND lease_owner = ? AND task_id IN ({placeholders})",
            (lease_expires_at, TaskStatus.RUNNING.value, self._owner_id, *ids),
        )
        renewed = cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
        if renewed:
            log.debug("task_store.heartbeat", renewed=renewed)
        return renewed

    async def insert_message(self, task_id: str, message: TaskMessage) -> None:
        """Append one conversation message row."""

        await self.initialize()
        conn = self.connection
        created_at = message.created_at or utcnow()
        await conn.execute(
            "INSERT INTO task_messages (task_id, type, content, metadata, created_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                task_id,
                message.type,
                _dump_message_content(message.content),
                _dump(message.metadata),
                message.created_by,
                _iso(created_at),
            ),
        )

    async def list_messages(self, task_id: str) -> list[TaskMessage]:
        """Return a task's conversation in insertion order."""

        await self.initialize()
        conn = self.connection
        cursor = await conn.execute(
            "SELECT task_id, type, content, metadata, created_by, created_at "
            "FROM task_messages WHERE task_id = ? ORDER BY id ASC",
            (task_id,),
        )
        messages: list[TaskMessage] = []
        for row in await cursor.fetchall():
            messages.append(
                TaskMessage(
                    task_id=row["task_id"],
                    type=row["type"],
                    content=json.loads(row["content"]) if row["content"] is not None else None,
                    metadata=json.loads(row["metadata"] or "{}"),
                    created_by=row["created_by"],
                    created_at=datetime.fromisoformat(row["created_at"]),
                )
            )
        return messages

    async def requeue_running(self, *, note: str, expired_only: bool = False) -> int:
        """Requeue RUNNING tasks back to PENDING and append ``note``.

        ``expired_only=True`` (multi-process recovery) requeues only tasks
        whose claim lease has expired — or pre-lease rows with no lease at
        all. Tasks whose lease is still fresh belong to a live claimer and
        are left alone. This makes restart recovery safe across processes,
        provided every claiming process runs a lease-aware build (pre-lease
        processes do not heartbeat, so their claims look expired).

        ``expired_only=False`` (the historical default) requeues every
        RUNNING task — only safe in single-process deployments.

        Returns the number of requeued tasks.
        """

        await self.initialize()
        async with self._transaction() as conn:
            if expired_only:
                cursor = await conn.execute(
                    "SELECT task_id FROM tasks WHERE status = ? "
                    "AND (lease_expires_at IS NULL OR lease_expires_at <= ?)",
                    (TaskStatus.RUNNING.value, _iso(utcnow())),
                )
            else:
                cursor = await conn.execute(
                    "SELECT task_id FROM tasks WHERE status = ?", (TaskStatus.RUNNING.value,)
                )
            rows = list(await cursor.fetchall())
            if not rows:
                return 0
            now = _iso(utcnow())
            for row in rows:
                await conn.execute(
                    "UPDATE tasks SET status = ?, updated_at = ?, lease_owner = NULL, "
                    "lease_expires_at = NULL WHERE task_id = ?",
                    (TaskStatus.PENDING.value, now, row["task_id"]),
                )
                await conn.execute(
                    "INSERT INTO task_messages (task_id, type, content, metadata, created_by, "
                    "created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        row["task_id"],
                        "system",
                        _dump_message_content(note),
                        "{}",
                        "task_store",
                        now,
                    ),
                )
            log.info("task_store.requeued_running", count=len(rows), expired_only=expired_only)
            return len(rows)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    class _Transaction:
        """Async context manager wrapping an explicit transaction.

        Serialized by ``TaskStore._tx_lock``: the store holds ONE aiosqlite
        connection shared by every in-process caller, so concurrent
        BEGIN/COMMIT sequences must not interleave.
        """

        def __init__(self, store: TaskStore) -> None:
            self._store = store

        async def __aenter__(self) -> aiosqlite.Connection:
            await self._store._tx_lock.acquire()
            conn = self._store.connection
            try:
                await conn.execute("BEGIN IMMEDIATE")
            except BaseException:
                self._store._tx_lock.release()
                raise
            return conn

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
            conn = self._store.connection
            try:
                if exc_type is None:
                    await conn.execute("COMMIT")
                else:
                    try:
                        await conn.execute("ROLLBACK")
                    except aiosqlite.Error:
                        pass
            finally:
                self._store._tx_lock.release()

    def _transaction(self) -> TaskStore._Transaction:
        return TaskStore._Transaction(self)


def _task_from_row(row: aiosqlite.Row) -> Task:
    """Build a :class:`Task` from a tasks-table row (messages loaded lazily)."""

    return Task(
        task_id=row["task_id"],
        title=row["title"],
        status=TaskStatus(row["status"]),
        created_by=row["created_by"],
        input=json.loads(row["input"] or "{}"),
        output=json.loads(row["output"] or "{}"),
        error=row["error"],
        parent_id=row["parent_id"],
        priority=int(row["priority"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )
