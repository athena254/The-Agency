"""Task lifecycle and structured agent-to-agent messaging.

Tasks are the unit of work in the Agency. A task carries an ordered queue of
structured :class:`TaskMessage` objects (SPEC §19 — no uncontrolled free-form
agent-to-agent communication) and transitions through :class:`TaskStatus`.

The :class:`TaskManager` is the deterministic, in-process coordinator for the
task workflow. It is deliberately async so it can be embedded in the same
event loop as the audit log.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from dataclasses import field as dc_field
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

import structlog
from pydantic import BaseModel, ConfigDict, Field, field_validator

from agency.kernel.identity import utcnow


class TaskStatus(str, Enum):
    """Lifecycle state of a task."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"

    def __str__(self) -> str:
        return self.value


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
    messages: list[TaskMessage] = Field(default_factory=list, description="Append-only conversation log.")
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
    """Coordinates task lifecycle in-process.

    The manager keeps tasks in memory and orders pending work by priority
    (high first) with FIFO tie-breaking. All public operations are async and
    serialized by an internal lock so they are safe to call from any task in
    the event loop.
    """

    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}
        self._pending: list[_PendingEntry] = []
        self._sequence = 0
        self._lock = asyncio.Lock()
        self._log = structlog.get_logger(__name__)

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
            if task.status is TaskStatus.PENDING:
                self._enqueue(task)
        self._log.info("task.created", task_id=task.task_id, created_by=created_by, priority=priority)
        return task

    async def get_task(self, task_id: str) -> Task | None:
        """Fetch a task by id, or ``None`` if it does not exist."""
        async with self._lock:
            return self._tasks.get(task_id)

    async def list_tasks(self, status: TaskStatus | None = None) -> list[Task]:
        """Return tasks, optionally filtered by :class:`TaskStatus`.

        Results are ordered by creation time (earliest first).
        """
        async with self._lock:
            tasks = list(self._tasks.values())
        if status is not None:
            tasks = [task for task in tasks if task.status is status]
        return sorted(tasks, key=lambda task: task.created_at)

    async def update_status(self, task_id: str, status: TaskStatus, *, error: str | None = None) -> Task:
        """Transition a task into a new lifecycle state.

        Raises
        ------
        KeyError:
            If the task does not exist.
        ValueError:
            On illegal transitions (e.g. COMPLETED -> RUNNING).
        """
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise KeyError(f"unknown task {task_id!r}")
            self._assert_transition(task.status, status)
            self._drop_from_pending(task_id)
            task.status = status
            if error is not None:
                task.error = error
            task.updated_at = utcnow()
            if status is TaskStatus.PENDING:
                self._enqueue(task)
        self._log.info("task.status", task_id=task_id, status=status.value)
        return task

    # ------------------------------------------------------------------ #
    # Messaging
    # ------------------------------------------------------------------ #

    async def add_message(self, task_id: str, message: TaskMessage) -> TaskMessage:
        """Append a structured message to a task's conversation.

        If the message uses ``task_id=""`` it is bound to ``task_id`` at
        insert time.
        """
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise KeyError(f"unknown task {task_id!r}")
            bound = message
            if bound.task_id == "":
                bound = replace(message, task_id=task_id)
            else:
                if bound.task_id != task_id:
                    raise ValueError("message.task_id does not match the target task")
            task.add_message(bound)
        self._log.info("task.message", task_id=task_id, msg_type=bound.type, by=bound.created_by)
        return bound

    async def take_next_pending(self) -> Task | None:
        """Pop the highest-priority pending task, or ``None`` if the queue is empty."""
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
            TaskStatus.PENDING: {TaskStatus.RUNNING, TaskStatus.BLOCKED, TaskStatus.FAILED, TaskStatus.COMPLETED},
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