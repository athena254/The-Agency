"""Focused tests for bounded, capture-only CI/gate-failure ingestion."""

from __future__ import annotations

import json

import pytest

from agency.forge.ci_feedback import (
    MAX_EVENTS,
    ApprovalError,
    CandidateJob,
    authorize_candidate,
    ingest_ci_failures,
    is_valid_approver,
    parse_ci_failures,
    redact_secrets,
)


def _candidate() -> CandidateJob:
    return ingest_ci_failures(
        [{"gate": "FAIL", "task_id": "t1", "detail": "bad gate", "source": "pytest"}]
    ).candidates[0]


def test_parse_json_failures_are_bounded_and_keyed() -> None:
    payload = [
        {"gate": "FAIL", "task_id": "t1", "detail": "bad gate", "source": "review"},
        {
            "gate": "ERROR",
            "job_id": "t2",
            "message": "tests broke",
            "test_paths": ["tests/test_a.py", "tests/test_b.py"],
        },
        {"gate": "PASS", "task_id": "t3"},
    ]
    events = parse_ci_failures(payload)
    assert [event.task_id for event in events] == ["t1", "t2"]
    assert [event.gate for event in events] == ["FAIL", "ERROR"]
    assert events[1].test_paths == ("tests/test_a.py", "tests/test_b.py")
    assert len({event.key for event in events}) == 2
    assert all(event.key for event in events)


def test_parse_nested_json_plain_text_and_bytes() -> None:
    nested = ingest_ci_failures(
        {"failures": [{"conclusion": "failure", "name": "lint", "output": "E501"}]}
    )
    assert nested.failures == 1
    assert nested.candidates[0].task_id == "lint"

    text = "FAILED tests/test_x.py - AssertionError\nPASS all good\nERROR boom"
    events = parse_ci_failures(text)
    assert [event.gate for event in events] == ["FAIL", "ERROR"]
    assert "AssertionError" in events[0].detail

    raw = json.dumps([{"gate": "FAIL", "task_id": "t2"}]).encode("utf-8")
    assert ingest_ci_failures(raw).failures == 1


def test_boolean_failure_flags_are_ingested() -> None:
    report = ingest_ci_failures([{"name": "check", "success": False, "ok": False}])
    assert report.failures == 1
    assert report.candidates[0].task_id == "check"


def test_deduplication_and_stable_keys_across_runs() -> None:
    payload = {
        "failures": [
            {"task_id": "t1", "gate": "FAIL", "detail": "same failure"},
            {"task_id": "t1", "gate": "FAIL", "detail": "same failure"},
        ]
    }
    first = ingest_ci_failures(payload)
    assert first.failures == 2
    assert len(first.candidates) == 1
    assert first.duplicates == 1

    second = ingest_ci_failures(payload, seen_keys=first.keys)
    assert second.failures == 2
    assert second.candidates == ()
    assert second.duplicates == 2

    again = ingest_ci_failures(payload)
    assert again.keys == first.keys


def test_candidate_jobs_carry_title_task_role_and_test_paths() -> None:
    candidate = ingest_ci_failures(
        {
            "failures": [
                {
                    "task_id": "t1",
                    "gate": "FAIL",
                    "detail": "boom",
                    "source": "pytest",
                    "test_paths": ["tests/test_a.py"],
                }
            ]
        },
        role="fixer",
    ).candidates[0]
    assert candidate.title
    assert "pytest" in candidate.task
    assert candidate.role == "fixer"
    assert candidate.test_paths == ("tests/test_a.py",)
    assert candidate.key


@pytest.mark.parametrize(
    "approver",
    ["", "   ", "auto", "ci", "agent", "forge", "human", "n/a", "test", "a=b", "1234"],
)
def test_generic_or_blank_approver_is_rejected(approver: str) -> None:
    assert not is_valid_approver(approver)
    with pytest.raises(ApprovalError, match="approver"):
        authorize_candidate(_candidate(), approved_by=approver)


def test_named_approver_produces_capture_only_proposal() -> None:
    proposal = authorize_candidate(_candidate(), approved_by="Alice Smith")
    assert proposal.approved_by == "Alice Smith"
    assert proposal.candidate_key == _candidate().key
    assert "no_auto_dispatch" in proposal.caveats
    assert "no_auto_merge" in proposal.caveats
    assert "no_push" in proposal.caveats


def test_ingestion_and_authorization_never_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agency.forge import orchestration

    def _no_dispatch(*args: object, **kwargs: object) -> object:
        raise AssertionError("a job was dispatched")

    monkeypatch.setattr(orchestration.BlacksmithOps, "submit", _no_dispatch)
    report = ingest_ci_failures([{"gate": "FAIL", "task_id": "t1"}])
    authorize_candidate(report.candidates[0], approved_by="alice")


def test_secrets_are_never_copied_into_output() -> None:
    payload = [
        {
            "gate": "FAIL",
            "task_id": "t1",
            "detail": 'api_key = "supersecretvalue123" and token: abcdefghijklmno',
            "test_paths": ["tests/test_a.py?key=supersecretvalue123"],
        }
    ]
    report = ingest_ci_failures(payload)
    blob = report.model_dump_json()
    assert "supersecretvalue123" not in blob
    assert "abcdefghijklmno" not in blob
    assert "[REDACTED_SECRET]" in blob

    proposal = authorize_candidate(report.candidates[0], approved_by="alice")
    assert "supersecretvalue123" not in proposal.model_dump_json()
    assert "abcdefghijklmno" not in proposal.model_dump_json()
    assert "supersecretvalue123" not in redact_secrets("api_key=supersecretvalue123")


def test_ingestion_is_bounded() -> None:
    payload = [
        {"gate": "FAIL", "task_id": f"t{i}", "detail": "x" * 5000} for i in range(MAX_EVENTS + 50)
    ]
    report = ingest_ci_failures(payload)
    assert len(report.candidates) == MAX_EVENTS
    assert all(len(candidate.task) <= 2000 for candidate in report.candidates)


def test_invalid_payload_type_is_rejected() -> None:
    with pytest.raises(TypeError, match="payload"):
        ingest_ci_failures(123)  # type: ignore[arg-type]
