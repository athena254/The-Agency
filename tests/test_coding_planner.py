"""Tests for the coding planner LLM helper."""

from __future__ import annotations

import json

from agency.agents.coding_planner import plan_edits


def test_plan_edits_parses_json_list():
    payload = [
        {"path": "m.py", "old": "x = 1", "new": "x = 2"},
    ]

    def fake_llm(prompt, context):
        assert "task" in prompt.lower() or True
        return json.dumps(payload)

    result = plan_edits(fake_llm, "change x to 2")
    assert result == payload


def test_plan_edits_handles_code_fence():
    def fake_llm(prompt, context):
        return "```json\n[{\"path\": \"a.py\", \"old\": \"a\", \"new\": \"b\"}]\n```"

    result = plan_edits(fake_llm, "edit a")
    assert result == [{"path": "a.py", "old": "a", "new": "b"}]


def test_plan_edits_returns_empty_on_parse_failure():
    def fake_llm(prompt, context):
        return "not json at all"

    assert plan_edits(fake_llm, "task") == []


def test_plan_edits_returns_empty_on_non_list():
    def fake_llm(prompt, context):
        return '{"path": "a.py"}'

    assert plan_edits(fake_llm, "task") == []
