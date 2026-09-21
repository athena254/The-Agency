"""Tests for the evidence store: append-only evidence, levels, finding CRUD."""

import pytest

from agency.evidence.levels import EvidenceLevelManager
from agency.evidence.store.models import (
    EvidenceEntry,
    EvidenceLevel,
    Finding,
    Severity,
    VerificationState,
)
from agency.evidence.store.store import EvidenceNotFoundError, EvidenceStore, FindingsFilter


def _finding(**kwargs) -> Finding:
    base = {"target": "staging/api", "evidence": "open port", "methodology": "nmap scan"}
    base.update(kwargs)
    return Finding(**base)


async def test_add_and_get_finding(test_evidence_store: EvidenceStore):
    finding = await test_evidence_store.add_finding(_finding())
    fetched = await test_evidence_store.get_finding(finding.id)
    assert fetched is not None and fetched.id == finding.id
    assert fetched.target == "staging/api"
    assert await test_evidence_store.get_finding("missing") is None


async def test_duplicate_finding_rejected(test_evidence_store: EvidenceStore):
    finding = _finding()
    await test_evidence_store.add_finding(finding)
    with pytest.raises(Exception):
        await test_evidence_store.add_finding(finding)


async def test_list_findings_filters(test_evidence_store: EvidenceStore):
    await test_evidence_store.add_finding(
        _finding(target="staging/api", severity=Severity.HIGH, confidence=0.9))
    await test_evidence_store.add_finding(
        _finding(target="prod/db", severity=Severity.LOW, confidence=0.2,
                 verification_status=VerificationState.VERIFIED))
    assert len(await test_evidence_store.list_findings()) == 2
    assert len(await test_evidence_store.list_findings(
        FindingsFilter(target="staging"))) == 1
    assert len(await test_evidence_store.list_findings(
        FindingsFilter(severity=Severity.HIGH))) == 1
    assert len(await test_evidence_store.list_findings(
        FindingsFilter(verification_status=VerificationState.VERIFIED))) == 1
    assert len(await test_evidence_store.list_findings(
        FindingsFilter(min_confidence=0.8))) == 1
    assert len(await test_evidence_store.list_findings(
        FindingsFilter(max_confidence=0.3))) == 1
    paged = await test_evidence_store.list_findings(FindingsFilter(limit=1, offset=1))
    assert len(paged) == 1


async def test_update_finding_mutable_fields(test_evidence_store: EvidenceStore):
    finding = await test_evidence_store.add_finding(_finding())
    updated = await test_evidence_store.update_finding(
        finding.id, {"severity": Severity.CRITICAL,
                     "verification_status": VerificationState.VERIFIED,
                     "confidence": 0.95})
    assert updated.severity is Severity.CRITICAL
    assert updated.verification_status is VerificationState.VERIFIED
    assert updated.confidence == 0.95


async def test_update_finding_unknown_fields_ignored(test_evidence_store: EvidenceStore):
    finding = await test_evidence_store.add_finding(_finding())
    updated = await test_evidence_store.update_finding(
        finding.id, {"id": "hacked", "nope": 1, "remediation": "patch"})
    assert updated.id == finding.id
    assert updated.remediation == "patch"


async def test_update_missing_finding_raises(test_evidence_store: EvidenceStore):
    with pytest.raises(EvidenceNotFoundError):
        await test_evidence_store.update_finding("missing", {"confidence": 0.1})


async def test_evidence_append_only(test_evidence_store: EvidenceStore):
    finding = await test_evidence_store.add_finding(_finding())
    e1 = await test_evidence_store.add_evidence(EvidenceEntry(
        finding_id=finding.id, level=EvidenceLevel.LEVEL_1_STATIC, data={"tool": "nmap"}))
    e2 = await test_evidence_store.add_evidence(EvidenceEntry(
        finding_id=finding.id, level=EvidenceLevel.LEVEL_2_BEHAVIORAL))
    entries = await test_evidence_store.evidence_for_finding(finding.id)
    assert [e.id for e in entries] == [e1.id, e2.id]  # append order
    # duplicate id replay is idempotent, not an error
    dup = await test_evidence_store.add_evidence(e1)
    assert dup.id == e1.id
    assert len(await test_evidence_store.evidence_for_finding(finding.id)) == 2


async def test_evidence_requires_finding(test_evidence_store: EvidenceStore):
    with pytest.raises(EvidenceNotFoundError):
        await test_evidence_store.add_evidence(EvidenceEntry(
            finding_id="missing", level=EvidenceLevel.LEVEL_1_STATIC))


async def test_latest_evidence_level(test_evidence_store: EvidenceStore):
    finding = await test_evidence_store.add_finding(_finding())
    assert await test_evidence_store.latest_evidence_level(finding.id) is None
    await test_evidence_store.add_evidence(EvidenceEntry(
        finding_id=finding.id, level=EvidenceLevel.LEVEL_1_STATIC))
    await test_evidence_store.add_evidence(EvidenceEntry(
        finding_id=finding.id, level=EvidenceLevel.LEVEL_3_REPEATED))
    latest = await test_evidence_store.latest_evidence_level(finding.id)
    assert latest is EvidenceLevel.LEVEL_3_REPEATED


def test_level_manager_transitions():
    m = EvidenceLevelManager
    assert m.is_valid_level(EvidenceLevel.LEVEL_1_STATIC)
    assert m.can_transition(None, EvidenceLevel.LEVEL_1_STATIC)
    assert m.can_transition(EvidenceLevel.LEVEL_1_STATIC, EvidenceLevel.LEVEL_2_BEHAVIORAL)
    assert not m.can_transition(EvidenceLevel.LEVEL_1_STATIC, EvidenceLevel.LEVEL_3_REPEATED)
    assert not m.can_transition(EvidenceLevel.LEVEL_2_BEHAVIORAL, EvidenceLevel.LEVEL_1_STATIC)
    assert not m.can_transition(None, EvidenceLevel.LEVEL_2_BEHAVIORAL)
    assert m.suggest_next(EvidenceLevel.LEVEL_1_STATIC) is EvidenceLevel.LEVEL_2_BEHAVIORAL
    assert m.suggest_next(EvidenceLevel.LEVEL_5_VERIFIED) is None
    assert m.suggest_next(None) is EvidenceLevel.LEVEL_1_STATIC


def test_level_manager_chain_validation():
    m = EvidenceLevelManager
    assert m.validate_chain([EvidenceLevel.LEVEL_0_HYPOTHESIS, EvidenceLevel.LEVEL_1_STATIC])
    assert m.validate_chain([EvidenceLevel.LEVEL_1_STATIC, EvidenceLevel.LEVEL_2_BEHAVIORAL])
    assert not m.validate_chain([EvidenceLevel.LEVEL_1_STATIC, EvidenceLevel.LEVEL_3_REPEATED])
    assert not m.validate_chain(
        [EvidenceLevel.LEVEL_1_STATIC, EvidenceLevel.LEVEL_0_HYPOTHESIS])
    assert m.validate_chain([])
    assert m.strongest_level([]) is EvidenceLevel.LEVEL_0_HYPOTHESIS
    assert m.strongest_level([EvidenceLevel.LEVEL_1_STATIC,
                              EvidenceLevel.LEVEL_3_REPEATED]) is EvidenceLevel.LEVEL_3_REPEATED


def test_level_labels_and_ranks():
    assert EvidenceLevel.LEVEL_1_STATIC.rank == 1
    assert EvidenceLevel.LEVEL_5_VERIFIED.rank == 5
    assert "Static" in EvidenceLevel.LEVEL_1_STATIC.label
    assert Severity.HIGH.rank > Severity.LOW.rank
