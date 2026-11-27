"""Tests for role skill bundles and the enforced plan gate."""

from __future__ import annotations

from pathlib import Path

from agency.forge.agent_skills import MAX_SKILL_CHARS, skill_for
from agency.forge.planning import check_plan
from agency.forge.work_order import WorkOrder, render_work_order


def test_skill_bundles_are_bounded_and_role_specific() -> None:
    striker = skill_for("striker")
    apprentice = skill_for("apprentice")
    blacksmith = skill_for("blacksmith")
    assert "SKILL: striker" in striker
    assert "promotion" in apprentice
    assert "AWAITING_HUMAN" in blacksmith
    assert len(striker) <= MAX_SKILL_CHARS
    assert skill_for("unknown") == ""


def test_plan_gate_requires_sections(tmp_path: Path) -> None:
    missing = check_plan(tmp_path)
    assert missing.gate == "FAIL"
    assert "plan_file_missing" in missing.findings

    (tmp_path / "PLAN.md").write_text("# Plan\nGoal: x\nApproach: y\nFiles: z\n", encoding="utf-8")
    partial = check_plan(tmp_path)
    assert partial.gate == "FAIL"
    assert "Tests" in partial.missing_sections

    (tmp_path / "PLAN.md").write_text(
        "# Plan\nGoal: x\nApproach: y\nFiles: z\nTests: pytest\nRisks: none\n", encoding="utf-8"
    )
    assert check_plan(tmp_path).gate == "PASS"


def test_work_order_carries_role_skill_and_plan_gate() -> None:
    order = WorkOrder(
        title="Build page",
        task="Build the page",
        repo="C:/repo",
        role="apprentice",
        plan_required=True,
    )
    text = render_work_order(order)
    assert "SKILL: apprentice" in text
    assert "PLAN GATE (hard)" in text
    assert "PLAN.md" in text
