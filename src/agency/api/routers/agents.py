"""Agent router — CRUD operations over :class:`agency.kernel.registry.AgentRegistry`.

Mounted by :mod:`agency.api.server` at ``/v1/agents``.

Endpoints
---------
GET    /v1/agents                        List agents.
POST   /v1/agents                        Register an agent.
GET    /v1/agents/{agent_id}             Fetch one agent.
DELETE /v1/agents/{agent_id}             Revoke an agent identity.
PATCH  /v1/agents/{agent_id}/capabilities Grant/revoke capabilities.
"""

from __future__ import annotations

from typing import Any, Literal, cast

import structlog
from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from agency.kernel.identity import Agent, Capability, TrustLevel
from agency.kernel.registry import AgentRegistry

log = structlog.get_logger(__name__)

router = APIRouter(tags=["agents"])


# --------------------------------------------------------------------------- #
# Request models (Pydantic v2, kernel-anchored)
# --------------------------------------------------------------------------- #

class AgentCreateRequest(BaseModel):
    """Payload for ``POST /v1/agents``. Mirrors :class:`agency.kernel.identity.Agent`."""

    model_config = ConfigDict(extra="forbid")

    id: str | None = Field(default=None, description="Explicit id; generated when omitted.")
    name: str = Field(min_length=1)
    domain: str = Field(default="general")
    capabilities: list[str | Capability] = Field(default_factory=list)
    trust_level: TrustLevel = Field(default=TrustLevel.UNKNOWN)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CapabilityUpdateRequest(BaseModel):
    """Payload for ``PATCH /v1/agents/{id}/capabilities``."""

    model_config = ConfigDict(extra="forbid")

    capabilities: list[str | Capability] = Field(min_length=1)
    mode: Literal["merge", "replace"] = Field(default="merge")


# --------------------------------------------------------------------------- #
# Dependencies
# --------------------------------------------------------------------------- #

def _registry(request: Request) -> AgentRegistry:
    registry = getattr(request.app.state, "agent_registry", None)
    if registry is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Agent registry is not initialised.",
        )
    return cast(AgentRegistry, registry)


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #

@router.get("", response_model=list[Agent], summary="List agents")
def list_agents(
    request: Request,
    domain: str | None = Query(default=None, description="Restrict to a single domain."),
    include_revoked: bool = Query(default=False, description="Include revoked identities."),
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[Agent]:
    """List registered agents, newest first."""
    registry = _registry(request)
    return registry.list_agents(include_revoked=include_revoked, domain=domain)[:limit]


@router.post("", response_model=Agent, status_code=status.HTTP_201_CREATED, summary="Register an agent")
def register_agent(payload: AgentCreateRequest, request: Request) -> Agent:
    """Register a new agent identity (grants no permissions by itself)."""
    from agency.kernel.identity import Agent as AgentModel

    registry = _registry(request)
    fields: dict[str, Any] = payload.model_dump(exclude_none=True)
    try:
        agent = AgentModel(**fields)
        return registry.register(agent)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/{agent_id}", response_model=Agent, summary="Get an agent")
def get_agent(agent_id: str, request: Request) -> Agent:
    """Fetch one agent by id."""
    registry = _registry(request)
    agent = registry.get(agent_id)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown agent {agent_id!r}")
    return agent


@router.delete("/{agent_id}", response_model=Agent, summary="Revoke an agent")
def revoke_agent(agent_id: str, request: Request) -> Agent:
    """Revoke an agent identity in place (record retained for audit)."""
    registry = _registry(request)
    try:
        agent = registry.revoke(agent_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    log.warning("api.agent.revoked", agent_id=agent_id)
    return agent


@router.patch("/{agent_id}/capabilities", response_model=Agent, summary="Update capabilities")
def update_capabilities(agent_id: str, payload: CapabilityUpdateRequest, request: Request) -> Agent:
    """Merge or replace an agent's capability set."""
    registry = _registry(request)
    try:
        return registry.update_capabilities(
            agent_id, payload.capabilities, replace=(payload.mode == "replace")
        )
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


__all__ = ["AgentCreateRequest", "CapabilityUpdateRequest", "router"]
