"""Focused tests for the guarded CI-failure to improvement bridge."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agency.forge.ci_feedback import CandidateJob, ingest_ci_failures
from agency.forge.improvement_bridge import (
    MAX_BRIDGED_JOBS,
    BridgeReport,
    bridge_failures,
)


def _candidate(task_id: str, detail: str = "boom", **extra: object) -> CandidateJob:
    payload: dict[str, object] = {
        "gate": "FAIL",
        "task_id": task_id,
        "detail": detail,
        "source": "pytest",
    }
    payload.update(extra)
    return ingest_ci_failures([payload]).candidates[0]


def test_dedupes_candidates_on_stable_keys_and_counts_duplicates() -> None:
    first = _candidate("t1", "same")
    dupe = _candidate("t1", "same")
    other = _candidate("t2", "other")
    assert first.key == dupe.key
    assert first.key != other.key

    report = bridge_failures([first, dupe, other])

    assert isinstance(report, BridgeReport)
    assert report.received == 3
    assert report.duplicates == 1
    assert report.candidates == (first, other)
    assert report.keys == (first.key, other.key)
    assert report.truncated == 0


@pytest.mark.parametrize(
    "approver",
    ["", "   ", "auto", "ci", "agent", "forge", "human", "n/a", "test", "1234"],
)
def test_no_proposals_without_named_human_approver(approver: str) -> None:
    candidate = _candidate("t1")

    report = bridge_failures([candidate], approved_by=approver)

    assert report.proposals == ()
    assert report.gate == "BLOCKED"
    assert report.approved is False
    assert report.approved_by == ""
    assert "human_approval_required" in report.blocked_reasons
    assert report.candidates == (candidate,)  # captured, never acted on


def test_named_approver_produces_capture_only_proposals() -> None:
    first, second = _candidate("t1"), _candidate("t2")

    report = bridge_failures([first, second], approved_by="Alice Smith")

    assert report.gate == "PASS"
    assert report.approved is True
    assert report.approved_by == "Alice Smith"
    assert report.blocked_reasons == ()
    assert [proposal.candidate_key for proposal in report.proposals] == [first.key, second.key]
    assert all(proposal.approved_by == "Alice Smith" for proposal in report.proposals)
    assert all("no_auto_dispatch" in proposal.caveats for proposal in report.proposals)
    assert "capture_only" in report.caveats
    assert "no_auto_merge" in report.caveats
    assert "no_push" in report.caveats


def test_seen_keys_make_reruns_idempotent() -> None:
    candidate = _candidate("t1")

    first = bridge_failures([candidate], approved_by="Alice")
    second = bridge_failures([candidate], approved_by="Alice", seen_keys=first.keys)

    assert first.proposals
    assert second.received == 1
    assert second.duplicates == 1
    assert second.candidates == ()
    assert second.proposals == ()
    assert second.keys == ()
    assert second.gate == "PASS"  # nothing left that needs approval


def test_bridge_never_dispatches_merges_or_pushes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agency.forge import orchestration

    def _no_side_effect(*args: object, **kwargs: object) -> object:
        raise AssertionError("a job was dispatched, merged or pushed")

    monkeypatch.setattr(orchestration.BlacksmithOps, "submit", _no_side_effect)
    for name in ("dispatch", "merge", "push"):
        if hasattr(orchestration.BlacksmithOps, name):
            monkeypatch.setattr(orchestration.BlacksmithOps, name, _no_side_effect)

    report = bridge_failures([_candidate("t1")], approved_by="Alice")

    assert len(report.proposals) == 1


def test_secrets_are_never_copied_into_report() -> None:
    candidates = ingest_ci_failures(
        [{"gate": "FAIL", "task_id": "t1", "detail": 'api_key = "supersecretvalue123"'}]
    ).candidates

    report = bridge_failures(candidates, approved_by="Alice")
    blob = report.model_dump_json()

    assert "supersecretvalue123" not in blob
    assert "[REDACTED_SECRET]" in blob


def test_candidate_list_is_bounded() -> None:
    many = [_candidate(f"t{i}") for i in range(MAX_BRIDGED_JOBS + 25)]

    report = bridge_failures(many, approved_by="Alice")

    assert report.received == MAX_BRIDGED_JOBS + 25
    assert len(report.candidates) == MAX_BRIDGED_JOBS
    assert report.truncated == 25
    assert len(report.proposals) == MAX_BRIDGED_JOBS
    assert len(report.keys) == MAX_BRIDGED_JOBS


def test_report_is_immutable_and_validated() -> None:
    report = bridge_failures([_candidate("t1")])

    with pytest.raises(ValidationError):
        BridgeReport(gate="PASS", received=-1, duplicates=0, truncated=0)
    with pytest.raises(ValidationError):
        report.gate = "PASS"
