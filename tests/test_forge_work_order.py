"""Tests for the striker work-order template (pure rendering, no LLM, no CLI)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agency.forge.protected_paths import protected_paths
from agency.forge.work_order import MAX_TASK_CHARS, WorkOrder, render_work_order


def _order(**over):
    base = {
        "title": "Add weather lookup tool",
        "task": "Implement a weather_lookup tool with tests.",
        "repo": "D:/scratch/repo",
    }
    base.update(over)
    return WorkOrder(**base)


def test_render_contains_all_sections_and_acceptance_verbatim():

    text = render_work_order(
        _order(
            acceptance=["Tool registered in catalog", "2/2 tests pass"],
            test_paths=["tests/test_weather.py"],
            test_command="pytest tests/test_weather.py",
        )
    )
    for section in (
        "OBJECTIVE",
        "CONTEXT",
        "BOUNDARIES",
        "ACCEPTANCE",
        "TESTS",
        "DELIVERABLE",
        "ON_FAILURE",
        "WORK RULES",
    ):
        assert section in text, f"missing section {section}"
    assert "Tool registered in catalog" in text
    assert "2/2 tests pass" in text
    for path in protected_paths():
        assert path in text


def test_render_includes_global_work_rules():

    text = render_work_order(_order())
    assert "KISS" in text
    assert "YAGNI" in text
    assert "reuse" in text.lower()
    assert "plan" in text.lower()


def test_retry_note_only_when_failure_tail_present():

    plain = render_work_order(_order())
    assert "RETRY NOTE" not in plain
    retried = render_work_order(
        _order(attempt=2, max_attempts=3, failure_tail="AssertionError: expected 200")
    )
    assert "RETRY NOTE" in retried
    assert "attempt 2 of 3" in retried
    assert "AssertionError: expected 200" in retried


def test_task_bounded_and_required():

    with pytest.raises(ValidationError):
        WorkOrder(**_order(task=""))
    with pytest.raises(ValidationError):
        WorkOrder(**_order(task="x" * (MAX_TASK_CHARS + 1)))
    with pytest.raises(ValidationError):
        WorkOrder(**_order(repo="relative/path"))
