"""Tests for the multi-dimensional risk engine and category derivation."""

from agency.evidence.store.models import (
    EvidenceEntry,
    EvidenceLevel,
    Finding,
    Severity,
    VerificationState,
)
from agency.risk.engine.engine import RiskEngine
from agency.risk.engine.models import RiskCategory, RiskModel


def _finding(**kwargs) -> Finding:
    base = {"target": "staging/api", "evidence": "sqli", "methodology": "red-team",
            "confidence": 0.8, "severity": Severity.HIGH,
            "affected_component": "web", "remediation": "parameterize"}
    base.update(kwargs)
    return Finding(**base)


def test_calculate_risk_vector_bounds():
    engine = RiskEngine()
    risk = engine.calculate_risk(_finding())
    for dim in ("impact", "likelihood", "confidence", "exposure",
                "exploitability", "detectability", "reversibility", "blast_radius"):
        assert 0.0 <= getattr(risk, dim) <= 1.0
    assert risk.finding_id is not None
    assert risk.affected_assets == ["web"]


def test_severity_drives_impact():
    engine = RiskEngine()
    crit = engine.calculate_risk(_finding(severity=Severity.CRITICAL))
    low = engine.calculate_risk(_finding(severity=Severity.LOW))
    assert crit.impact > low.impact


def test_evidence_ladder_raises_exploitability():
    engine = RiskEngine()
    finding = _finding()
    bare = engine.calculate_risk(finding, [])
    ladder = engine.calculate_risk(finding, [
        EvidenceEntry(finding_id=finding.id, level=EvidenceLevel.LEVEL_4_REPRODUCED),
        EvidenceEntry(finding_id=finding.id, level=EvidenceLevel.LEVEL_5_VERIFIED),
    ])
    assert ladder.exploitability > bare.exploitability
    assert ladder.detectability > bare.detectability
    assert ladder.evidence["evidence_count"] == 2
    assert ladder.evidence["strongest_level"] == EvidenceLevel.LEVEL_5_VERIFIED.value


def test_disproven_kills_likelihood():
    engine = RiskEngine()
    normal = engine.calculate_risk(_finding())
    disproven = engine.calculate_risk(
        _finding(verification_status=VerificationState.DISPROVEN))
    assert disproven.likelihood < normal.likelihood


def test_reproduction_status_modulates_likelihood():
    engine = RiskEngine()
    base = engine.calculate_risk(_finding()).likelihood
    assert engine.calculate_risk(
        _finding(reproduction_status="reproduced 5/5")).likelihood >= base
    assert engine.calculate_risk(
        _finding(reproduction_status="not reproduced")).likelihood < base


def test_remediation_improves_reversibility():
    engine = RiskEngine()
    assert engine.calculate_risk(_finding(remediation="patch")).reversibility > \
        engine.calculate_risk(_finding(remediation=None)).reversibility


def test_derive_category_monotonic():
    engine = RiskEngine()
    low_risk = RiskModel(confidence=1.0)  # all zeros -> negligible
    assert engine.derive_category(low_risk) is RiskCategory.NEGLIGIBLE
    max_risk = RiskModel(impact=1.0, likelihood=1.0, confidence=1.0, exposure=1.0,
                         exploitability=1.0, detectability=0.0, reversibility=0.0,
                         blast_radius=1.0)
    # With all dimensions at max (except detectability/reversibility which are protective),
    # the weighted score should be high enough for CRITICAL
    assert engine.derive_category(max_risk) is RiskCategory.CRITICAL


def test_uncertainty_dampens_category():
    engine = RiskEngine()
    certain = RiskModel(impact=0.8, likelihood=0.8, confidence=1.0, exposure=0.6,
                        exploitability=0.7, blast_radius=0.5)
    uncertain = certain.model_copy(update={"confidence": 0.0})
    assert engine.derive_category(uncertain).rank <= engine.derive_category(certain).rank


def test_assess_returns_pair():
    engine = RiskEngine()
    finding = _finding()
    risk, category = engine.assess(finding)
    assert isinstance(risk, RiskModel)
    assert isinstance(category, RiskCategory)


def test_category_ranks():
    assert RiskCategory.CRITICAL.rank == 4
    assert RiskCategory.NEGLIGIBLE.rank == 0
    assert RiskCategory.HIGH.rank > RiskCategory.MEDIUM.rank


def test_weights_sum_sane():
    assert abs(sum(RiskEngine.WEIGHTS.values()) - 1.0) < 1e-9
    assert set(RiskEngine.WEIGHTS) >= {"impact", "likelihood", "exploitability"}
