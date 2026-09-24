"""Consensus protocol for multi-agent governance (Lattice Brief 3).

Implements SPEC_LATTICE.md section 7.1: proposals are opened, agents cast
reputation-weighted votes, and the engine resolves a proposal as soon as
quorum is reached. The in-memory store is the source of truth; every
mutation is serialized through an ``asyncio.Lock`` (one atomic step per
vote/cast) and mirrored best-effort to the lattice backend via
``create_node`` / ``add_edge`` so the event log stays complete.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from typing import Any

import structlog

from agency.lattice.backends.base import LatticeBackend
from agency.lattice.models import (
    ConsensusProposal,
    EdgeType,
    NodeType,
    Vote,
    ensure_utc,
    utc_now,
)
from agency.lattice.reputation import ReputationEngine

#: Proposal kinds supported by :meth:`GovernanceEngine.submit_proposal`.
PROPOSAL_TYPES: frozenset[str] = frozenset(
    {"spawn_agent", "replace_agent", "policy_change", "agent_restart"}
)

#: Vote decisions accepted by :meth:`GovernanceEngine.cast_vote`.
VOTE_DECISIONS: frozenset[str] = frozenset({"approve", "deny", "abstain"})

log = structlog.get_logger(__name__)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class GovernanceEngine:
    """Consensus protocol for multi-agent governance."""

    def __init__(
        self,
        lattice: LatticeBackend,
        reputation: ReputationEngine | None = None,
    ) -> None:
        self.lattice = lattice
        self.reputation = reputation if reputation is not None else ReputationEngine(lattice)
        self._proposals: dict[str, ConsensusProposal] = {}
        self._payloads: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def submit_proposal(
        self,
        proposer_id: str,
        proposal_type: str,
        payload: dict[str, Any],
        quorum: float = 0.66,
        ttl_seconds: int = 3600,
    ) -> str:
        """Submit a governance proposal and return its proposal ID."""
        if not proposer_id:
            raise ValueError("proposer_id must not be empty.")
        if proposal_type not in PROPOSAL_TYPES:
            raise ValueError(
                f"proposal_type {proposal_type!r} must be one of {sorted(PROPOSAL_TYPES)}."
            )
        if not 0.0 < quorum <= 1.0:
            raise ValueError("quorum must be within (0.0, 1.0].")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be > 0.")
        proposal_id = _new_id("prop")
        now = utc_now()
        proposal = ConsensusProposal(
            proposal_id=proposal_id,
            proposer_id=proposer_id,
            proposal_type=proposal_type,
            quorum_required=float(quorum),
            votes=[],
            status="open",
            expires_at=ensure_utc(now + timedelta(seconds=ttl_seconds)),
        )
        async with self._lock:
            self._proposals[proposal_id] = proposal
            self._payloads[proposal_id] = dict(payload)
        await self._mirror_proposal(proposal, dict(payload))
        return proposal_id

    async def cast_vote(
        self,
        voter_id: str,
        proposal_id: str,
        decision: str,  # 'approve', 'deny', 'abstain'
        evidence: list[str] | None = None,
    ) -> dict[str, Any]:
        """Cast a vote; auto-resolves the proposal when quorum is reached.

        Returns ``{'status': ..., 'quorum_reached': bool}``. A human
        (``user``) approval resolves the proposal immediately. Voting on
        an expired proposal marks it expired; voting on an already
        resolved proposal raises ``ValueError``.
        """
        if not voter_id:
            raise ValueError("voter_id must not be empty.")
        if decision not in VOTE_DECISIONS:
            raise ValueError(f"decision {decision!r} must be approve|deny|abstain.")
        weight = await self.reputation.vote_weight(voter_id)
        async with self._lock:
            proposal = self._require_locked(proposal_id)
            if proposal.status != "open":
                if proposal.status == "expired" or self._is_expired_locked(proposal):
                    self._mark_locked(proposal, "expired")
                    already_expired = True
                else:
                    raise ValueError(f"proposal {proposal_id} is already {proposal.status}.")
            elif self._is_expired_locked(proposal):
                self._mark_locked(proposal, "expired")
                already_expired = True
            else:
                already_expired = False
            if already_expired:
                await self._mirror_resolution_locked(proposal)
                return {"status": "expired", "quorum_reached": False}
            # One vote per voter: a re-vote replaces the earlier ballot.
            votes = [v for v in proposal.votes if v.voter_id != voter_id]
            votes.append(
                Vote(
                    voter_id=voter_id,
                    proposal_id=proposal_id,
                    decision=decision,
                    evidence=list(evidence or []),
                    weight=float(weight),
                    timestamp=utc_now(),
                )
            )
            proposal.votes = votes
            # Human override: a user ballot resolves immediately.
            if voter_id.strip().lower() == "user" and decision in ("approve", "deny"):
                outcome = "passed" if decision == "approve" else "denied"
                self._mark_locked(proposal, outcome)
                resolved = True
            else:
                resolved = self._apply_quorum_locked(proposal)
            status = proposal.status
        await self._mirror_vote(proposal_id, voter_id, decision, weight, list(evidence or []))
        if status != "open":
            await self._mirror_resolution(proposal_id)
        return {"status": status, "quorum_reached": resolved and status == "passed"}

    async def resolve_proposal(self, proposal_id: str) -> str:
        """Tally reputation-weighted votes and resolve the proposal.

        Returns ``'passed'``, ``'denied'``, or ``'expired'``. An explicit
        resolve is decisive: a non-expired proposal without approve-quorum
        is denied.
        """
        async with self._lock:
            proposal = self._require_locked(proposal_id)
            if proposal.status != "open":
                return proposal.status
            if self._is_expired_locked(proposal):
                self._mark_locked(proposal, "expired")
            elif self._approve_ratio_locked(proposal) is not None and self._meets_quorum_locked(
                proposal
            ):
                self._mark_locked(proposal, "passed")
            else:
                self._mark_locked(proposal, "denied")
            status = proposal.status
        await self._mirror_resolution(proposal_id)
        return status

    async def check_quorum(self, proposal_id: str) -> bool:
        """Return True when approve-quorum is reached (auto-resolves).

        Quorum = weighted approve / (weighted approve + weighted deny);
        abstentions are excluded. An expired proposal is marked expired
        and returns False.
        """
        async with self._lock:
            proposal = self._require_locked(proposal_id)
            if proposal.status != "open":
                return proposal.status == "passed"
            if self._is_expired_locked(proposal):
                self._mark_locked(proposal, "expired")
                expired = True
            else:
                expired = False
                reached = self._meets_quorum_locked(proposal)
                if reached:
                    self._mark_locked(proposal, "passed")
            status = proposal.status
        if expired or status != "open":
            await self._mirror_resolution(proposal_id)
        return status == "passed"

    async def get_proposal_status(self, proposal_id: str) -> ConsensusProposal:
        """Return a copy of the full proposal with all votes."""
        async with self._lock:
            proposal = self._require_locked(proposal_id)
            return ConsensusProposal.from_dict(proposal.to_dict())

    async def list_open_proposals(self) -> list[ConsensusProposal]:
        """List all proposals with status ``'open'``."""
        async with self._lock:
            return [
                ConsensusProposal.from_dict(p.to_dict())
                for p in self._proposals.values()
                if p.status == "open"
            ]

    # -- tally helpers (call with self._lock held) ---------------------- #

    def _require_locked(self, proposal_id: str) -> ConsensusProposal:
        try:
            return self._proposals[proposal_id]
        except KeyError:
            raise KeyError(f"unknown proposal: {proposal_id}") from None

    @staticmethod
    def _is_expired_locked(proposal: ConsensusProposal) -> bool:
        return utc_now() >= ensure_utc(proposal.expires_at)

    @staticmethod
    def _mark_locked(proposal: ConsensusProposal, status: str) -> None:
        proposal.status = status

    @staticmethod
    def _tally_locked(proposal: ConsensusProposal) -> tuple[float, float]:
        approve = sum(v.weight for v in proposal.votes if v.decision == "approve")
        deny = sum(v.weight for v in proposal.votes if v.decision == "deny")
        return approve, deny

    def _approve_ratio_locked(self, proposal: ConsensusProposal) -> float | None:
        approve, deny = self._tally_locked(proposal)
        total = approve + deny
        if total <= 0.0:
            return None
        return approve / total

    def _meets_quorum_locked(self, proposal: ConsensusProposal) -> bool:
        ratio = self._approve_ratio_locked(proposal)
        if ratio is None:
            return False
        return ratio >= proposal.quorum_required

    def _apply_quorum_locked(self, proposal: ConsensusProposal) -> bool:
        """Resolve on approve-quorum; return True when resolved to passed."""
        if self._meets_quorum_locked(proposal):
            self._mark_locked(proposal, "passed")
            return True
        return False

    # -- backend mirroring (best-effort, never raises) ------------------ #

    async def _mirror_proposal(self, proposal: ConsensusProposal, payload: dict[str, Any]) -> None:
        create_node = getattr(self.lattice, "create_node", None)
        if create_node is None:
            return
        try:
            await create_node(
                NodeType.DECISION,
                {
                    "proposal_id": proposal.proposal_id,
                    "proposer_id": proposal.proposer_id,
                    "proposal_type": proposal.proposal_type,
                    "quorum_required": proposal.quorum_required,
                    "status": proposal.status,
                    "expires_at": proposal.expires_at.isoformat(),
                    "payload": payload,
                },
                actor=proposal.proposer_id,
            )
        except Exception as exc:  # noqa: BLE001 - best-effort backend mirror
            log.debug("lattice.governance.mirror_proposal_skipped", error=str(exc))

    async def _mirror_vote(
        self,
        proposal_id: str,
        voter_id: str,
        decision: str,
        weight: float,
        evidence: list[str],
    ) -> None:
        """Mirror a vote as an edge so every vote creates a backend event."""
        add_edge = getattr(self.lattice, "add_edge", None)
        if add_edge is None:
            return
        try:
            await add_edge(
                voter_id,
                proposal_id,
                EdgeType.VOTED_ON,
                {"decision": decision, "weight": float(weight), "evidence": evidence},
                actor=voter_id,
            )
        except Exception as exc:  # noqa: BLE001 - best-effort backend mirror
            log.debug("lattice.governance.mirror_vote_skipped", error=str(exc))

    async def _mirror_resolution(self, proposal_id: str) -> None:
        async with self._lock:
            proposal = self._proposals.get(proposal_id)
            snapshot = ConsensusProposal.from_dict(proposal.to_dict()) if proposal else None
        if snapshot is not None:
            await self._mirror_resolution_locked(snapshot)

    async def _mirror_resolution_locked(self, proposal: ConsensusProposal) -> None:
        create_node = getattr(self.lattice, "create_node", None)
        if create_node is None:
            return
        approve, deny = self._tally_locked(proposal)
        try:
            await create_node(
                NodeType.DECISION,
                {
                    "proposal_id": proposal.proposal_id,
                    "resolution": proposal.status,
                    "approve_weight": approve,
                    "deny_weight": deny,
                    "vote_count": len(proposal.votes),
                },
                actor="governance",
            )
        except Exception as exc:  # noqa: BLE001 - best-effort backend mirror
            log.debug("lattice.governance.mirror_resolution_skipped", error=str(exc))


__all__ = ["PROPOSAL_TYPES", "VOTE_DECISIONS", "GovernanceEngine"]
