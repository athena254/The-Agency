"""Tests for Forge Phase 5 merge queue."""

from __future__ import annotations

from pathlib import Path

from agency.forge import MergeQueueStore, evaluate_merge


def test_merge_allowed_all_pass(tmp_path: Path):
    report = evaluate_merge(
        "PASS",
        "PASS",
        "PASS",
        "PASS",
        changed_paths=["src/ok.py"],
        protected_paths=["infra/", "secrets"],
    )
    assert report.merge_allowed is True
    assert report.plan.sealed is True
    assert "never_auto_merge" in report.plan.actions


def test_merge_blocked_protected(tmp_path: Path):
    report = evaluate_merge(
        inspection="PASS",
        security="PASS",
        tests="PASS",
        review="PASS",
        changed_paths=["infra/deploy.yml"],
        protected_paths=["infra/"],
    )
    assert report.merge_allowed is False
    assert any("protected_path_touched" in r for r in report.blocked_reasons)


def test_merge_blocked_gate_fail():
    report = evaluate_merge(
        "FAIL",
        "PASS",
        "PASS",
        "PASS",
        changed_paths=["src/ok.py"],
        protected_paths=[],
    )
    assert report.merge_allowed is False


def test_merge_protected_with_approval():
    report = evaluate_merge(
        "PASS",
        "PASS",
        "PASS",
        "PASS",
        changed_paths=["infra/x.yml"],
        protected_paths=["infra/"],
        approved=True,
    )
    assert report.merge_allowed is True


def test_merge_persistence(tmp_path: Path):
    report = evaluate_merge(
        "PASS",
        "PASS",
        "PASS",
        "PASS",
        changed_paths=["src/ok.py"],
        protected_paths=[],
    )
    db = tmp_path / "m.db"
    store = MergeQueueStore(db)
    store.save(report)
    store.close()
    reopened = MergeQueueStore(db)
    try:
        loaded = reopened.load(report.report_id)
    finally:
        reopened.close()
    assert loaded is not None
    assert loaded.merge_allowed == report.merge_allowed
