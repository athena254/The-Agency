"""Contract tests for the forge reviewer LLM prompt (pure string checks)."""

from __future__ import annotations

from agency.forge.reviewer import _review_prompt


def test_review_prompt_carries_rubric_and_evidence_rules() -> None:
    prompt = _review_prompt({"tests": "not_run"})
    for marker in (
        "Severity rubric",
        "low = style or naming",
        "critical = secret leak",
        "cite the exact path and line",
        "lower the score and name what is unverified",
        "Do not invent files, lines, or findings",
    ):
        assert marker in prompt, f"reviewer prompt lacks: {marker}"


def test_review_prompt_marks_truncation_explicitly() -> None:
    big = {"k": "x" * 20000}
    prompt = _review_prompt(big)
    assert "...[change set truncated for review]" in prompt
    small = {"k": "x"}
    assert "change set truncated" not in _review_prompt(small)
