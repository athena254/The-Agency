"""SQLite persistence for :class:`agency.kernel.tasks.TaskManager`.

Tasks and their conversation messages survive process restarts. The store is
shared between server processes (api + butler) via one database file, so a
task submitted through one server is visible and claimable by the other.

Claim protocol
--------------
``claim_next_pending`` runs ``SELECT`` + ``UPDATE`` inside a ``BEGIN
IMMEDIATE`` transaction so two dispatcher processes can never claim the same
task. SQLite WAL keeps readers unblocked while claims serialize.

Startup recovery is explicit, not automatic: a shared database means one
process must not requeue tasks another (live) process is executing. Call
:meth:`requeue_running` on boot only in single-process deployments
(``AGENCY_TASKS_RECOVER=1``).
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

import aiosqlite
import structlog

from agency.kernel.identity import utcnow
from agency.kernel.tasks import Task, TaskMessage, TaskStatus

log = structlog.get_logger(__name__)

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

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None
        self._initialized = False
        self._init_lock = asyncio.Lock()

    async def __aenter__(self) -> TaskStore:
        await self.initialize()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    @property
    def db_path(self) -> str:
        return self._db_path

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
            self._initialized = True
            log.info("task_store.initialized", db_path=self._db_path)

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
        conn = self.connection
        async with self._transaction() as conn:
            cursor = await conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM tasks")
            row = await cursor.fetchone()
            seq = int(row[0]) if row is not None else 1
            await conn.execute(
                f"INSERT INTO tasks ({_TASK_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
    ) -> bool:
        """Update mutable task fields. ``False`` when the task does not exist.

        ``set_error`` distinguishes "leave error untouched" from "clear it"
        (``error=None`` + ``set_error=True``).
        """

        await self.initialize()
        conn = self.connection
        assignments: list[str] = ["updated_at = ?"]
        params: list[Any] = [_iso(utcnow())]
        if status is not None:
            assignments.append("status = ?")
            params.append(status.value)
        if set_error:
            assignments.append("error = ?")
            params.append(error)
        if output is not None:
            assignments.append("output = ?")
            params.append(_dump(output))
        params.append(task_id)
        cursor = await conn.execute(
            f"UPDATE tasks SET {', '.join(assignments)} WHERE task_id = ?", params
        )
        return cursor.rowcount > 0

    async def claim_next_pending(self) -> Task | None:
        """Atomically pop the highest-priority pending task and mark it RUNNING.

        The SELECT and UPDATE run inside one ``BEGIN IMMEDIATE`` transaction,
        so concurrent dispatcher processes serialize on SQLite's write lock.
        """

        await self.initialize()
        conn = self.connection
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
            now = _iso(utcnow())
            await conn.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ?",
                (TaskStatus.RUNNING.value, now, row["task_id"]),
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
        task.updated_at = datetime.fromisoformat(now)
        task.messages = await self.list_messages(task.task_id)
        return task

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

    async def requeue_running(self, *, note: str) -> int:
        """Requeue every RUNNING task back to PENDING and append ``note``.

        Only safe in single-process deployments: a live second process would
        otherwise have its executing tasks snatched back into the queue.
        Returns the number of requeued tasks.
        """

        await self.initialize()
        conn = self.connection
        async with self._transaction() as conn:
            cursor = await conn.execute("SELECT task_id FROM tasks WHERE status = ?",
                                        (TaskStatus.RUNNING.value,))
            rows = await cursor.fetchall()
            if not rows:
                return 0
            now = _iso(utcnow())
            for row in rows:
                await conn.execute(
                    "UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ?",
                    (TaskStatus.PENDING.value, now, row["task_id"]),
                )
                await conn.execute(
                    "INSERT INTO task_messages (task_id, type, content, metadata, created_by, "
                    "created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        row["task_id"],
                        "system",
                        note,
                        "{}",
                        "task_store",
                        now,
                    ),
                )
            log.info("task_store.requeued_running", count=len(rows))
            return len(rows)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    class _Transaction:
        """Async context manager wrapping an explicit transaction."""

        def __init__(self, store: TaskStore) -> None:
            self._store = store

        async def __aenter__(self) -> aiosqlite.Connection:
            conn = self._store.connection
            await conn.execute("BEGIN IMMEDIATE")
            return conn

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
            conn = self._store.connection
            if exc_type is None:
                await conn.execute("COMMIT")
            else:
                try:
                    await conn.execute("ROLLBACK")
                except aiosqlite.Error:
                    pass

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
