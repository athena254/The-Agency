"""Tests for the ECC provenance + regression-first slice (A07/B02).

Covers ``handoff`` (fail-closed provenance envelope) and ``regression_first``
(captured failing-test evidence before a fix is trusted). No I/O, no network.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from agency.forge.handoff import (
    MAX_BUDGET_KEYS,
    HandoffEnvelope,
    make_handoff,
    validate_handoff,
)
from agency.forge.regression_first import (
    MAX_PROOF_OUTPUT_CHARS,
    RegressionProof,
    check_regression_first,
)


def _handoff(**overrides: Any) -> HandoffEnvelope:
    base: dict[str, Any] = {
        "task_id": "task-1",
        "from_": "planner",
        "to": "coder",
        "spec_ref": "docs/plan.md",
        "risk_class": "high",
        "artifacts": ("src/a.py",),
        "acceptance": ("tests pass",),
        "budget": {"max_tokens": 100, "max_seconds": 60},
        "producer": "orchestrator",
        "evidence": ("pytest PASS output",),
        "status": "ready",
    }
    if "parent_id" in overrides:
        base["parent_id"] = overrides.pop("parent_id")
    base.update(overrides)
    return make_handoff(**base)


def _proof(**overrides: Any) -> RegressionProof:
    base: dict[str, Any] = {
        "bug_ref": "bug-42",
        "test_command": "pytest -q tests/test_bug.py",
        "failing_test": "tests/test_bug.py::test_regression",
        "failure_output": "FAILED tests/test_bug.py::test_regression - AssertionError",
        "returncode": 1,
    }
    base.update(overrides)
    return RegressionProof(**base)


# --------------------------------------------------------------------------- #
# handoff: envelope shape
# --------------------------------------------------------------------------- #


def test_make_handoff_carries_every_provenance_field() -> None:
    env = make_handoff(
        task_id="task-1",
        parent_id="task-0",
        from_="planner",
        to="coder",
        spec_ref="docs/plan.md",
        risk_class="critical",
        artifacts=["src/a.py", "src/b.py"],
        acceptance=["tests green"],
        budget={"max_tokens": 100},
        producer="orchestrator",
        evidence=["hash:abc"],
        status="accepted",
    )
    assert env.task_id == "task-1"
    assert env.parent_id == "task-0"
    assert env.from_ == "planner"
    assert env.to == "coder"
    assert env.spec_ref == "docs/plan.md"
    assert env.risk_class == "critical"
    assert env.artifacts == ("src/a.py", "src/b.py")
    assert env.acceptance == ("tests green",)
    assert env.budget == {"max_tokens": 100}
    assert env.producer == "orchestrator"
    assert env.evidence == ("hash:abc",)
    assert env.status == "accepted"


def test_handoff_from_alias_accepts_both_forms() -> None:
    by_name = make_handoff(task_id="t", from_="a", to="b", spec_ref="s", producer="p")
    by_alias = HandoffEnvelope.model_validate(
        {
            "task_id": "t",
            "from": "a",
            "to": "b",
            "spec_ref": "s",
            "producer": "p",
        }
    )
    assert by_name.from_ == by_alias.from_ == "a"
    assert by_alias.model_dump(by_alias=True)["from"] == "a"


def test_handoff_is_frozen_and_forbids_extra_fields() -> None:
    env = _handoff()
    with pytest.raises(ValidationError):
        env.task_id = "mutated"
    with pytest.raises(ValidationError):
        HandoffEnvelope(  # type: ignore[call-arg]
            task_id="t", from_="a", to="b", spec_ref="s", producer="p", surprise="x"
        )


def test_handoff_defaults_for_optional_fields() -> None:
    env = make_handoff(task_id="t", from_="a", to="b", spec_ref="s", producer="p")
    assert env.parent_id is None
    assert env.risk_class == "medium"
    assert env.artifacts == ()
    assert env.acceptance == ()
    assert env.budget == {}
    assert env.evidence == ()
    assert env.status == "draft"


def test_handoff_json_round_trip() -> None:
    env = _handoff()
    restored = HandoffEnvelope.model_validate_json(env.model_dump_json())
    assert restored == env


# --------------------------------------------------------------------------- #
# handoff: fail-closed validation
# --------------------------------------------------------------------------- #


def test_validate_handoff_passes_fully_specified_envelope() -> None:
    result = validate_handoff(_handoff())
    assert result.ok is True
    assert bool(result) is True
    assert result.gate == "PASS"
    assert result.findings == ()
    assert result.envelope is not None


def test_validate_handoff_low_risk_may_omit_evidence() -> None:
    result = validate_handoff(_handoff(risk_class="low", evidence=()))
    assert result.ok is True


def test_validate_handoff_accepted_always_requires_evidence() -> None:
    result = validate_handoff(_handoff(risk_class="low", status="accepted", evidence=()))
    assert result.ok is False
    assert "no_evidence" in result.findings


def test_validate_handoff_rejects_blank_required_text() -> None:
    result = validate_handoff(_handoff(spec_ref="   "))
    assert result.ok is False
    assert "blank_field" in result.findings


def test_validate_handoff_rejects_missing_acceptance() -> None:
    result = validate_handoff(_handoff(acceptance=()))
    assert result.ok is False
    assert "no_acceptance" in result.findings


def test_validate_handoff_rejects_missing_artifacts() -> None:
    result = validate_handoff(_handoff(artifacts=()))
    assert result.ok is False
    assert "no_artifacts" in result.findings


@pytest.mark.parametrize("risk", ["medium", "high", "critical"])
def test_validate_handoff_requires_evidence_above_low_risk(risk: str) -> None:
    result = validate_handoff(_handoff(risk_class=risk, evidence=()))
    assert result.ok is False
    assert "no_evidence" in result.findings


def test_validate_handoff_rejects_self_handoff() -> None:
    result = validate_handoff(_handoff(to="planner"))
    assert result.ok is False
    assert "self_handoff" in result.findings


def test_validate_handoff_rejects_self_parent() -> None:
    result = validate_handoff(_handoff(parent_id="task-1"))
    assert result.ok is False
    assert "self_parent" in result.findings


def test_validate_handoff_rejects_negative_budget() -> None:
    result = validate_handoff(_handoff(budget={"max_tokens": -1}))
    assert result.ok is False
    assert "invalid_budget" in result.findings


def test_validate_handoff_rejects_blank_budget_key() -> None:
    result = validate_handoff(_handoff(budget={"  ": 5}))
    assert result.ok is False
    assert "invalid_budget" in result.findings


def test_validate_handoff_bounds_budget_size() -> None:
    budget = {f"k{i}": 1 for i in range(MAX_BUDGET_KEYS + 1)}
    result = validate_handoff(_handoff(budget=budget))
    assert result.ok is False
    assert "invalid_budget" in result.findings


def test_validate_handoff_rejects_blank_collection_entries() -> None:
    result = validate_handoff(_handoff(acceptance=["  "], evidence=["  "]))
    assert result.ok is False
    assert "blank_entry" in result.findings


def test_validate_handoff_rejects_invalid_raw_mapping() -> None:
    result = validate_handoff({"task_id": "t"})
    assert result.ok is False
    assert result.envelope is None
    assert "invalid_envelope" in result.findings


def test_validate_handoff_is_fail_closed_on_ambiguous_input() -> None:
    assert validate_handoff(None).ok is False  # type: ignore[arg-type]
    assert validate_handoff("not-an-envelope").ok is False  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# regression_first: proof shape
# --------------------------------------------------------------------------- #


def test_regression_proof_is_frozen_and_forbids_extra_fields() -> None:
    proof = _proof()
    with pytest.raises(ValidationError):
        proof.returncode = 0
    with pytest.raises(ValidationError):
        RegressionProof(  # type: ignore[call-arg]
            bug_ref="b", test_command="t", failing_test="f", extra="x"
        )


def test_regression_proof_json_round_trip() -> None:
    proof = _proof()
    restored = RegressionProof.model_validate_json(proof.model_dump_json())
    assert restored == proof


# --------------------------------------------------------------------------- #
# regression_first: fail-closed gate
# --------------------------------------------------------------------------- #


def test_check_regression_first_passes_captured_failure() -> None:
    verdict = check_regression_first(_proof())
    assert verdict.ok is True
    assert bool(verdict) is True
    assert verdict.gate == "PASS"
    assert verdict.findings == ()
    assert verdict.proof is not None


def test_check_regression_first_rejects_passing_returncode() -> None:
    verdict = check_regression_first(_proof(returncode=0))
    assert verdict.ok is False
    assert "no_failure_returncode" in verdict.findings


def test_check_regression_first_rejects_missing_returncode() -> None:
    verdict = check_regression_first(_proof(returncode=None))
    assert verdict.ok is False
    assert "no_failure_returncode" in verdict.findings


@pytest.mark.parametrize("output", ["", "   ", "\n\t"])
def test_check_regression_first_rejects_blank_failure_output(output: str) -> None:
    verdict = check_regression_first(_proof(failure_output=output))
    assert verdict.ok is False
    assert "no_failure_output" in verdict.findings


def test_check_regression_first_rejects_oversized_output() -> None:
    verdict = check_regression_first(_proof(failure_output="x" * (MAX_PROOF_OUTPUT_CHARS + 1)))
    assert verdict.ok is False
    assert "output_too_large" in verdict.findings


def test_check_regression_first_rejects_blank_identity_fields() -> None:
    proof = _proof(bug_ref="  ", test_command="  ", failing_test="  ")
    verdict = check_regression_first(proof)
    assert verdict.ok is False
    assert "blank_field" in verdict.findings


def test_check_regression_first_enforces_before_fix_ordering() -> None:
    now = datetime.now(UTC)
    before = check_regression_first(_proof(), fix_time=now + timedelta(minutes=1))
    after = check_regression_first(_proof(), fix_time=now - timedelta(minutes=1))
    assert before.ok is True
    assert after.ok is False
    assert "proof_after_fix" in after.findings


def test_check_regression_first_accepts_raw_mapping() -> None:
    verdict = check_regression_first(
        {
            "bug_ref": "b",
            "test_command": "pytest -q",
            "failing_test": "test_x",
            "failure_output": "FAILED",
            "returncode": 2,
        }
    )
    assert verdict.ok is True


def test_check_regression_first_rejects_invalid_raw_mapping() -> None:
    verdict = check_regression_first({"bug_ref": "b"})
    assert verdict.ok is False
    assert verdict.proof is None
    assert "invalid_proof" in verdict.findings


def test_check_regression_first_is_fail_closed_on_ambiguous_input() -> None:
    assert check_regression_first(None).ok is False  # type: ignore[arg-type]
    assert check_regression_first(42).ok is False  # type: ignore[arg-type]
