"""Automatic risky review and human-gate attestation (ECC wave 2, slice 1).

Both modules are deep seams: they accept injected adapters/records, return one
immutable result, and never authorize a merge on their own.
"""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from agency.forge.approvals import ApprovalRecord
from agency.forge.gate_attestation import (
    GATE_ATTESTATION_CAVEATS,
    GateAttestation,
    attest_human_gates,
)
from agency.forge.gold_set import GoldSet, GoldTask
from agency.forge.quorum import CriticAdapter
from agency.forge.risky_review import (
    RISKY_REVIEW_CAVEATS,
    RiskyReviewReport,
    apply_risky_review,
)

# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _critic(critic_id: str, family: str, verdict: str | Callable[[str], str]) -> CriticAdapter:
    def review(diff: str) -> str:
        return verdict(diff) if callable(verdict) else verdict

    return CriticAdapter(critic_id=critic_id, model_family=family, review=review)


def _approve_pair() -> list[CriticAdapter]:
    return [_critic("c1", "fam-a", "APPROVE"), _critic("c2", "fam-b", "APPROVE")]


def _gold() -> GoldSet:
    return GoldSet(
        set_id="gold-1",
        owner="human-owner",
        tasks=(GoldTask(task_id="t1", prompt="2+2?", expected="4"),),
    )


def _record(
    *,
    approver: str = "alice",
    decision: str = "approve",
    caveats: tuple[str, ...] = (),
    note: str = "",
) -> dict[str, object]:
    return {
        "approver": approver,
        "decision": decision,
        "caveats": caveats,
        "note": note,
    }


# --------------------------------------------------------------------------- #
# apply_risky_review
# --------------------------------------------------------------------------- #


def test_pass_when_quorum_approves_and_gold_passes() -> None:
    report = apply_risky_review(
        "job-1",
        "diff --git a/x b/x\n+ok",
        critics=_approve_pair(),
        gold_set=_gold(),
        run=lambda _prompt: "4",
    )
    assert isinstance(report, RiskyReviewReport)
    assert report.decision == "PASS"
    assert report.allowed is True
    assert report.quorum is not None and report.quorum.outcome == "APPROVE"
    assert report.gold is not None and report.gold.gate == "PASS"
    assert report.reasons == ()


def test_block_when_quorum_rejects() -> None:
    report = apply_risky_review(
        "job-1",
        "diff",
        critics=[_critic("c1", "fam-a", "REJECT"), _critic("c2", "fam-b", "REJECT")],
        gold_set=_gold(),
        run=lambda _prompt: "4",
    )
    assert report.decision == "BLOCK"
    assert report.allowed is False


def test_block_when_gold_fails() -> None:
    report = apply_risky_review(
        "job-1",
        "diff",
        critics=_approve_pair(),
        gold_set=_gold(),
        run=lambda _prompt: "wrong",
    )
    assert report.decision == "BLOCK"
    assert report.allowed is False
    assert report.gold is not None and report.gold.gate == "FAIL"


def test_uncertain_when_quorum_escalates() -> None:
    report = apply_risky_review(
        "job-1",
        "diff",
        critics=[_critic("c1", "fam-a", "APPROVE"), _critic("c2", "fam-b", "REJECT")],
        gold_set=_gold(),
        run=lambda _prompt: "4",
    )
    assert report.decision == "UNCERTAIN"
    assert report.allowed is False
    assert "critic_disagreement" in report.reasons


def test_uncertain_when_gold_set_missing_is_fail_closed() -> None:
    report = apply_risky_review(
        "job-1",
        "diff",
        critics=_approve_pair(),
        gold_set=None,
        run=lambda _prompt: "4",
    )
    assert report.decision == "UNCERTAIN"
    assert report.allowed is False
    assert report.gold is None
    assert "gold_set_missing" in report.reasons


def test_uncertain_when_run_adapter_missing_is_fail_closed() -> None:
    report = apply_risky_review(
        "job-1",
        "diff",
        critics=_approve_pair(),
        gold_set=_gold(),
        run=None,
    )
    assert report.decision == "UNCERTAIN"
    assert report.allowed is False
    assert report.gold is None
    assert "run_adapter_missing" in report.reasons


def test_self_approval_is_rejected_and_fail_closed() -> None:
    report = apply_risky_review(
        "job-1",
        "diff",
        critics=_approve_pair(),
        gold_set=_gold(),
        run=lambda _prompt: "4",
        author_id="c1",
    )
    assert report.decision == "UNCERTAIN"
    assert report.allowed is False


def test_injected_adapters_are_actually_invoked() -> None:
    critic_calls = 0
    run_calls = 0

    def review(_diff: str) -> str:
        nonlocal critic_calls
        critic_calls += 1
        return "APPROVE"

    def run(_prompt: str) -> str:
        nonlocal run_calls
        run_calls += 1
        return "4"

    apply_risky_review(
        "job-1",
        "diff",
        critics=[
            CriticAdapter(critic_id="c1", model_family="fam-a", review=review),
            CriticAdapter(critic_id="c2", model_family="fam-b", review=review),
        ],
        gold_set=_gold(),
        run=run,
    )
    assert critic_calls == 2
    assert run_calls == 1


def test_caveats_are_honest() -> None:
    report = apply_risky_review(
        "job-1",
        "diff",
        critics=_approve_pair(),
        gold_set=_gold(),
        run=lambda _prompt: "4",
    )
    assert set(RISKY_REVIEW_CAVEATS).issubset(set(report.caveats))
    assert "does_not_authorize_merge" in report.caveats
    assert "not_a_human_gate" in report.caveats
    assert "no_network_calls" in report.caveats


def test_empty_job_id_rejected() -> None:
    with pytest.raises(ValueError):
        apply_risky_review(
            "",
            "diff",
            critics=_approve_pair(),
            gold_set=_gold(),
            run=lambda _prompt: "4",
        )


def test_report_is_immutable() -> None:
    report = apply_risky_review(
        "job-1",
        "diff",
        critics=_approve_pair(),
        gold_set=_gold(),
        run=lambda _prompt: "4",
    )
    with pytest.raises(ValidationError):
        report.decision = "BLOCK"  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# attest_human_gates
# --------------------------------------------------------------------------- #


def test_all_required_gates_named_in_caveats_allowed() -> None:
    att = attest_human_gates(
        ["src/auth/login.py", "payments/checkout.py"],
        _record(caveats=("no_auto_merge", "gate_reviewed:auth", "gate_reviewed:payments")),
    )
    assert isinstance(att, GateAttestation)
    assert att.required_gates == ("auth", "payments")
    assert att.attested_gates == ("auth", "payments")
    assert att.missing_gates == ()
    assert att.allowed is True


def test_gates_named_in_note_allowed() -> None:
    att = attest_human_gates(
        ["src/auth/login.py"],
        _record(note="reviewed auth; human_gates=auth,payments"),
    )
    assert att.required_gates == ("auth",)
    assert att.allowed is True


def test_missing_gate_fails_closed() -> None:
    att = attest_human_gates(
        ["src/auth/login.py", "payments/checkout.py"],
        _record(caveats=("gate_reviewed:auth",)),
    )
    assert att.allowed is False
    assert att.missing_gates == ("payments",)
    assert "missing_gate_attestation" in att.reasons


def test_no_approval_record_fails_closed() -> None:
    att = attest_human_gates(["src/auth/login.py"], [])
    assert att.allowed is False
    assert "no_approval_record" in att.reasons


def test_bot_approver_does_not_count() -> None:
    att = attest_human_gates(
        ["src/auth/login.py"],
        _record(approver="bot", caveats=("gate_reviewed:auth",)),
    )
    assert att.allowed is False
    assert "invalid_approver_ignored" in att.reasons


def test_non_approve_decision_does_not_count() -> None:
    att = attest_human_gates(
        ["src/auth/login.py"],
        _record(decision="reject", caveats=("gate_reviewed:auth",)),
    )
    assert att.allowed is False
    assert "decision_not_approve_ignored" in att.reasons


def test_gates_may_be_spread_across_multiple_records() -> None:
    att = attest_human_gates(
        ["src/auth/login.py", "payments/checkout.py"],
        [
            _record(approver="alice", caveats=("gate_reviewed:auth",)),
            _record(approver="bob", caveats=("gate_reviewed:payments",)),
        ],
    )
    assert att.allowed is True
    assert att.attested_gates == ("auth", "payments")


def test_no_required_gates_is_allowed_with_reason() -> None:
    att = attest_human_gates(["src/landing.ts"], _record())
    assert att.required_gates == ()
    assert att.allowed is True
    assert "no_required_gates" in att.reasons


def test_real_approval_record_is_accepted() -> None:
    record = ApprovalRecord(
        job_id="job-1",
        approver="alice",
        decision="approve",
        at="2026-01-01T00:00:00Z",
        state_after="AWAITING_HUMAN",
        caveats=("no_auto_merge", "gate_reviewed:auth"),
    )
    att = attest_human_gates(["src/auth/login.py"], record)
    assert att.allowed is True


def test_simple_namespace_record_is_accepted() -> None:
    record = SimpleNamespace(
        approver="alice",
        decision="approve",
        caveats=("gate_reviewed:permissions",),
        note="",
    )
    att = attest_human_gates(["src/acl/roles.py"], record)
    assert att.allowed is True


def test_attestation_caveats_are_honest() -> None:
    att = attest_human_gates(["src/auth/login.py"], _record(caveats=("gate_reviewed:auth",)))
    assert set(GATE_ATTESTATION_CAVEATS).issubset(set(att.caveats))
    assert "does_not_authorize_merge" in att.caveats
    assert "no_network_calls" in att.caveats


def test_attestation_is_immutable() -> None:
    att = attest_human_gates(["src/landing.ts"], _record())
    with pytest.raises(ValidationError):
        att.allowed = False  # type: ignore[misc]
