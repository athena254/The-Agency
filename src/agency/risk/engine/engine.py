"""Multi-dimensional risk engine.

The engine transforms a technical :class:`~agency.evidence.store.models.Finding`
into a :class:`~agency.risk.engine.models.RiskModel` — deliberately **not** a
single score. Each dimension is estimated independently from whatever signal the
finding exposes (severity, confidence, reproduction status, evidence ladder
position), and the coarse :class:`RiskCategory` is later derived from the full
vector via weighted thresholds.

The per-dimension heuristics below are *starting points* designed to be swapped
for calibrated data sources (scan feeds, agent telemetry, red-team results)
without changing the engine's contract.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, ClassVar

from structlog import get_logger

from ...evidence.levels import EvidenceLevelManager
from ...evidence.store.models import (
    EvidenceEntry,
    Finding,
    Severity,
    VerificationState,
)
from .models import RiskCategory, RiskModel

logger = get_logger(__name__)


def _clamp(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


_SEVERITY_IMPACT: dict[Severity, float] = {
    Severity.CRITICAL: 1.0,
    Severity.HIGH: 0.8,
    Severity.MEDIUM: 0.6,
    Severity.LOW: 0.35,
    Severity.NEGLIGIBLE: 0.15,
}


class RiskEngine:
    """Estimates the risk vector for a finding and labels it categorically."""

    # High weigh movement, low weigh protective factors.
    WEIGHTS: ClassVar[dict[str, float]] = {
        "impact": 0.30,
        "likelihood": 0.20,
        "exploitability": 0.15,
        "exposure": 0.15,
        "blast_radius": 0.10,
        "reversibility": 0.05,
        "detectability": 0.05,
    }

    # Ordered ascending: thresholds are inclusive upper bounds per category.
    THRESHOLDS: tuple[tuple[RiskCategory, float], ...] = (
        (RiskCategory.NEGLIGIBLE, 0.2),
        (RiskCategory.LOW, 0.4),
        (RiskCategory.MEDIUM, 0.6),
        (RiskCategory.HIGH, 0.8),
        (RiskCategory.CRITICAL, 1.0),
    )

    def calculate_risk(
        self,
        finding: Finding,
        evidence: Sequence[EvidenceEntry] | None = None,
    ) -> RiskModel:
        """Estimate the multi-dimensional risk vector for ``finding``.

        ``evidence`` (optional) raises the certainty of the exploitability and
        detectability estimates: a finding that has been repeatedly reproduced
        is far better understood than a bare hypothesis.
        """
        entries = list(evidence) if evidence else []
        levels = [entry.level for entry in entries]
        strongest = EvidenceLevelManager.strongest_level(levels)
        ladder = strongest.rank

        confidence = finding.confidence
        likelihood = self._estimate_likelihood(finding)

        impact = _SEVERITY_IMPACT[finding.severity] if finding.severity else 0.5
        exposure = self._estimate_exposure(finding)
        exploitability = _clamp(0.3 + 0.14 * ladder)
        detectability = _clamp(0.4 + 0.12 * ladder)
        reversibility = 0.6 if finding.remediation else 0.2
        blast_radius = self._estimate_blast_radius(finding)

        risk = RiskModel(
            finding_id=finding.id,
            impact=impact,
            likelihood=likelihood,
            confidence=confidence,
            exposure=exposure,
            affected_assets=self._affected_assets(finding),
            exploitability=exploitability,
            detectability=detectability,
            reversibility=reversibility,
            blast_radius=blast_radius,
            evidence={
                "strongest_level": strongest.value,
                "evidence_count": len(entries),
            },
        )
        logger.debug(
            "risk_calculated",
            finding_id=finding.id,
            impact=impact,
            likelihood=likelihood,
            category=self.derive_category(risk).value,
        )
        return risk

    def derive_category(self, risk: RiskModel) -> RiskCategory:
        """Derive a coarse category from the full risk vector.

        Composite = sum of weighted, protective-adjusted dimensions, dampened
        by analytic confidence so an *uncertain* HIGH estimate degrades towards
        MEDIUM rather than sounding a false alarm.
        """
        composite = (
            risk.impact * self.WEIGHTS["impact"]
            + risk.likelihood * self.WEIGHTS["likelihood"]
            + risk.exploitability * self.WEIGHTS["exploitability"]
            + risk.exposure * self.WEIGHTS["exposure"]
            + risk.blast_radius * self.WEIGHTS["blast_radius"]
            + (1.0 - risk.reversibility) * self.WEIGHTS["reversibility"]
            + (1.0 - risk.detectability) * self.WEIGHTS["detectability"]
        )
        certainty = _clamp(0.2 + 0.8 * risk.confidence)
        score = round(composite * certainty, 5)

        for category, upper in self.THRESHOLDS:
            if score <= upper:
                return category
        return RiskCategory.NEGLIGIBLE

    def assess(
        self,
        finding: Finding,
        evidence: Sequence[EvidenceEntry] | None = None,
    ) -> tuple[RiskModel, RiskCategory]:
        """Convenience: compute the vector and its category in one call."""
        risk = self.calculate_risk(finding, evidence)
        return risk, self.derive_category(risk)

    # -- dimension heuristics ----------------------------------------------

    @staticmethod
    def _estimate_likelihood(finding: Finding) -> float:
        likelihood = _clamp(0.3 + 0.7 * finding.confidence)

        if finding.verification_status == VerificationState.DISPROVEN:
            likelihood *= 0.05
        elif finding.reproduction_status:
            status = finding.reproduction_status.casefold()
            if "not reproduced" in status or "fail" in status:
                likelihood *= 0.5
            elif "reproduced" in status or "confirmed" in status:
                likelihood *= 1.15
        return _clamp(likelihood)

    @staticmethod
    def _estimate_exposure(finding: Finding) -> float:
        surface_owned = 0.0
        if finding.affected_component:
            surface_owned += 0.2
        assets = RiskEngine._affected_assets(finding)
        surface_owned += _clamp(0.05 * (len(assets) - 1))
        return _clamp(0.5 + surface_owned) if assets else _clamp(0.5)

    @staticmethod
    def _estimate_blast_radius(finding: Finding) -> float:
        assets_count = len(RiskEngine._affected_assets(finding))
        if assets_count == 0:
            return 0.4
        return _clamp(0.4 + 0.1 * (assets_count - 1))

    @staticmethod
    def _affected_assets(finding: Finding) -> list[str]:
        assets: list[Any] = []
        if finding.affected_component:
            assets.append(finding.affected_component)
        provenance_assets = finding.provenance.model_dump().get("assets")
        if isinstance(provenance_assets, list):
            assets.extend(str(item) for item in provenance_assets)
        unique = list(dict.fromkeys(assets))
        return [asset for asset in unique if asset]


__all__ = ["RiskCategory", "RiskEngine", "RiskModel"]