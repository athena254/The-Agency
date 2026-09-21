"""Pydantic models describing security findings and the evidence supporting them.

Evidence in The Agency is a graded body of observations attached to a finding.
A :class:`Finding` is the claim under investigation; :class:`EvidenceEntry`
records a single observation at a given :class:`EvidenceLevel`. Evidence is
append-only: entries are never mutated or deleted once written to the store.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> datetime:
    """Return the current UTC time (timezone-aware)."""
    return datetime.now(UTC)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class EvidenceLevel(str, Enum):
    """Graded scale of how strongly a claim has been demonstrated.

    The scale is strictly cumulative: each level presupposes every level below
    it. ``LEVEL_0_HYPOTHESIS`` is the default position (no positive evidence)
    that every finding starts from.
    """

    LEVEL_0_HYPOTHESIS = "LEVEL_0_HYPOTHESIS"
    LEVEL_1_STATIC = "LEVEL_1_STATIC"
    LEVEL_2_BEHAVIORAL = "LEVEL_2_BEHAVIORAL"
    LEVEL_3_REPEATED = "LEVEL_3_REPEATED"
    LEVEL_4_REPRODUCED = "LEVEL_4_REPRODUCED"
    LEVEL_5_VERIFIED = "LEVEL_5_VERIFIED"

    @property
    def rank(self) -> int:
        """Zero-based position on the evidence ladder."""
        return _LEVEL_ORDER[self]

    @property
    def label(self) -> str:
        return self.name.replace("LEVEL_", "").replace("_", " ").title()


_LEVEL_ORDER: dict[EvidenceLevel, int] = {
    EvidenceLevel.LEVEL_0_HYPOTHESIS: 0,
    EvidenceLevel.LEVEL_1_STATIC: 1,
    EvidenceLevel.LEVEL_2_BEHAVIORAL: 2,
    EvidenceLevel.LEVEL_3_REPEATED: 3,
    EvidenceLevel.LEVEL_4_REPRODUCED: 4,
    EvidenceLevel.LEVEL_5_VERIFIED: 5,
}


class Severity(str, Enum):
    """Impact severity assigned at discovery time (operational grading).

    Kept intentionally decoupled from :mod:`agency.risk`: a finding's recorded
    severity is descriptive, while derived risk is computed by the risk engine.
    """

    NEGLIGIBLE = "negligible"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return _SEVERITY_ORDER[self]


_SEVERITY_ORDER: dict[Severity, int] = {
    Severity.NEGLIGIBLE: 0,
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}


class VerificationState(str, Enum):
    """Lifecycle status of a finding's verification effort."""

    UNVERIFIED = "unverified"
    IN_PROGRESS = "in_progress"
    PARTIALLY_VERIFIED = "partially_verified"
    VERIFIED = "verified"
    DISPROVEN = "disproven"


class Provenance(BaseModel):
    """Origin metadata describing who, what and how a finding was produced."""

    model_config = ConfigDict(extra="allow")

    source: str = "agency"
    agent_id: str | None = None
    tool: str | None = None
    environment: str | None = None
    chain: list[str] = Field(default_factory=list, description="Agent execution trail")

    @property
    def system(self) -> str | None:
        """Inferred owning system, taken from the environment label if set."""
        if self.environment:
            return self.environment
        return None


class Finding(BaseModel):
    """A technical claim requiring escalation or remediation.

    The finding is the atomic unit of the evidence system: every piece of
    evidence, every risk score and every accuracy measurement eventually
    references exactly one finding.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: _new_id("finding"))
    target: str = Field(..., description="Resource or system the finding concerns")
    timestamp: datetime = Field(default_factory=utcnow)
    evidence: str = Field(..., description="Human-readable summary of the evidence")
    methodology: str = Field(..., description="How the evidence was gathered")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    affected_component: str | None = Field(default=None)
    reproduction_status: str | None = Field(
        default=None, description="Free-form reproduction status, e.g. 'reproduced 3/5 tries'"
    )
    severity: Severity | None = Field(default=None)
    remediation: str | None = Field(default=None)
    verification_status: VerificationState = Field(default=VerificationState.UNVERIFIED)
    provenance: Provenance = Field(default_factory=Provenance)


class EvidenceEntry(BaseModel):
    """A single, immutable observation attached to a finding.

    ``data`` is a free-form payload whose schema is dictated by
    ``methodology``/``source_agent``. Entries are written once and never
    modified (enforced by the store, not just by convention).
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: _new_id("evidence"))
    finding_id: str = Field(...)
    level: EvidenceLevel = Field(...)
    data: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=utcnow)
    source_agent: str | None = Field(default=None)


__all__ = [
    "EvidenceEntry",
    "EvidenceLevel",
    "Finding",
    "Provenance",
    "Severity",
    "VerificationState",
    "utcnow",
]