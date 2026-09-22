"""Core data models for the Unified Lattice Service.

Implements SPEC_LATTICE.md section 3: node/edge types, immutable events,
governance primitives, and backend configuration.

All models are dependency-light dataclasses speaking ``str``/``dict`` at
boundaries so SQLite, Neo4j, and Qdrant backends can share them.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


def utc_now() -> datetime:
    """Return the current timezone-aware UTC timestamp."""

    return datetime.now(UTC)


def ensure_utc(value: datetime) -> datetime:
    """Attach UTC to naive datetimes so comparisons never fail."""

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class NodeType(str, Enum):
    """Graph node kinds (SPEC_LATTICE.md section 3.1)."""

    AGENT = "agent"
    TASK = "task"
    DELIVERABLE = "deliverable"
    EVIDENCE = "evidence"
    DECISION = "decision"
    POLICY = "policy"
    WING = "wing"
    EVENT = "event"


class EdgeType(str, Enum):
    """Graph relationship kinds (SPEC_LATTICE.md section 3.2)."""

    DEPENDS_ON = "depends_on"
    PRODUCED_BY = "produced_by"
    EVIDENCE_FOR = "evidence_for"
    VOTED_ON = "voted_on"
    GOVERNS = "governs"
    MEMBER_OF = "member_of"
    TRUSTS = "trusts"
    SUPERSEDES = "supersedes"


@dataclass(frozen=True)
class LatticeEvent:
    """An immutable coordination-log entry (SPEC_LATTICE.md section 3.3)."""

    event_id: str
    timestamp: datetime
    event_type: str
    actor: str
    target_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    prior_state: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", ensure_utc(self.timestamp))
        if not self.event_id:
            raise ValueError("event_id must not be empty.")
        if not self.event_type:
            raise ValueError("event_type must not be empty.")
        if not self.actor:
            raise ValueError("actor must not be empty.")
        if not self.target_id:
            raise ValueError("target_id must not be empty.")

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""

        data = asdict(self)
        data["timestamp"] = self.timestamp.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LatticeEvent:
        """Deserialize from :meth:`to_dict` output."""

        raw_ts = data["timestamp"]
        timestamp = (
            datetime.fromisoformat(raw_ts) if isinstance(raw_ts, str) else raw_ts
        )
        return cls(
            event_id=data["event_id"],
            timestamp=ensure_utc(timestamp),
            event_type=data["event_type"],
            actor=data["actor"],
            target_id=data["target_id"],
            payload=dict(data.get("payload") or {}),
            prior_state=(
                dict(data["prior_state"]) if data.get("prior_state") is not None else None
            ),
        )


@dataclass
class Vote:
    """A single governance vote (SPEC_LATTICE.md section 3.4)."""

    voter_id: str
    proposal_id: str
    decision: str
    evidence: list[str] = field(default_factory=list)
    weight: float = 1.0
    timestamp: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        self.timestamp = ensure_utc(self.timestamp)
        if not self.voter_id:
            raise ValueError("voter_id must not be empty.")
        if not self.proposal_id:
            raise ValueError("proposal_id must not be empty.")
        if self.decision not in ("approve", "deny", "abstain"):
            raise ValueError(f"decision {self.decision!r} must be approve|deny|abstain.")
        if self.weight < 0.0:
            raise ValueError("weight must be >= 0.0.")

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""

        data = asdict(self)
        data["timestamp"] = self.timestamp.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Vote:
        """Deserialize from :meth:`to_dict` output."""

        raw_ts = data.get("timestamp")
        timestamp = (
            datetime.fromisoformat(raw_ts)
            if isinstance(raw_ts, str)
            else (raw_ts if raw_ts is not None else utc_now())
        )
        return cls(
            voter_id=data["voter_id"],
            proposal_id=data["proposal_id"],
            decision=data["decision"],
            evidence=list(data.get("evidence") or []),
            weight=float(data.get("weight", 1.0)),
            timestamp=ensure_utc(timestamp),
        )


@dataclass
class Reputation:
    """Agent reputation state (SPEC_LATTICE.md section 3.4)."""

    agent_id: str
    score: float = 0.5
    tasks_completed: int = 0
    tasks_failed: int = 0
    peer_ratings: list[float] = field(default_factory=list)
    last_updated: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        self.last_updated = ensure_utc(self.last_updated)
        if not self.agent_id:
            raise ValueError("agent_id must not be empty.")
        if not 0.0 <= self.score <= 1.0:
            raise ValueError(f"score {self.score} must be within 0.0-1.0.")
        if self.tasks_completed < 0 or self.tasks_failed < 0:
            raise ValueError("task counters must be >= 0.")

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""

        data = asdict(self)
        data["last_updated"] = self.last_updated.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Reputation:
        """Deserialize from :meth:`to_dict` output."""

        raw_ts = data.get("last_updated")
        last_updated = (
            datetime.fromisoformat(raw_ts)
            if isinstance(raw_ts, str)
            else (raw_ts if raw_ts is not None else utc_now())
        )
        return cls(
            agent_id=data["agent_id"],
            score=float(data.get("score", 0.5)),
            tasks_completed=int(data.get("tasks_completed", 0)),
            tasks_failed=int(data.get("tasks_failed", 0)),
            peer_ratings=[float(r) for r in (data.get("peer_ratings") or [])],
            last_updated=ensure_utc(last_updated),
        )


@dataclass
class ConsensusProposal:
    """A governance proposal awaiting quorum (SPEC_LATTICE.md section 3.4)."""

    proposal_id: str
    proposer_id: str
    proposal_type: str
    quorum_required: float = 0.5
    votes: list[Vote] = field(default_factory=list)
    status: str = "open"
    expires_at: datetime = field(
        default_factory=lambda: utc_now().replace(tzinfo=UTC),
    )

    def __post_init__(self) -> None:
        self.expires_at = ensure_utc(self.expires_at)
        if not self.proposal_id:
            raise ValueError("proposal_id must not be empty.")
        if not self.proposer_id:
            raise ValueError("proposer_id must not be empty.")
        if not self.proposal_type:
            raise ValueError("proposal_type must not be empty.")
        if not 0.0 < self.quorum_required <= 1.0:
            raise ValueError("quorum_required must be within (0.0, 1.0].")
        if self.status not in ("open", "passed", "denied", "expired"):
            raise ValueError(f"status {self.status!r} is not a valid proposal status.")

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""

        return {
            "proposal_id": self.proposal_id,
            "proposer_id": self.proposer_id,
            "proposal_type": self.proposal_type,
            "quorum_required": self.quorum_required,
            "votes": [v.to_dict() for v in self.votes],
            "status": self.status,
            "expires_at": self.expires_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConsensusProposal:
        """Deserialize from :meth:`to_dict` output."""

        raw_ts = data["expires_at"]
        expires_at = (
            datetime.fromisoformat(raw_ts) if isinstance(raw_ts, str) else raw_ts
        )
        return cls(
            proposal_id=data["proposal_id"],
            proposer_id=data["proposer_id"],
            proposal_type=data["proposal_type"],
            quorum_required=float(data.get("quorum_required", 0.5)),
            votes=[Vote.from_dict(v) for v in (data.get("votes") or [])],
            status=data.get("status", "open"),
            expires_at=ensure_utc(expires_at),
        )


@dataclass
class LatticeConfig:
    """Backend configuration for the Lattice (SPEC_LATTICE.md section 8).

    Flat view over ``config/lattice.yaml`` + ``LATTICE_*`` env vars.
    See :mod:`agency.lattice.config` for loading and validation.
    """

    backend: str = "sqlite"
    sqlite_path: str = "./data/lattice.db"
    neo4j_uri: str | None = None
    neo4j_user: str | None = None
    neo4j_password: str | None = None
    neo4j_database: str = "theagency"
    qdrant_url: str | None = None
    qdrant_api_key: str | None = None
    vector_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    vector_dimension: int = 384
    event_retention_days: int = 365
    prune_enabled: bool = False
    default_quorum: float = 0.66
    proposal_ttl_seconds: int = 3600
    reputation_window: int = 20

    def __post_init__(self) -> None:
        self.backend = (self.backend or "sqlite").strip().lower()

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""

        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LatticeConfig:
        """Deserialize from :meth:`to_dict` output."""

        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


__all__ = [
    "ConsensusProposal",
    "EdgeType",
    "LatticeConfig",
    "LatticeEvent",
    "NodeType",
    "Reputation",
    "Vote",
    "ensure_utc",
    "utc_now",
]
