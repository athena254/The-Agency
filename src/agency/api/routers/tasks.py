"""Task router — CRUD operations over :class:`agency.kernel.tasks.TaskManager`.

Mounted by :mod:`agency.api.server` at ``/v1/tasks``.

Endpoints
---------
POST   /v1/tasks                    Create a task.
GET    /v1/tasks                    List tasks (``status``, ``limit`` filters).
GET    /v1/tasks/{task_id}          Fetch one task.
PATCH  /v1/tasks/{task_id}/status   Transition lifecycle state.
POST   /v1/tasks/{task_id}/messages Append a structured message.
"""

from __future__ import annotations

from typing import Annotated, Any, cast

import structlog
from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from agency.kernel.tasks import Task, TaskManager, TaskMessage, TaskStatus

log = structlog.get_logger(__name__)

router = APIRouter(tags=["tasks"])


# --------------------------------------------------------------------------- #
# Request models (Pydantic v2)
# --------------------------------------------------------------------------- #


class TaskCreateRequest(BaseModel):
    """Payload for ``POST /v1/tasks``."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="", description="Short human-readable title.")
    created_by: str = Field(min_length=1, description="Agent or operator id.")
    input: dict[str, Any] = Field(default_factory=dict)
    parent_id: str | None = Field(default=None)
    priority: int = Field(default=0, ge=0, le=10)
    task_id: str | None = Field(default=None, description="Explicit id; generated when omitted.")


class TaskStatusUpdateRequest(BaseModel):
    """Payload for ``PATCH /v1/tasks/{id}/status``."""

    model_config = ConfigDict(extra="forbid")

    status: TaskStatus
    error: str | None = Field(default=None)


class TaskMessageCreateRequest(BaseModel):
    """Payload for ``POST /v1/tasks/{id}/messages``."""

    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1)
    content: Any = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_by: str = Field(default="")


# --------------------------------------------------------------------------- #
# Dependencies
# --------------------------------------------------------------------------- #


def _manager(request: Request) -> TaskManager:
    manager = getattr(request.app.state, "task_manager", None)
    if manager is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Task manager is not initialised.",
        )
    return cast(TaskManager, manager)


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #


@router.post("", response_model=Task, status_code=status.HTTP_201_CREATED, summary="Create a task")
async def create_task(payload: TaskCreateRequest, request: Request) -> Task:
    """Create and register a new task."""
    manager = _manager(request)
    try:
        task = await manager.create_task(
            title=payload.title,
            created_by=payload.created_by,
            input=payload.input,
            parent_id=payload.parent_id,
            priority=payload.priority,
            task_id=payload.task_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    log.info("api.task.created", task_id=task.task_id, created_by=payload.created_by)
    return task


@router.get("", response_model=list[Task], summary="List tasks")
async def list_tasks(
    request: Request,
    task_status: Annotated[
        TaskStatus | None, Query(alias="status", description="Filter by lifecycle state.")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> list[Task]:
    """List tasks ordered by creation time (earliest first)."""
    manager = _manager(request)
    tasks = await manager.list_tasks(status=task_status)
    return tasks[:limit]


@router.get("/{task_id}", response_model=Task, summary="Get a task")
async def get_task(task_id: str, request: Request) -> Task:
    """Fetch a single task by id."""
    manager = _manager(request)
    task = await manager.get_task(task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown task {task_id!r}"
        )
    return task


@router.patch("/{task_id}/status", response_model=Task, summary="Transition task status")
async def update_task_status(
    task_id: str, payload: TaskStatusUpdateRequest, request: Request
) -> Task:
    """Transition a task into a new lifecycle state."""
    manager = _manager(request)
    try:
        return await manager.update_status(task_id, payload.status, error=payload.error)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post(
    "/{task_id}/messages",
    response_model=TaskMessage,
    status_code=status.HTTP_201_CREATED,
    summary="Append a task message",
)
async def add_task_message(
    task_id: str, payload: TaskMessageCreateRequest, request: Request
) -> TaskMessage:
    """Append a structured message to a task's conversation log."""
    manager = _manager(request)
    message = TaskMessage(
        task_id=task_id,
        type=payload.type,
        content=payload.content,
        metadata=payload.metadata,
        created_by=payload.created_by,
    )
    try:
        return await manager.add_message(task_id, message)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


__all__ = ["TaskCreateRequest", "TaskMessageCreateRequest", "TaskStatusUpdateRequest", "router"]
