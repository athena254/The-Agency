"""Tests for the guarded self-improvement loop primitives."""

from __future__ import annotations

from pathlib import Path

import pytest

from agency.forge.orchestration import BlacksmithOps, JobState
from agency.forge.self_improve import (
    bridge_issue_to_job,
    detect_problems,
    plan_issue,
    publish_issue,
)


def test_detects_and_deduplicates_failures() -> None:
    reports = [
        {"gate": "FAIL", "task_id": "t1", "note": "bad gate", "source": "review"},
        {"gate": "FAIL", "task_id": "t1", "note": "bad gate", "source": "review"},
        {"gate": "PASS", "task_id": "t2"},
        {"gate": "ERROR", "task_id": "t3", "note": "tests broke", "source": "tests"},
    ]
    candidates = detect_problems(reports)
    assert [candidate.task_id for candidate in candidates] == ["t1", "t3"]
    assert candidates[0].key == candidates[0].key
    assert len({candidate.key for candidate in candidates}) == 2


def test_plan_is_dry_run_and_never_publishes(tmp_path: Path) -> None:
    candidate = detect_problems([{"gate": "FAIL", "task_id": "t1", "note": "bad gate"}])[0]
    draft = plan_issue(candidate, repo="owner/repo")
    assert draft.dry_run is True
    assert draft.commands and "--repo owner/repo" in draft.commands[0]
    assert not (tmp_path / "issue").exists()


def test_publish_requires_approval_and_allowlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AGENCY_FORGE_ALLOWED_ISSUE_REPOS", raising=False)
    candidate = detect_problems([{"gate": "FAIL", "task_id": "t1"}])[0]
    draft = plan_issue(candidate, repo="owner/repo")

    blocked = publish_issue(draft, dry_run=True, proof_log=tmp_path / "proof.jsonl")
    assert blocked.gate == "BLOCKED"
    assert "human_approval_required" in blocked.blocked_reasons

    monkeypatch.setenv("AGENCY_FORGE_ALLOWED_ISSUE_REPOS", "owner/repo")
    planned = publish_issue(
        draft, approved_by="alice", dry_run=True, proof_log=tmp_path / "proof.jsonl"
    )
    assert planned.gate == "PASS"
    assert planned.published is False
    assert not (tmp_path / "proof.jsonl").exists()


def test_bridge_creates_queued_job_only(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    ops = BlacksmithOps(str(tmp_path / "jobs.db"))
    try:
        job = bridge_issue_to_job(
            ops,
            issue_url="https://github.com/owner/repo/issues/1",
            repo_path=str(repo),
            task_id="task-1",
            title="Fix failing gate",
        )
        assert job.state is JobState.QUEUED
        assert ops.awaiting_human() == ()
    finally:
        ops.close()
