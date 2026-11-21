"""Task lifecycle and structured agent-to-agent messaging.

Tasks are the unit of work in the Agency. A task carries an ordered queue of
structured :class:`TaskMessage` objects (SPEC §19 — no uncontrolled free-form
agent-to-agent communication) and transitions through :class:`TaskStatus`.

The :class:`TaskManager` is the deterministic coordinator for the task
workflow. It is deliberately async so it can be embedded in the same event
loop as the audit log. Without a :class:`~agency.kernel.task_store.TaskStore`
it is purely in-process (in-memory). With a store, SQLite becomes
authoritative: tasks survive restarts and are visible to every process
sharing the database file, and ``take_next_pending`` claims are atomic across
processes.
"""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass, replace
from dataclasses import field as dc_field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Final
from uuid import uuid4

import structlog
from pydantic import BaseModel, ConfigDict, Field, field_validator

from agency.kernel.identity import utcnow

if TYPE_CHECKING:
    from agency.kernel.task_store import TaskStore


class TaskStatus(str, Enum):
    """Lifecycle state of a task."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"

    def __str__(self) -> str:
        return self.value


#: States a task cannot leave; dispatchers and submitters treat these as final.
_TERMINAL_STATUSES: Final[frozenset[TaskStatus]] = frozenset(
    {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.BLOCKED}
)


@dataclass(frozen=True, slots=True)
class TaskMessage:
    """A single structured message within a task conversation.

    Immutable (:class:`~dataclasses.dataclass` with ``frozen=True``) so
    conversation history cannot be rewritten post-hoc.
    """

    task_id: str
    type: str
    content: Any = None
    metadata: dict[str, Any] = dc_field(default_factory=dict)
    created_by: str = ""
    created_at: datetime = dc_field(default_factory=utcnow)

    def __str__(self) -> str:
        return f"<TaskMessage {self.task_id} {self.type} by {self.created_by}>"


class Task(BaseModel):
    """An actionable unit of work with full lifecycle state."""

    model_config = ConfigDict(frozen=False, extra="forbid")

    task_id: str = Field(default_factory=lambda: str(uuid4()))
    title: str = Field(default="", description="Short human-readable title.")
    status: TaskStatus = Field(default=TaskStatus.PENDING)
    created_by: str = Field(default="", description="Agent or operator id that created the task.")
    input: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict, description="Final result on COMPLETED.")
    error: str | None = Field(default=None, description="Failure detail on FAILED.")
    messages: list[TaskMessage] = Field(
        default_factory=list, description="Append-only conversation log."
    )
    parent_id: str | None = Field(default=None, description="Parent task id for hierarchy.")
    priority: int = Field(default=0, ge=0, le=10)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    @field_validator("task_id")
    @classmethod
    def _normalize_task_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("task_id must not be empty.")
        return value.strip()

    def add_message(self, message: TaskMessage) -> TaskMessage:
        """Append a message; the local ``updated_at`` is refreshed in place."""
        self.messages.append(message)
        self.updated_at = utcnow()
        return message


@dataclass
class _PendingEntry:
    """Internal priority-queue entry with FIFO ordering at equal priority."""

    priority: int
    sequence: int
    task: Task

    def __lt__(self, other: _PendingEntry) -> bool:
        if self.priority != other.priority:
            return self.priority > other.priority
        return self.sequence < other.sequence


class TaskManager:
    """Coordinates task lifecycle.

    The manager orders pending work by priority (high first) with FIFO
    tie-breaking. All public operations are async and serialized by an
    internal lock so they are safe to call from any task in the event loop.

    With ``store`` set, SQLite is authoritative: tasks persist across
    restarts, every process sharing the database file sees the same queue,
    and ``take_next_pending`` claims atomically via the store. Without a
    store the manager is purely in-memory (original behaviour, used by
    unit tests and embedded library callers).
    """

    def __init__(self, store: TaskStore | None = None) -> None:
        self._store = store
        self._tasks: dict[str, Task] = {}
        self._pending: list[_PendingEntry] = []
        self._sequence = 0
        self._lock = asyncio.Lock()
        self._log = structlog.get_logger(__name__)

    @property
    def store(self) -> TaskStore | None:
        """The backing store when SQLite is authoritative, else ``None``."""
        return self._store

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def create_task(
        self,
        *,
        title: str = "",
        created_by: str,
        input: dict[str, Any] | None = None,
        parent_id: str | None = None,
        priority: int = 0,
        task_id: str | None = None,
    ) -> Task:
        """Create and register a task.

        Parameters
        ----------
        created_by:
            Agent or operator id that created the task (required).
        input:
            Structured input payload (``None`` -> ``{}``).
        parent_id:
            Parent task id, if part of a hierarchy.
        priority:
            0..10 scheduling priority (higher runs first).
        task_id:
            Explicit task id; a UUID v4 is generated when omitted.
        """
        task = Task(
            task_id=task_id or str(uuid4()),
            title=title,
            created_by=created_by,
            input=input or {},
            parent_id=parent_id,
            priority=priority,
        )
        async with self._lock:
            if task.task_id in self._tasks:
                raise ValueError(f"task {task.task_id!r} already exists")
            self._tasks[task.task_id] = task
            if self._store is not None:
                try:
                    await self._store.insert_task(task)
                except sqlite3.IntegrityError as exc:
                    del self._tasks[task.task_id]
                    raise ValueError(f"task {task.task_id!r} already exists") from exc
            if task.status is TaskStatus.PENDING and self._store is None:
                self._enqueue(task)
        self._log.info(
            "task.created", task_id=task.task_id, created_by=created_by, priority=priority
        )
        return task

    async def get_task(self, task_id: str) -> Task | None:
        """Fetch a task by id, or ``None`` if it does not exist.

        With a store, reads come from SQLite (fresh across processes) and are
        cached in memory.
        """
        if self._store is not None:
            task = await self._store.get_task(task_id)
            if task is not None:
                async with self._lock:
                    self._tasks.setdefault(task_id, task)
            return task
        async with self._lock:
            return self._tasks.get(task_id)

    async def list_tasks(
        self,
        status: TaskStatus | None = None,
        created_by: str | None = None,
    ) -> list[Task]:
        """Return tasks, optionally filtered by :class:`TaskStatus` and creator.

        Results are ordered by creation time (earliest first). ``created_by``
        filters to one submitter's tasks (scoped reads); with a store the
        filter reflects tasks submitted by every process sharing the file.
        """
        if self._store is not None:
            tasks = await self._store.list_tasks(status=status, created_by=created_by)
            async with self._lock:
                for task in tasks:
                    self._tasks.setdefault(task.task_id, task)
            return tasks
        async with self._lock:
            tasks = list(self._tasks.values())
        if status is not None:
            tasks = [task for task in tasks if task.status is status]
        if created_by is not None:
            tasks = [task for task in tasks if task.created_by == created_by]
        return sorted(tasks, key=lambda task: task.created_at)

    async def update_status(
        self,
        task_id: str,
        status: TaskStatus,
        *,
        error: str | None = None,
        output: dict[str, Any] | None = None,
    ) -> Task:
        """Transition a task into a new lifecycle state.

        ``output`` populates the task's final result payload (written
        alongside the status change so pollers never observe COMPLETED
        without its result). ``error`` is recorded when provided.

        When the task is a child (``parent_id`` set) and the target state is
        terminal, the result is also recorded on the parent's task record as
        a ``child_result`` message, so the submitter sees the delegated
        result wherever it reads task status.

        Raises
        ------
        KeyError:
            If the task does not exist.
        ValueError:
            On illegal transitions (e.g. COMPLETED -> RUNNING).
        """
        current = await self.get_task(task_id)
        if current is None:
            raise KeyError(f"unknown task {task_id!r}")
        self._assert_transition(current.status, status)
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is not None:
                self._drop_from_pending(task_id)
                task.status = status
                if error is not None:
                    task.error = error
                if output is not None:
                    task.output = output
                task.updated_at = utcnow()
                if status is TaskStatus.PENDING and self._store is None:
                    self._enqueue(task)
            if self._store is not None:
                updated = await self._store.update_task(
                    task_id,
                    status=status,
                    error=error,
                    output=output,
                    set_error=error is not None,
                )
                if not updated:
                    raise KeyError(f"unknown task {task_id!r}")
        self._log.info("task.status", task_id=task_id, status=status.value)
        if status in _TERMINAL_STATUSES:
            await self._notify_parent_of_result(task_id, status)
        return await self.get_task(task_id) or current

    async def heartbeat_many(self, task_ids: list[str]) -> int:
        """Renew store leases for tasks this process still holds RUNNING.

        No-op (0) without a store; in-memory tasks have no lease to renew.
        """

        if self._store is None or not task_ids:
            return 0
        return await self._store.heartbeat(task_ids)

    async def _notify_parent_of_result(self, task_id: str, status: TaskStatus) -> None:
        """Record a child task's terminal result on its parent's task record.

        Delegated children carry ``parent_id``; the submitter reads task
        status through the parent (``GET /v1/tasks/{id}`` or
        :meth:`get_task`), so the result — status, output summary, error —
        is appended as a structured ``child_result`` message there. The
        child's own ``output``/``error`` fields are already persisted by the
        terminal transition itself.

        Best-effort by design: if the parent is gone, or the append races a
        store shutdown, the child's terminal state still stands (the result
        remains readable on the child record) and the loss is logged, not
        raised. A crash between the child's terminal write and this append
        is honest UNCERTAIN territory — the parent may never learn the
        result, though the child record always holds it.
        """

        child = await self.get_task(task_id)
        if child is None or not child.parent_id:
            return
        summary = child.output.get("summary") if isinstance(child.output, dict) else None
        content = {
            "child_task_id": child.task_id,
            "status": status.value,
            "summary": summary if isinstance(summary, str) else None,
            "error": child.error,
        }
        try:
            await self.add_message(
                child.parent_id,
                TaskMessage(
                    task_id=child.parent_id,
                    type="child_result",
                    content=content,
                    created_by="task_manager",
                ),
            )
        except (KeyError, ValueError) as exc:
            self._log.warning(
                "task.parent_result_unrecorded",
                child_task_id=task_id,
                parent_task_id=child.parent_id,
                reason=type(exc).__name__,
            )

    # ------------------------------------------------------------------ #
    # Messaging
    # ------------------------------------------------------------------ #

    async def add_message(self, task_id: str, message: TaskMessage) -> TaskMessage:
        """Append a structured message to a task's conversation.

        If the message uses ``task_id=""`` it is bound to ``task_id`` at
        insert time.
        """
        current = await self.get_task(task_id)
        if current is None:
            raise KeyError(f"unknown task {task_id!r}")
        bound = message
        if bound.task_id == "":
            bound = replace(message, task_id=task_id)
        else:
            if bound.task_id != task_id:
                raise ValueError("message.task_id does not match the target task")
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is not None:
                task.add_message(bound)
            if self._store is not None:
                await self._store.insert_message(task_id, bound)
        self._log.info("task.message", task_id=task_id, msg_type=bound.type, by=bound.created_by)
        return bound

    async def take_next_pending(self) -> Task | None:
        """Pop the highest-priority pending task, or ``None`` if the queue is empty.

        With a store the claim is an atomic SQLite transaction, safe across
        concurrent dispatcher processes; without one it pops from the
        in-process priority queue.
        """
        if self._store is not None:
            task = await self._store.claim_next_pending()
            if task is not None:
                async with self._lock:
                    self._tasks[task.task_id] = task
                self._log.info("task.taken", task_id=task.task_id, priority=task.priority)
            return task
        async with self._lock:
            if not self._pending:
                return None
            entry = _guarded_pop(self._pending)
            self._tasks[entry.task.task_id].status = TaskStatus.RUNNING
            self._tasks[entry.task.task_id].updated_at = utcnow()
        self._log.info("task.taken", task_id=entry.task.task_id, priority=entry.priority)
        return entry.task

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _enqueue(self, task: Task) -> None:
        self._sequence += 1
        self._pending.append(_PendingEntry(task.priority, self._sequence, task))

    def _drop_from_pending(self, task_id: str) -> None:
        self._pending = [entry for entry in self._pending if entry.task.task_id != task_id]

    @staticmethod
    def _assert_transition(current: TaskStatus, target: TaskStatus) -> None:
        legal: dict[TaskStatus, set[TaskStatus]] = {
            TaskStatus.PENDING: {
                TaskStatus.RUNNING,
                TaskStatus.BLOCKED,
                TaskStatus.FAILED,
                TaskStatus.COMPLETED,
            },
            TaskStatus.RUNNING: {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.BLOCKED},
            TaskStatus.BLOCKED: {TaskStatus.PENDING, TaskStatus.FAILED, TaskStatus.COMPLETED},
            TaskStatus.COMPLETED: set(),
            TaskStatus.FAILED: set(),
        }
        if target not in legal[current]:
            raise ValueError(f"illegal task transition {current.value} -> {target.value}")


def _guarded_pop(queue: list[_PendingEntry]) -> _PendingEntry:
    """Best-effort take of the smallest entry under the caller's lock."""
    target = min(queue)
    queue.remove(target)
    return target
