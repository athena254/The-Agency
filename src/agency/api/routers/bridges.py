"""Bridge router — execution and health over the external coordinator.

Mounted by :mod:`agency.api.server` at ``/v1/bridges``.

Endpoints
---------
GET  /v1/bridges                 List registered bridges + capabilities.
POST /v1/bridges/{name}/execute  Execute a task via the named bridge.
GET  /v1/bridges/{name}/health   Probe one bridge.
"""

from __future__ import annotations

from typing import Any, cast

import structlog
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from agency.bridges.coordinator import BridgeNotFoundError, ExternalCoordinator

log = structlog.get_logger(__name__)

router = APIRouter(tags=["bridges"])


class BridgeExecuteRequest(BaseModel):
    """Payload for ``POST /v1/bridges/{name}/execute``."""

    model_config = ConfigDict(extra="forbid")

    task: str = Field(min_length=1, description="Natural-language instruction for the external agent.")
    context: dict[str, Any] = Field(default_factory=dict)


class BridgeExecuteResponse(BaseModel):
    """Result envelope for a bridged execution."""

    model_config = ConfigDict(extra="forbid")

    bridge: str
    status: str
    output: str = ""
    artifacts: dict[str, Any] = Field(default_factory=dict)
    duration_s: float = 0.0
    error: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)


class BridgeInfo(BaseModel):
    """One registered bridge with its capability descriptor."""

    model_config = ConfigDict(extra="forbid")

    name: str
    capabilities: dict[str, Any] = Field(default_factory=dict)
    circuit: str = "unknown"


def _coordinator(request: Request) -> ExternalCoordinator:
    coordinator = getattr(request.app.state, "bridge_coordinator", None)
    if coordinator is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Bridge coordinator is not initialised.",
        )
    return cast(ExternalCoordinator, coordinator)


@router.get("", response_model=list[BridgeInfo], summary="List bridges")
async def list_bridges(request: Request) -> list[BridgeInfo]:
    """List registered bridges with capabilities and circuit state."""
    coordinator = _coordinator(request)
    try:
        capabilities = await coordinator.capabilities_all()
    except Exception:
        log.exception("api.bridges.capabilities_failed")
        capabilities = {}
    infos: list[BridgeInfo] = []
    for name in coordinator.list_bridges():
        try:
            circuit = str(coordinator.breaker_state(name))
        except BridgeNotFoundError:
            circuit = "unknown"
        infos.append(
            BridgeInfo(name=name, capabilities=capabilities.get(name, {}), circuit=circuit)
        )
    return infos


@router.post("/{name}/execute", response_model=BridgeExecuteResponse, summary="Execute via bridge")
async def execute_bridge(name: str, payload: BridgeExecuteRequest, request: Request) -> BridgeExecuteResponse:
    """Route ``payload.task`` to the named bridge behind its circuit breaker."""
    coordinator = _coordinator(request)
    try:
        result = await coordinator.route_task(payload.task, name, payload.context or None)
    except BridgeNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    log.info("api.bridge.execute", bridge=name, status=result.status.value)
    return BridgeExecuteResponse(
        bridge=name,
        status=result.status.value,
        output=result.output,
        artifacts=result.artifacts,
        duration_s=result.duration_s,
        error=result.error,
        usage={
            "input_tokens": result.usage.input_tokens,
            "output_tokens": result.usage.output_tokens,
            "total_tokens": result.usage.total_tokens,
            "estimated_cost_usd": result.usage.estimated_cost_usd,
        },
    )


@router.get("/{name}/health", summary="Probe one bridge")
async def bridge_health(name: str, request: Request) -> dict[str, Any]:
    """Liveness probe for a single bridge."""
    coordinator = _coordinator(request)
    try:
        bridge = coordinator.get_bridge(name)
    except BridgeNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    try:
        healthy = await bridge.health_check()
    except Exception:
        log.exception("api.bridge.health_failed", bridge=name)
        healthy = False
    return {"bridge": name, "healthy": healthy}


__all__ = ["BridgeExecuteRequest", "BridgeExecuteResponse", "BridgeInfo", "router"]
