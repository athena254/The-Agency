"""Authenticated local owner-pilot transport over the real execution service."""

from __future__ import annotations

import re
from typing import Annotated, Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agency.admission.models import (
    OwnerError,
    OwnerPrincipal,
    OwnerTaskRequest,
    PilotCapabilities,
    PilotError,
)
from agency.admission.service import OwnerPilotService
from agency.prototype.owner_guard import guard_owner_request

router = APIRouter(prefix="/api/pilot", tags=["owner-pilot"])
_RequestId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$", max_length=128)]


class NewThread(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: _RequestId
    title: Annotated[str, Field(min_length=1, max_length=120)]
    parent_thread_id: Annotated[str, Field(min_length=1, max_length=128)] | None = None


class CancelTask(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: _RequestId


def pilot_error_response(exc: PilotError) -> JSONResponse:
    """Use only the fixed public error catalog, never raw exception text."""
    safe = PilotError(exc.code, uncertain=exc.uncertain)
    body = OwnerError(
        error=str(safe),
        code=safe.code,
        request_id=None,
        task_id=None,
        uncertain=safe.uncertain,
        retryable=False,
    )
    return JSONResponse(status_code=safe.status_code, content=body.model_dump(mode="json"))


async def _boundary(
    request: Request, *, write: bool = False
) -> tuple[OwnerPilotService, OwnerPrincipal, dict[str, Any] | None]:
    principal, body = await guard_owner_request(
        request,
        getattr(request.app.state, "owner_authenticator", None),
        csrf_token=getattr(request.app.state, "csrf_token", None),
        write=write,
    )
    pilot = getattr(request.app.state, "pilot", None)
    if type(pilot) is not OwnerPilotService:
        raise PilotError("disabled")
    return pilot, principal, body


@router.get("/capabilities", response_model=PilotCapabilities)
async def capabilities(request: Request) -> PilotCapabilities:
    pilot, principal, _ = await _boundary(request)
    return await pilot.capabilities(principal)


@router.get("/threads")
async def threads(request: Request) -> JSONResponse:
    pilot, principal, _ = await _boundary(request)
    rows = await pilot.list_threads(principal)
    return JSONResponse(content={"threads": [row.model_dump(mode="json") for row in rows]})


@router.post("/threads")
async def create_thread(request: Request) -> JSONResponse:
    pilot, principal, body = await _boundary(request, write=True)
    try:
        payload = NewThread.model_validate(body)
    except ValidationError:
        raise PilotError("invalid_request") from None
    receipt, thread = await pilot.create_thread(
        principal, payload.request_id, payload.title, payload.parent_thread_id
    )
    return JSONResponse(
        status_code=200 if receipt.replayed else 201,
        content={
            "request": receipt.model_dump(mode="json"),
            "thread": thread.model_dump(mode="json"),
        },
    )


@router.get("/threads/{thread_id}/messages")
async def messages(request: Request, thread_id: str) -> JSONResponse:
    pilot, principal, _ = await _boundary(request)
    rows = await pilot.list_messages(principal, thread_id)
    return JSONResponse(content={"messages": [row.model_dump(mode="json") for row in rows]})


@router.post("/tasks")
async def submit(request: Request) -> JSONResponse:
    pilot, principal, body = await _boundary(request, write=True)
    try:
        payload = OwnerTaskRequest.model_validate(body)
    except ValidationError:
        raise PilotError("invalid_request") from None
    view = await pilot.submit(principal, payload)
    return JSONResponse(
        status_code=200 if view.replayed else 202, content=view.model_dump(mode="json")
    )


@router.get("/tasks")
async def tasks(request: Request) -> JSONResponse:
    pilot, principal, _ = await _boundary(request)
    values = request.query_params.getlist("limit")
    raw = values[0] if values else "50"
    if len(values) > 1 or re.fullmatch(r"[0-9]{1,3}", raw) is None or not 1 <= int(raw) <= 100:
        raise PilotError("invalid_request")
    rows = await pilot.list_tasks(principal, limit=int(raw))
    return JSONResponse(content={"tasks": [row.model_dump(mode="json") for row in rows]})


@router.get("/tasks/{task_id}")
async def task(request: Request, task_id: str) -> JSONResponse:
    pilot, principal, _ = await _boundary(request)
    view = await pilot.get_task(principal, task_id)
    return JSONResponse(content=view.model_dump(mode="json"))


@router.post("/tasks/{task_id}/cancel")
async def cancel(request: Request, task_id: str) -> JSONResponse:
    pilot, principal, body = await _boundary(request, write=True)
    try:
        payload = CancelTask.model_validate(body)
    except ValidationError:
        raise PilotError("invalid_request") from None
    view = await pilot.cancel(principal, task_id, payload.request_id)
    return JSONResponse(
        status_code=200 if view.replayed else 202,
        content={"task": view.model_dump(mode="json")},
    )
