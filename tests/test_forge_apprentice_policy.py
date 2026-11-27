"""Gap 5: the apprentice is a bounded worker path, not just a label.

Risky classes are refused, tool budgets have a hard cap, promotion needs
recorded evidence, and the policy is rendered into the work order. The
dispatcher-facing helper exposes reasons and caveats for the parent to wire.
"""

from __future__ import annotations

from pathlib import Path

from agency.forge.apprentice_policy import (
    DEFAULT_TOOL_BUDGET_CAP,
    PROMOTION_TASK_THRESHOLD,
    ApprenticePolicy,
    PromotionEvidence,
    can_take,
    enforce_apprentice_policy,
    refusal_reasons,
    render_apprentice_policy,
    tool_budget,
)
from agency.forge.work_order import WorkOrder, render_work_order

_PROMOTED = PromotionEvidence(
    passed_tasks=PROMOTION_TASK_THRESHOLD, isolation_violations=0, approver="human-lead"
)


def test_apprentice_refuses_risky_and_unknown_classes() -> None:
    assert can_take("apprentice", "trivial") is True
    assert can_take("apprentice", "risky") is False
    # Promotion never unlocks risky work for an apprentice.
    assert can_take("apprentice", "risky", promotion_evidence=_PROMOTED) is False
    assert can_take("apprentice", "unknown") is False
    assert "apprentice_refuses_risky_class" in refusal_reasons("apprentice", "risky")


def test_standard_class_requires_promotion_evidence() -> None:
    assert can_take("apprentice", "standard") is False
    assert can_take("apprentice", "standard", promotion_evidence=PromotionEvidence()) is False
    assert can_take("apprentice", "standard", promotion_evidence=_PROMOTED) is True
    assert "apprentice_requires_promotion_evidence" in refusal_reasons("apprentice", "standard")


def test_non_apprentice_roles_are_not_constrained() -> None:
    assert can_take("striker", "risky") is True
    assert can_take("blacksmith", "standard") is True


def test_promotion_evidence_is_recorded_and_checked() -> None:
    assert _PROMOTED.is_sufficient() is True
    assert PromotionEvidence(passed_tasks=9, approver="lead").is_sufficient() is False
    assert PromotionEvidence(passed_tasks=10, approver="").is_sufficient() is False
    assert (
        PromotionEvidence(passed_tasks=10, approver="lead", isolation_violations=1).is_sufficient()
        is False
    )
    assert str(PROMOTION_TASK_THRESHOLD) in _PROMOTED.render()
    assert "status=sufficient" in _PROMOTED.render()


def test_tool_budget_is_capped_for_apprentices() -> None:
    assert tool_budget("apprentice", 999) == DEFAULT_TOOL_BUDGET_CAP
    assert tool_budget("apprentice", 3) == 3
    assert tool_budget("striker", 40) == 40
    custom = ApprenticePolicy(tool_budget_cap=5)
    assert tool_budget("apprentice", 999, policy=custom) == 5


def test_enforcement_result_exposes_reasons_budget_and_caveats() -> None:
    refused = enforce_apprentice_policy("apprentice", "risky", 99)
    assert refused.allowed is False
    assert "apprentice_refuses_risky_class" in refused.reasons
    assert refused.effective_tool_budget == DEFAULT_TOOL_BUDGET_CAP
    assert "apprentice_policy_evaluated" in refused.caveats
    assert "tool_budget_capped" in refused.caveats

    promoted = enforce_apprentice_policy("apprentice", "standard", 4, promotion_evidence=_PROMOTED)
    assert promoted.allowed is True
    assert promoted.effective_tool_budget == 4
    assert "tool_budget_capped" not in promoted.caveats
    assert "promotion_evidence_recorded" in promoted.caveats
    assert "apprentice_promoted" in promoted.caveats


def test_policy_rendered_into_apprentice_work_order(tmp_path: Path) -> None:
    order = WorkOrder(
        title="Tidy docs",
        task="Fix a typo in the README.",
        repo=str(tmp_path),
        role="apprentice",
        job_class="trivial",
        tool_budget=50,
        promotion_evidence=_PROMOTED,
    )
    text = render_work_order(order)
    assert "APPRENTICE POLICY (enforced)" in text
    assert "ALLOWED" in text
    assert "at most 12 tool calls" in text
    assert "risky classes are always refused" in text
    assert "recorded promotion evidence" in text
    assert "status=sufficient" in text


def test_render_excludes_policy_for_strikers(tmp_path: Path) -> None:
    text = render_work_order(
        WorkOrder(title="Ship", task="Build a thing", repo=str(tmp_path), role="striker")
    )
    assert "APPRENTICE POLICY" not in text
    assert render_apprentice_policy("striker", "risky") == ""


def test_render_marks_refused_class(tmp_path: Path) -> None:
    order = WorkOrder(
        title="Harden auth",
        task="Refuse risky work.",
        repo=str(tmp_path),
        role="apprentice",
        job_class="risky",
    )
    text = render_work_order(order)
    assert "risky -> REFUSED" in text
