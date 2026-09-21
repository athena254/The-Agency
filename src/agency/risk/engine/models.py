"""Risk domain models.

Risk in The Agency is a **multi-dimensional vector**, not a single number. A
:class:`RiskModel` carries the independent dimensions used to reason about a
finding; the :class:`RiskCategory` is a coarse, human-facing label derived from
that vector by weighted thresholds (see ``agency.risk.engine.engine``).
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class RiskCategory(str, Enum):
    """Coarse severity label derived from a :class:`RiskModel`."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NEGLIGIBLE = "negligible"

    @property
    def rank(self) -> int:
        """Larger is more severe; ``CRITICAL`` = 4, ``NEGLIGIBLE`` = 0."""
        return _CATEGORY_ORDER[self]


_CATEGORY_ORDER: dict[RiskCategory, int] = {
    RiskCategory.NEGLIGIBLE: 0,
    RiskCategory.LOW: 1,
    RiskCategory.MEDIUM: 2,
    RiskCategory.HIGH: 3,
    RiskCategory.CRITICAL: 4,
}


class RiskModel(BaseModel):
    """Multi-dimensional risk profile for a single finding.

    All numeric dimensions are normalised to ``[0, 1]``.

    * ``impact``            — severity of the worst realistic consequence.
    * ``likelihood``        — probability the consequence materialises.
    * ``confidence``        — how certain we are of the other estimates.
    * ``exposure``          — the share of the surface that is reachable.
    * ``affected_assets``   — enumerated assets in blast path.
    * ``exploitability``    — how easily the weakness can be weaponised
      (high = trivially exploitable).
    * ``detectability``     — how easily the activity is observed/reported
      (high = easy to detect, and therefore *protective*).
    * ``reversibility``     — how fully the damage can be undone
      (high = reversible, therefore *protective*).
    * ``blast_radius``      — reach beyond the directly affected component.

    The two *protective* dimensions (``detectability``, ``reversibility``) are
    scored with higher-is-safer and are inverted when a category is derived.
    """

    finding_id: str | None = Field(default=None)
    impact: float = Field(default=0.0, ge=0.0, le=1.0)
    likelihood: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    exposure: float = Field(default=0.0, ge=0.0, le=1.0)
    affected_assets: list[str] = Field(default_factory=list)
    exploitability: float = Field(default=0.0, ge=0.0, le=1.0)
    detectability: float = Field(default=0.0, ge=0.0, le=1.0)
    reversibility: float = Field(default=0.0, ge=0.0, le=1.0)
    blast_radius: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: dict[str, Any] = Field(default_factory=dict, description="Free-form engine trace")


__all__ = ["RiskCategory", "RiskModel"]