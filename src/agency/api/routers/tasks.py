"""Task router — task intake and result retrieval over :class:`agency.kernel.tasks.TaskManager`.

Mounted by :mod:`agency.api.server` at ``/v1/tasks``.

Endpoints (bearer token or signed grant; see :mod:`agency.api.auth` and
:mod:`agency.api.authority`)
-----------------------------------------------------------------------
POST   /v1/tasks                    Submit a task (``tasks:write`` grant or bearer).
GET    /v1/tasks                    List own tasks (``tasks:read``).
GET    /v1/tasks/{task_id}          Fetch one own task + result (``tasks:read``).
PATCH  /v1/tasks/{task_id}/status   Owner transitions; creators may cancel (``tasks:write``).
POST   /v1/tasks/{task_id}/messages Append a structured message to an own task.

Identity is bound server-side: ``created_by`` comes from the authenticated
principal — the bearer caller or, on the grant path, the verified grant
issuer peer id — never from the request body (owners may submit on behalf of
an agent explicitly). Reads are scoped to the caller's own tasks unless the
principal is the owner.
"""

from __future__ import annotations

from typing import Annotated, Any, cast

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from agency.api.auth import Principal, principal_dependency
from agency.api.authority import intake_dependency
from agency.kernel.registry import AgentRegistry
from agency.kernel.tasks import Task, TaskManager, TaskMessage, TaskStatus

log = structlog.get_logger(__name__)

router = APIRouter(tags=["tasks"])

MAX_DESCRIPTION_CHARS = 8000
MAX_EXTERNAL_PRIORITY = 5


# --------------------------------------------------------------------------- #
# Request models (Pydantic v2)
# --------------------------------------------------------------------------- #


class TaskCreateRequest(BaseModel):
    """Payload for ``POST /v1/tasks``."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="", description="Short human-readable title.")
    created_by: str = Field(
        default="",
        description="Ignored for non-owner callers; owners may submit on behalf of an agent.",
    )
    input: dict[str, Any] = Field(default_factory=dict)
    parent_id: str | None = Field(default=None)
    priority: int = Field(default=0, ge=0, le=10)
    task_id: str | None = Field(default=None, description="Explicit id; generated when omitted.")
    agent_id: str | None = Field(
        default=None, description="Registered agent that should execute this task."
    )


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


def _registry(request: Request) -> AgentRegistry | None:
    return getattr(request.app.state, "agent_registry", None)


def _owns_task(task: Task, principal: Principal) -> bool:
    return principal.role == "owner" or task.created_by == principal.caller_id


def _scoped_created_by(principal: Principal, requested: str) -> str:
    """Bind ``created_by`` to the caller; owners may submit on behalf of an agent."""

    if principal.role == "owner" and requested.strip():
        return requested.strip()
    return principal.caller_id


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #


@router.post("", response_model=Task, status_code=status.HTTP_201_CREATED, summary="Submit a task")
async def create_task(
    payload: TaskCreateRequest,
    request: Request,
    principal: Annotated[Principal, Depends(intake_dependency("POST /v1/tasks", "tasks:write"))],
) -> Task:
    """Create and register a new task for dispatcher execution.

    Authenticated via bearer token or a valid signed, task-scoped grant
    (``X-Agency-Grant`` scoped to ``POST /v1/tasks``); the grant path is
    verified actuator-side and consumes the grant nonce (gaps.md #18).
    """
    manager = _manager(request)
    description = payload.input.get("description")
    if isinstance(description, str) and len(description) > MAX_DESCRIPTION_CHARS:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"description exceeds {MAX_DESCRIPTION_CHARS} chars.",
        )
    if payload.agent_id is not None:
        agent = (_registry(request) or AgentRegistry()).get(payload.agent_id)
        if agent is None or agent.revoked:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"unknown agent_id {payload.agent_id!r}",
            )
    task_input = dict(payload.input)
    if payload.agent_id is not None:
        task_input["agent_id"] = payload.agent_id
    try:
        task = await manager.create_task(
            title=payload.title,
            created_by=_scoped_created_by(principal, payload.created_by),
            input=task_input,
            parent_id=payload.parent_id,
            priority=payload.priority
            if principal.role == "owner"
            else min(payload.priority, MAX_EXTERNAL_PRIORITY),
            task_id=payload.task_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    log.info(
        "api.task.created",
        task_id=task.task_id,
        created_by=task.created_by,
        principal=principal.caller_id,
        role=principal.role,
        auth_kind=getattr(request.state, "auth_kind", "unknown"),
        grant_id=getattr(request.state, "grant_id", None),
    )
    return task


@router.get("", response_model=list[Task], summary="List own tasks")
async def list_tasks(
    request: Request,
    principal: Annotated[Principal, Depends(principal_dependency("tasks:read"))],
    task_status: Annotated[
        TaskStatus | None, Query(alias="status", description="Filter by lifecycle state.")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> list[Task]:
    """List tasks ordered by creation time (scoped to the caller unless owner)."""
    manager = _manager(request)
    created_by = None if principal.role == "owner" else principal.caller_id
    tasks = await manager.list_tasks(status=task_status, created_by=created_by)
    return tasks[:limit]


@router.get("/{task_id}", response_model=Task, summary="Get a task and its result")
async def get_task(
    task_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(principal_dependency("tasks:read"))],
) -> Task:
    """Fetch a single task by id (scoped to the caller unless owner)."""
    manager = _manager(request)
    task = await manager.get_task(task_id)
    if task is None or not _owns_task(task, principal):
        # 404 (not 403) so foreign task ids are not confirmed to exist.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown task {task_id!r}"
        )
    return task


@router.patch("/{task_id}/status", response_model=Task, summary="Transition task status")
async def update_task_status(
    task_id: str,
    payload: TaskStatusUpdateRequest,
    request: Request,
    principal: Annotated[Principal, Depends(principal_dependency("tasks:write"))],
) -> Task:
    """Transition a task into a new lifecycle state.

    Owners may apply any legal transition; other callers may only cancel
    (``blocked``) their own tasks.
    """
    manager = _manager(request)
    if principal.role != "owner":
        task = await manager.get_task(task_id)
        if task is None or task.created_by != principal.caller_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown task {task_id!r}"
            )
        if payload.status is not TaskStatus.BLOCKED:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="callers may only cancel their own tasks (status=blocked)",
            )
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
    task_id: str,
    payload: TaskMessageCreateRequest,
    request: Request,
    principal: Annotated[Principal, Depends(principal_dependency("tasks:write"))],
) -> TaskMessage:
    """Append a structured message to a task's conversation log."""
    manager = _manager(request)
    task = await manager.get_task(task_id)
    if task is None or not _owns_task(task, principal):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown task {task_id!r}"
        )
    message = TaskMessage(
        task_id=task_id,
        type=payload.type,
        content=payload.content,
        metadata=payload.metadata,
        created_by=principal.caller_id,
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
