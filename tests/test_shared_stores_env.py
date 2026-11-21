"""Shared durable audit/evidence stores via env vars (gaps.md #19 b).

The api server's audit/memory/evidence stores default to ``:memory:`` (hermetic
per-process), and ``AGENCY_AUDIT_DB`` / ``AGENCY_EVIDENCE_DB`` /
``AGENCY_MEMORY_DB`` point api + butler at shared durable files so audit
trails and evidence rows survive restarts and are visible across processes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agency.evidence.store.models import Finding, Severity, VerificationState
from agency.evidence.store.store import EvidenceStore
from agency.kernel.audit import AuditEntry, AuditLog
from agency.orchestrator import AgencyOrchestrator


def _memory_orchestrator() -> AgencyOrchestrator:
    return AgencyOrchestrator(memory_db_path=":memory:", skill_db_path=":memory:")


def test_orchestrator_stores_default_to_memory() -> None:
    orch = _memory_orchestrator()
    assert str(orch._audit_log._db_path) == ":memory:"
    assert orch._evidence_store.database == ":memory:"


def test_env_vars_point_audit_and_evidence_at_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit_db = tmp_path / "audit.db"
    evidence_db = tmp_path / "evidence.db"
    monkeypatch.setenv("AGENCY_AUDIT_DB", str(audit_db))
    monkeypatch.setenv("AGENCY_EVIDENCE_DB", str(evidence_db))
    orch = _memory_orchestrator()
    assert orch._audit_log._db_path == Path(audit_db)
    assert orch._evidence_store.database == str(evidence_db)


def test_explicit_kwargs_win_over_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENCY_AUDIT_DB", str(tmp_path / "from-env.db"))
    monkeypatch.setenv("AGENCY_EVIDENCE_DB", str(tmp_path / "from-env.db"))
    orch = AgencyOrchestrator(
        memory_db_path=":memory:",
        skill_db_path=":memory:",
        audit_db_path=str(tmp_path / "from-kwarg.db"),
        evidence_db_path=str(tmp_path / "from-kwarg.db"),
    )
    assert orch._audit_log._db_path == Path(tmp_path / "from-kwarg.db")
    assert orch._evidence_store.database == str(tmp_path / "from-kwarg.db")


@pytest.mark.asyncio
async def test_two_processes_share_one_evidence_file(tmp_path: Path) -> None:
    """The mechanism the env vars enable: rows written by 'api' visible to 'butler'."""
    shared = str(tmp_path / "evidence.db")
    api_store = EvidenceStore(shared)
    butler_store = EvidenceStore(shared)
    await api_store.initialize()
    await butler_store.initialize()
    finding = Finding(
        target="shared-target",
        evidence="row written by the api process",
        methodology="agent_execution",
        confidence=0.4,
        severity=Severity.LOW,
        verification_status=VerificationState.UNVERIFIED,
    )
    await api_store.add_finding(finding)
    seen = await butler_store.get_finding(finding.id)
    assert seen is not None
    assert seen.evidence == "row written by the api process"
    await api_store.close()
    await butler_store.close()


@pytest.mark.asyncio
async def test_two_processes_share_one_audit_file(tmp_path: Path) -> None:
    shared = tmp_path / "audit.db"
    api_log = AuditLog(shared)
    butler_log = AuditLog(shared)
    await api_log.initialize()
    await butler_log.initialize()
    await api_log.append(
        AuditEntry(
            agent="api-agent",
            task="t-1",
            capability="execute",
            action="subtask_1",
            result="allowed",
        )
    )
    entries = await butler_log.query()
    assert [e.task for e in entries] == ["t-1"]
    await api_log.close()
    await butler_log.close()
