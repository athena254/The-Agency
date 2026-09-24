"""Governance API router — proposals, votes, agent spawning (SPEC_LATTICE.md §7)."""

from __future__ import annotations

import os
from secrets import compare_digest
from typing import Any, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from agency.lattice.api import Lattice

log = structlog.get_logger(__name__)


def _require_owner(request: Request) -> None:
    """Fail closed until the deployment provisions an owner-only API token."""
    expected = os.environ.get("AGENCY_GOVERNANCE_TOKEN", "")
    if not expected:
        raise HTTPException(status_code=503, detail="Governance HTTP is not configured")
    supplied = request.headers.get("X-Agency-Governance-Token", "")
    if not compare_digest(supplied, expected):
        raise HTTPException(status_code=403, detail="Governance access denied")


router = APIRouter(
    prefix="/v1/governance", tags=["governance"], dependencies=[Depends(_require_owner)]
)


class ProposeAgentRequest(BaseModel):
    """Request to propose a new agent via governance."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, description="Human-readable agent name.")
    domain: str = Field(default="general", description="Operational domain.")
    capabilities: list[str] = Field(default_factory=list, description="Agent capabilities.")
    quorum: float = Field(default=0.66, ge=0.0, le=1.0, description="Quorum threshold.")
    ttl: int = Field(default=3600, gt=0, description="Proposal TTL in seconds.")


class VoteRequest(BaseModel):
    """The authenticated owner votes as user; HTTP callers cannot name a voter."""

    model_config = ConfigDict(extra="forbid")

    proposal_id: str = Field(min_length=1)
    decision: Literal["approve", "deny", "abstain"] = "abstain"
    evidence: list[str] = Field(default_factory=list)


@router.get("/proposals")
async def list_proposals(request: Request) -> dict[str, Any]:
    """List all open governance proposals."""
    lattice = _get_lattice(request)
    proposals = await lattice.list_open_proposals()
    return {
        "proposals": [
            {
                "proposal_id": p.proposal_id,
                "proposal_type": p.proposal_type,
                "proposer_id": p.proposer_id,
                "quorum_required": p.quorum_required,
                "status": p.status,
                "vote_count": len(p.votes),
                "expires_at": p.expires_at.isoformat() if p.expires_at else None,
            }
            for p in proposals
        ]
    }


@router.post("/propose")
async def propose_agent(payload: ProposeAgentRequest, request: Request) -> dict[str, Any]:
    """Propose a new agent via governance."""
    lattice = _get_lattice(request)

    proposal_id = await lattice.submit_proposal(
        proposer_id="user",
        proposal_type="spawn_agent",
        payload={
            "name": payload.name,
            "domain": payload.domain,
            "capabilities": payload.capabilities,
        },
        quorum=payload.quorum,
        ttl_seconds=payload.ttl,
    )

    return {
        "proposal_id": proposal_id,
        "proposal_type": "spawn_agent",
        "proposer": "user",
        "status": "open",
        "quorum": payload.quorum,
        "vote_count": 0,
    }


@router.post("/vote")
async def vote(request: Request, body: VoteRequest) -> dict[str, Any]:
    """Cast the authenticated owner's vote on an open proposal."""
    lattice = _get_lattice(request)
    result = await lattice.cast_vote(
        voter_id="user",
        proposal_id=body.proposal_id,
        decision=body.decision,
        evidence=body.evidence,
    )
    return result


def _get_lattice(request: Request) -> Lattice:
    """Get the Lattice instance from app state."""
    lattice = getattr(request.app.state, "lattice", None)
    if not isinstance(lattice, Lattice):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Lattice not initialized",
        )
    return lattice
