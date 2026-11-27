"""Tests for Forge Phase 3 bounded coding agent."""

from __future__ import annotations

import subprocess
import threading
from datetime import UTC, datetime
from pathlib import Path

from agency.forge import CodingAgentReport, CodingAgentStore, run_coding_agent
from agency.forge.inspection import InspectionReport
from agency.forge.security_scan import SecurityReport


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "m.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


def test_coding_agent_applies_edit(tmp_path: Path):
    repo = _git_repo(tmp_path)
    report = run_coding_agent(repo, "feat/ok", [{"path": "m.py", "old": "x = 1", "new": "x = 2"}])
    assert isinstance(report, CodingAgentReport)
    assert report.gate == "PASS"
    assert report.edits_applied == 1
    assert report.inspection.gate == "PASS"
    assert report.security.gate == "PASS"


def test_coding_agent_fails_on_non_single_occurrence(tmp_path: Path):
    repo = _git_repo(tmp_path)
    (repo / "m.py").write_text("x = 1\nx = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "dup", "--amend"], cwd=repo, check=True)
    report = run_coding_agent(repo, "feat/bad", [{"path": "m.py", "old": "x = 1", "new": "x = 2"}])
    assert report.gate == "FAIL"
    assert report.edits_applied == 0


def test_coding_agent_cleanup(tmp_path: Path):
    repo = _git_repo(tmp_path)
    report = run_coding_agent(
        repo, "feat/clean", [{"path": "m.py", "old": "x = 1", "new": "x = 2"}], cleanup=True
    )
    assert not Path(report.worktree_path).exists()


def test_coding_agent_store_roundtrip(tmp_path: Path):
    repo = _git_repo(tmp_path)
    report = run_coding_agent(
        repo, "feat/store", [{"path": "m.py", "old": "x = 1", "new": "x = 2"}]
    )
    db = tmp_path / "ca.db"
    store = CodingAgentStore(db)
    store.save(report)
    store.close()
    reopened = CodingAgentStore(db)
    try:
        loaded = reopened.load(report.report_id)
    finally:
        reopened.close()
    assert loaded is not None
    assert loaded.edits_applied == report.edits_applied


def test_coding_agent_max_edits_bound(tmp_path: Path):
    repo = _git_repo(tmp_path)
    edits = [{"path": "m.py", "old": "x = 1", "new": "x = 2"} for _ in range(33)]
    report = run_coding_agent(repo, "feat/many", edits)
    assert report.gate == "FAIL"
    assert report.edits_applied == 0


# ---------------------------------------------------------------------------
# CodingAgentStore bootstrap contract (characterization tests for T1).
# These pin the behaviour that the shared open_store helper must preserve.
# They build a minimal valid report directly so each test is self-contained.
# ---------------------------------------------------------------------------


def _report(report_id: str = "rpt-1") -> CodingAgentReport:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return CodingAgentReport(
        report_id=report_id,
        created_at=now,
        worktree_path="wt",
        branch="test",
        edits_applied=1,
        inspection=InspectionReport(report_id="inspection", created_at=now, root="wt", gate="PASS"),
        security=SecurityReport(
            report_id="security",
            created_at=now,
            root="wt",
            gate="PASS",
            scanned_files=0,
            skipped_files=0,
        ),
        gate="PASS",
    )


def test_store_memory_builds_schema_roundtrips_without_directory(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    report = _report()
    with CodingAgentStore(":memory:") as store:
        tables = {
            row[0]
            for row in store._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert "forge_coding_agent_reports" in tables
        store.save(report)
        loaded = store.load(report.report_id)
    assert loaded is not None
    assert loaded.report_id == report.report_id
    assert loaded.edits_applied == report.edits_applied
    assert not (tmp_path / ":memory:").exists()


def test_store_nested_new_directory_creates_parents_and_roundtrips(tmp_path: Path):
    db = tmp_path / "not-yet" / "deep" / "ca.db"
    assert not db.parent.exists()
    report = _report("nested")
    store = CodingAgentStore(db)
    try:
        store.save(report)
        loaded = store.load(report.report_id)
    finally:
        store.close()
    assert db.parent.is_dir()
    assert db.is_file()
    assert loaded is not None
    assert loaded.report_id == report.report_id


def test_store_uses_wal_journal_mode(tmp_path: Path):
    db = tmp_path / "wal.db"
    with CodingAgentStore(db) as store:
        mode = store._conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert str(mode).lower() == "wal"


def test_store_connection_usable_from_second_thread(tmp_path: Path):
    db = tmp_path / "threaded.db"
    report = _report("threaded")
    with CodingAgentStore(db) as store:
        store.save(report)
        loaded: list[CodingAgentReport | None] = []
        errors: list[BaseException] = []

        def worker() -> None:
            try:
                loaded.append(store.load(report.report_id))
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
    assert not errors
    assert len(loaded) == 1
    assert loaded[0] is not None
    assert loaded[0].report_id == report.report_id
