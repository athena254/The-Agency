"""Tests for the Forge ECC critic quorum module."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import ValidationError

from agency.forge.gold_set import (
    GOLD_SET_CAVEATS,
    GoldSet,
    evaluate_gold_set,
    redact_secrets,
)
from agency.forge.quorum import QUORUM_CAVEATS, CriticAdapter, run_quorum


def _critic(critic_id: str, family: str, verdict: str | Callable[[str], str]) -> CriticAdapter:
    def review(diff: str) -> str:
        if callable(verdict):
            return verdict(diff)
        return verdict

    return CriticAdapter(critic_id=critic_id, model_family=family, review=review)


def test_unanimous_approval_from_two_families_approves() -> None:
    report = run_quorum(
        "T-1",
        "diff --git a/x b/x\n+ok",
        [_critic("c1", "anthropic", "APPROVE"), _critic("c2", "openai", "APPROVE")],
        author_id="dev-1",
    )
    assert report.outcome == "APPROVE"
    assert len(report.results) == 2
    assert all(r.attempts == 1 for r in report.results)


def test_disagreement_escalates() -> None:
    report = run_quorum(
        "T-2",
        "diff",
        [_critic("c1", "fam-a", "APPROVE"), _critic("c2", "fam-b", "REJECT")],
    )
    assert report.outcome == "ESCALATE"
    assert "critic_disagreement" in report.reasons


def test_unanimous_rejection_rejects() -> None:
    report = run_quorum(
        "T-3",
        "diff",
        [_critic("c1", "fam-a", "REJECT"), _critic("c2", "fam-b", "REJECT")],
    )
    assert report.outcome == "REJECT"


def test_self_approval_is_invalidated_and_escalates() -> None:
    report = run_quorum(
        "T-4",
        "diff",
        [_critic("c1", "fam-a", "APPROVE"), _critic("c2", "fam-b", "APPROVE")],
        author_id="c1",
    )
    assert report.outcome == "ESCALATE"
    assert any(r.invalid for r in report.results)
    assert "self_approval_rejected" in report.reasons


def test_same_model_family_escalates() -> None:
    report = run_quorum(
        "T-5",
        "diff",
        [_critic("c1", "fam-a", "APPROVE"), _critic("c2", "fam-a", "APPROVE")],
    )
    assert report.outcome == "ESCALATE"
    assert "insufficient_model_family_diversity" in report.reasons


def test_single_critic_escalates() -> None:
    report = run_quorum("T-6", "diff", [_critic("c1", "fam-a", "APPROVE")])
    assert report.outcome == "ESCALATE"
    assert "quorum_requires_two_critics" in report.reasons


def test_bounded_retries_exhausted_yields_uncertain() -> None:
    calls = {"n": 0}

    def boom(_diff: str) -> str:
        calls["n"] += 1
        raise RuntimeError("transient")

    report = run_quorum(
        "T-7",
        "diff",
        [_critic("c1", "fam-a", boom), _critic("c2", "fam-b", boom)],
        max_retries=2,
    )
    assert report.outcome == "UNCERTAIN"
    assert all(r.attempts == 3 for r in report.results)
    assert "critic_unavailable" in report.reasons
    assert calls["n"] == 6


def test_retry_recovers_from_transient_failure() -> None:
    calls = {"n": 0}

    def flaky(_diff: str) -> str:
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("transient")
        return "APPROVE"

    report = run_quorum(
        "T-8",
        "diff",
        [_critic("c1", "fam-a", flaky), _critic("c2", "fam-b", "APPROVE")],
        max_retries=2,
    )
    assert report.outcome == "APPROVE"
    assert report.results[0].attempts == 2


def test_diff_is_bounded_before_critic_sees_it() -> None:
    seen: dict[str, int] = {}

    def review(diff: str) -> str:
        seen["len"] = len(diff)
        return "APPROVE"

    run_quorum(
        "T-9",
        "x" * 500,
        [_critic("c1", "fam-a", review), _critic("c2", "fam-b", "APPROVE")],
        max_diff_chars=10,
    )
    assert seen["len"] == 10


def test_static_critic_mapping_accepted() -> None:
    report = run_quorum(
        "T-10",
        "diff",
        [
            {"critic_id": "c1", "model_family": "fam-a", "verdict": "APPROVE"},
            {"critic_id": "c2", "model_family": "fam-b", "verdict": "APPROVE"},
        ],
    )
    assert report.outcome == "APPROVE"


def test_report_is_frozen_and_carries_caveats() -> None:
    report = run_quorum(
        "T-11",
        "diff",
        [_critic("c1", "fam-a", "APPROVE"), _critic("c2", "fam-b", "APPROVE")],
    )
    assert set(QUORUM_CAVEATS) <= set(report.caveats)
    with pytest.raises(ValidationError):
        report.outcome = "REJECT"


def _gold(**overrides: object) -> GoldSet:
    data: dict[str, object] = {
        "set_id": "GS-1",
        "owner": "alice@example.com",
        "tasks": [
            {"task_id": "a", "prompt": "What is 2+2?", "expected": "4"},
            {"task_id": "b", "prompt": "Capital of France?", "expected": "Paris"},
        ],
    }
    data.update(overrides)
    return GoldSet.model_validate(data)


def test_gold_set_all_tasks_pass() -> None:
    answers = {"What is 2+2?": "4", "Capital of France?": "Paris"}
    report = evaluate_gold_set(_gold(), lambda prompt: answers[prompt])
    assert report.gate == "PASS"
    assert [result.task_id for result in report.results] == ["a", "b"]
    assert all(result.verdict == "PASS" for result in report.results)


def test_wrong_answer_fails_with_deterministic_verdicts() -> None:
    report = evaluate_gold_set(_gold(), lambda prompt: "Paris" if "Capital" in prompt else "5")
    assert report.gate == "FAIL"
    assert report.results[0].verdict == "FAIL"
    assert report.results[1].verdict == "PASS"


def test_run_exception_is_error_not_pass() -> None:
    def run(_prompt: str) -> str:
        raise RuntimeError("boom")

    report = evaluate_gold_set(_gold(), run)
    assert report.gate == "FAIL"
    assert all(result.verdict == "ERROR" for result in report.results)


def test_secrets_redacted_from_set_and_report() -> None:
    key = "sk-abcdefghijklmnopqrstuvwxyz"
    gold = GoldSet.model_validate(
        {
            "set_id": "GS-S",
            "owner": "alice",
            "tasks": [{"task_id": "s", "prompt": f"Use {key} as key", "expected": "ok"}],
        }
    )
    assert key not in gold.tasks[0].prompt
    report = evaluate_gold_set(gold, lambda _prompt: key)
    assert key not in report.results[0].observed
    assert report.gate == "FAIL"


def test_redact_secrets_handles_assignments_and_bearer_tokens() -> None:
    redacted = redact_secrets("token=abcdef123456 bearer ghp_abcdefghijklmnopqrstuvwxyz")
    assert "abcdef123456" not in redacted
    assert "ghp_" not in redacted


def test_tasks_sorted_by_id_for_deterministic_order() -> None:
    gold = _gold(
        tasks=[
            {"task_id": "z", "prompt": "p", "expected": "x"},
            {"task_id": "a", "prompt": "p", "expected": "x"},
        ]
    )
    assert [task.task_id for task in gold.tasks] == ["a", "z"]


def test_duplicate_task_ids_rejected() -> None:
    with pytest.raises(ValidationError):
        _gold(
            tasks=[
                {"task_id": "a", "prompt": "p", "expected": "x"},
                {"task_id": "a", "prompt": "q", "expected": "y"},
            ]
        )


def test_empty_set_rejected() -> None:
    with pytest.raises(ValidationError):
        _gold(tasks=[])


def test_automated_owner_rejected() -> None:
    with pytest.raises(ValidationError):
        _gold(owner="agent:forge")


def test_observed_output_is_bounded() -> None:
    report = evaluate_gold_set(_gold(), lambda _prompt: "x" * 1000, max_output_chars=8)
    assert all(len(result.observed) == 8 for result in report.results)


def test_report_is_repeatable() -> None:
    gold = _gold()
    assert evaluate_gold_set(gold, lambda prompt: prompt) == evaluate_gold_set(
        gold, lambda prompt: prompt
    )


def test_mapping_run_accepted() -> None:
    report = evaluate_gold_set(_gold(), {"What is 2+2?": "4", "Capital of France?": "Paris"})
    assert report.gate == "PASS"


def test_gold_set_is_frozen_and_carries_caveats() -> None:
    gold = _gold()
    report = evaluate_gold_set(gold, lambda _prompt: "anything")
    assert set(GOLD_SET_CAVEATS) <= set(report.caveats)
    with pytest.raises(ValidationError):
        gold.owner = "bob"
