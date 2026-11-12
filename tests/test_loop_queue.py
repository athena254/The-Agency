"""Tests for loop queue normalization (Phase 2)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agency.autonomy.loop_queue import (
    from_backlog,
    load_queue,
    parse_github_issues,
    parse_goal_plan,
)

GOAL = "Ship the autonomous loop end to end.\n"

PLAN = """# Fix plan

- [x] Already shipped
- [ ] Wire the driver
  - test: tests/test_driver.py
  - acceptance: pytest tests/test_driver.py
  - deps: expose-queue
- [ ] Expose queue
  - test: tests/test_queue.py
- [ ] Wire the driver
"""


def _write_pair(tmp_path: Path) -> tuple[Path, Path]:
    goal = tmp_path / "GOAL.md"
    plan = tmp_path / "fix_plan.md"
    goal.write_text(GOAL, encoding="utf-8")
    plan.write_text(PLAN, encoding="utf-8")
    return goal, plan


def test_parse_goal_plan_parses_items_acceptance_and_deps(tmp_path: Path) -> None:
    goal, plan = _write_pair(tmp_path)

    items = parse_goal_plan(goal, plan)

    assert [item.id for item in items] == [
        "wire-the-driver",
        "expose-queue",
        "wire-the-driver-2",
    ]
    assert [item.priority for item in items] == [3, 2, 1]
    assert all(item.description == GOAL.strip() for item in items)
    assert items[0].acceptance == ("tests/test_driver.py", "pytest tests/test_driver.py")
    assert items[0].deps == ("expose-queue",)
    assert items[1].acceptance == ("tests/test_queue.py",)
    assert items[2].title == "Wire the driver"


def test_parse_goal_plan_missing_file_raises(tmp_path: Path) -> None:
    goal, plan = _write_pair(tmp_path)

    with pytest.raises(FileNotFoundError):
        parse_goal_plan(tmp_path / "missing.md", plan)
    with pytest.raises(FileNotFoundError):
        parse_goal_plan(goal, tmp_path / "missing.md")


def test_from_backlog_maps_prd_headings() -> None:
    prd = (
        "# First feature\n\n"
        "Do the first thing.\n"
        "- test: tests/test_first.py\n\n"
        "# Second feature\n\n"
        "Do the second thing.\n"
    )

    items = from_backlog(prd)

    assert [item.title for item in items] == ["First feature", "Second feature"]
    assert [item.id for item in items] == ["first-feature", "second-feature"]
    assert [item.priority for item in items] == [2, 1]
    assert items[0].acceptance == ("tests/test_first.py",)
    assert items[0].description.startswith("Do the first thing.")


def test_load_queue_dispatches_goal_plan_directory(tmp_path: Path) -> None:
    _write_pair(tmp_path)

    items = load_queue(tmp_path)

    assert [item.id for item in items] == [
        "wire-the-driver",
        "expose-queue",
        "wire-the-driver-2",
    ]


def test_load_queue_local_directory_without_repo_kwarg(tmp_path: Path) -> None:
    _write_pair(tmp_path)

    items = load_queue(str(tmp_path))

    assert [item.id for item in items] == [
        "wire-the-driver",
        "expose-queue",
        "wire-the-driver-2",
    ]
    assert all(item.description == GOAL.strip() for item in items)


def test_load_queue_dispatches_gh_source(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str | None]] = []

    def fake_parse(repo: str, *, label: str | None = None, limit: int = 64) -> list:
        calls.append((repo, label))
        return []

    monkeypatch.setattr("agency.autonomy.loop_queue.parse_github_issues", fake_parse)

    assert load_queue("gh:owner/repo", label="ready") == []
    assert calls == [("owner/repo", "ready")]


def test_parse_github_issues_returns_empty_when_gh_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError("gh not found")

    monkeypatch.setattr("agency.autonomy.loop_queue.subprocess.run", fake_run)

    assert parse_github_issues("owner/repo") == []


def test_parse_github_issues_returns_empty_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="boom")

    monkeypatch.setattr("agency.autonomy.loop_queue.subprocess.run", fake_run)

    assert parse_github_issues("owner/repo") == []


def test_parse_github_issues_maps_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = [
        {
            "number": 7,
            "title": "Fix the thing",
            "body": "Details here.\nacceptance: pytest tests/test_thing.py\n",
            "labels": [{"name": "ready"}],
        },
        {"number": 8, "title": "Bad body", "body": None, "labels": []},
    ]
    seen: dict[str, list[str]] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen["command"] = command
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr("agency.autonomy.loop_queue.subprocess.run", fake_run)

    items = parse_github_issues("owner/repo", label="ready")

    assert seen["command"][:4] == ["gh", "issue", "list", "--repo"]
    assert "owner/repo" in seen["command"]
    assert "--label" in seen["command"]
    assert "ready" in seen["command"]
    assert [item.id for item in items] == ["issue-7", "issue-8"]
    assert [item.priority for item in items] == [2, 1]
    assert items[0].title == "Fix the thing"
    assert items[0].description == "Details here.\nacceptance: pytest tests/test_thing.py"
    assert items[0].acceptance == ("pytest tests/test_thing.py",)
    assert items[1].description == ""
