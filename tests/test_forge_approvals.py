"""Tests for the human approval surface."""

from __future__ import annotations

from pathlib import Path

import pytest

from agency.forge.approvals import ApprovalError, decide, list_briefs
from agency.forge.orchestration import BlacksmithOps, JobState


def _awaiting(tmp_path: Path) -> tuple[BlacksmithOps, str]:
    ops = BlacksmithOps(str(tmp_path / "jobs.db"))
    job = ops.submit("build slice", "do the work", str(tmp_path / "repo"))
    ops._transition(job, JobState.AWAITING_HUMAN, "test_setup")
    return ops, job.job_id


def test_lists_only_awaiting_jobs(tmp_path: Path) -> None:
    ops, job_id = _awaiting(tmp_path)
    try:
        briefs = list_briefs(ops)
        assert [brief.job_id for brief in briefs] == [job_id]
        assert briefs[0].state == JobState.AWAITING_HUMAN.value
    finally:
        ops.close()


def test_approve_records_without_shipping(tmp_path: Path) -> None:
    ops, job_id = _awaiting(tmp_path)
    try:
        record = decide(ops, job_id, "alice", "approve", note="looks good")
        assert record.decision == "approve"
        assert record.state_after == JobState.AWAITING_HUMAN.value
        assert "no_release_authorization" in record.caveats
    finally:
        ops.close()


def test_reject_blocks_the_job(tmp_path: Path) -> None:
    ops, job_id = _awaiting(tmp_path)
    try:
        record = decide(ops, job_id, "alice", "reject")
        assert record.state_after == JobState.BLOCKED.value
        assert list_briefs(ops) == ()
    finally:
        ops.close()


def test_generic_approver_and_bad_decision_are_refused(tmp_path: Path) -> None:
    ops, job_id = _awaiting(tmp_path)
    try:
        with pytest.raises(ApprovalError):
            decide(ops, job_id, "agent", "approve")
        with pytest.raises(ApprovalError):
            decide(ops, job_id, "alice", "ship")
    finally:
        ops.close()
