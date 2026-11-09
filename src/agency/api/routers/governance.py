"""Governance API router — proposals, votes, agent spawning (SPEC_LATTICE.md §7)."""

from __future__ import annotations

import base64
import binascii
from typing import Any, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from agency.api.authority import require_peer_authority
from agency.lattice.api import Lattice
from agency.peers.authority import AuthorityError, PeerAuthority

log = structlog.get_logger(__name__)


router = APIRouter(
    prefix="/v1/governance", tags=["governance"], dependencies=[Depends(require_peer_authority)]
)

# Signed ballots are authorized by their Ed25519 signature, not by a token,
# so this router carries no require_peer_authority dependency. Without a
# configured PeerAuthority it still fails closed (503).
ballot_router = APIRouter(prefix="/v1/governance", tags=["governance"])


class ProposeAgentRequest(BaseModel):
    """Request to propose a new agent via governance."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, description="Human-readable agent name.")
    domain: str = Field(default="general", description="Operational domain.")
    capabilities: list[str] = Field(default_factory=list, description="Agent capabilities.")
    quorum: float = Field(default=0.66, ge=0.0, le=1.0, description="Quorum threshold.")
    ttl: int = Field(default=3600, gt=0, description="Proposal TTL in seconds.")


class VoteRequest(BaseModel):
    """Legacy unsigned vote body. Kept so old clients get a clear 400."""

    model_config = ConfigDict(extra="forbid")

    proposal_id: str = Field(min_length=1)
    decision: Literal["approve", "deny", "abstain"] = "abstain"
    evidence: list[str] = Field(default_factory=list)


class BallotRequest(BaseModel):
    """A signed peer ballot as wire bytes: hex or base64 of canonical_json."""

    model_config = ConfigDict(extra="forbid")

    wire_hex: str | None = Field(default=None, min_length=2)
    wire_base64: str | None = Field(default=None, min_length=2)


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


@ballot_router.post("/vote", deprecated=True)
async def vote(request: Request) -> JSONResponse:
    """Legacy unsigned vote (``voter_id=user``). Fail closed, no override.

    Unsigned votes are never written; clients must submit a signed ballot
    to POST /v1/governance/ballot instead.
    """
    log.warning("legacy_unsigned_vote_rejected", path=request.url.path)
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={
            "detail": "Unsigned votes are rejected. Submit a signed ballot to "
            "/v1/governance/ballot."
        },
        headers={"Deprecation": "true", "Link": '</v1/governance/ballot>; rel="successor-version"'},
    )


@ballot_router.post("/ballot")
async def ballot(payload: BallotRequest, request: Request) -> dict[str, Any]:
    """Verify a signed peer ballot via PeerAuthority and return tally/decide."""
    authority = _get_authority(request)
    wire = _decode_wire(payload)
    try:
        msg = authority.submit_ballot(wire)
    except AuthorityError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    proposal_id = str(msg.payload["proposal_id"])
    outcome = authority.decide(proposal_id)
    return {
        "proposal_id": proposal_id,
        "voter_peer_id": msg.sender_peer_id,
        "decision": str(msg.payload["decision"]),
        "ballot_seq": int(msg.payload["ballot_seq"]),
        "tally": authority.tally(proposal_id),
        "quorum_required": authority.quorum_required(),
        "quorum_met": outcome == "passed",
        "outcome": outcome,
    }


def _decode_wire(payload: BallotRequest) -> bytes:
    if (payload.wire_hex is None) == (payload.wire_base64 is None):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide exactly one of wire_hex or wire_base64.",
        )
    try:
        if payload.wire_hex is not None:
            return bytes.fromhex(payload.wire_hex)
        assert payload.wire_base64 is not None
        return base64.b64decode(payload.wire_base64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid ballot encoding."
        ) from exc


def _get_authority(request: Request) -> PeerAuthority:
    """Get the PeerAuthority instance from app state (per-test injectable)."""
    authority = getattr(request.app.state, "peer_authority", None)
    if not isinstance(authority, PeerAuthority):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Peer authority not initialized",
        )
    return authority


def _get_lattice(request: Request) -> Lattice:
    """Get the Lattice instance from app state."""
    lattice = getattr(request.app.state, "lattice", None)
    if not isinstance(lattice, Lattice):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Lattice not initialized",
        )
    return lattice
