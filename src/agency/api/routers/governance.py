"""Governance API router — proposals, votes, agent spawning (SPEC_LATTICE.md §7)."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/v1/governance", tags=["governance"])


class ProposeAgentRequest(BaseModel):
    """Request to propose a new agent via governance."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, description="Human-readable agent name.")
    domain: str = Field(default="general", description="Operational domain.")
    capabilities: list[str] = Field(default_factory=list, description="Agent capabilities.")
    proposer: str = Field(default="user", description="Proposer identity.")
    quorum: float = Field(default=0.66, ge=0.0, le=1.0, description="Quorum threshold.")
    ttl: int = Field(default=3600, gt=0, description="Proposal TTL in seconds.")


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
        proposer_id=payload.proposer,
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
        "proposer": payload.proposer,
        "status": "open",
        "quorum": payload.quorum,
        "vote_count": 0,
    }


@router.post("/vote")
async def vote(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """Cast a vote on an open proposal."""
    lattice = _get_lattice(request)
    proposal_id = body.get("proposal_id")
    voter_id = body.get("voter_id", "anonymous")
    decision = body.get("decision", "abstain")
    evidence = body.get("evidence", [])

    if not proposal_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="proposal_id required")

    result = await lattice.cast_vote(
        voter_id=voter_id,
        proposal_id=proposal_id,
        decision=decision,
        evidence=evidence,
    )
    return result


def _get_lattice(request: Request) -> Any:
    """Get the Lattice instance from app state."""
    lattice = getattr(request.app.state, "lattice", None)
    if lattice is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Lattice not initialized",
        )
    return lattice
