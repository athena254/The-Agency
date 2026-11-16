"""TUI tests: both apps render, spec rules gate, yolo bypasses."""

from __future__ import annotations

import pytest

from agency.tui.agency_app import AgencyApp
from agency.tui.forge_app import ForgeApp
from agency.tui.permission import Mode, PermissionPolicy
from agency.tui.spec_rules import (
    SpecDraft,
    SystemType,
    check_spec,
    classify_system,
    draft_from_text,
)


def test_permission_default_needs_approval():
    policy = PermissionPolicy()
    assert policy.needs_approval("merge", "main")
    assert policy.audit[-1]["decision"] == "approval_required"


def test_permission_yolo_bypasses_and_audits():
    policy = PermissionPolicy(mode=Mode.YOLO)
    assert not policy.needs_approval("merge", "main", protected=True)
    assert policy.audit[-1]["decision"] == "yolo_auto_approved"


def test_classify_system():
    assert classify_system(description="build a web app with fastapi") is SystemType.WEB_APP
    assert classify_system(description="make a cli tool") is SystemType.CLI


def test_spec_fail_then_pass():
    draft = draft_from_text("fix it", repo=".")
    assert check_spec(draft).gate == "FAIL"
    full = SpecDraft(
        goal="Build a todo web app",
        system_type=SystemType.WEB_APP,
        scope="list and create todos",
        acceptance="GET / returns 200",
        test_plan="pytest",
        repo=".",
        journeys="user creates a todo",
    )
    result = check_spec(full)
    assert result.gate == "PASS"
    assert result.questions == ()


def test_draft_from_text_markers_pass():
    text = (
        "build a cli tool that sorts lines\n"
        "scope: read stdin and write sorted stdout\n"
        "acceptance: piping unsorted input prints sorted output\n"
        "- empty input prints nothing\n"
        "test_plan: pytest tests/test_sort.py\n"
        "repo: .\n"
    )
    draft = draft_from_text(text)
    assert draft.system_type is SystemType.CLI
    assert draft.goal == "build a cli tool that sorts lines"
    assert draft.acceptance == (
        "piping unsorted input prints sorted output\n- empty input prints nothing"
    )
    assert check_spec(draft).gate == "PASS"


def test_draft_from_text_all_markers_web_app_pass():
    text = (
        "goal: Build a todo web app\n"
        "scope: list and create todos\n"
        "acceptance: GET / returns 200\n"
        "- POST /todos creates a todo\n"
        "test_plan: pytest tests/test_app.py\n"
        "repo: .\n"
        "journeys: user creates a todo\n"
    )
    draft = draft_from_text(text)
    assert draft.system_type is SystemType.WEB_APP
    assert draft.goal == "Build a todo web app"
    assert draft.journeys == "user creates a todo"
    assert draft.acceptance == "GET / returns 200\n- POST /todos creates a todo"
    assert check_spec(draft).gate == "PASS"


def test_draft_from_text_marker_fallbacks():
    draft = draft_from_text("build a cli tool that sorts lines from stdin")
    assert draft.goal == "build a cli tool that sorts lines from stdin"
    assert draft.scope == "build a cli tool that sorts lines from stdin"
    assert draft.acceptance == ""


@pytest.mark.asyncio
async def test_agency_app_renders_and_slash():
    app = AgencyApp()
    async with app.run_test() as pilot:
        await pilot.press(*"/help")
        await pilot.press("enter")
        await pilot.pause()
        assert app.query_one("#transcript") is not None
        assert app.query_one("#composer") is not None


@pytest.mark.asyncio
async def test_forge_app_renders_and_intake_gates():
    app = ForgeApp(repo=".")
    async with app.run_test() as pilot:
        await pilot.press(*"/help")
        await pilot.press("enter")
        await pilot.pause()
        assert app.query_one("#transcript") is not None
        # Ambiguous request must not pass the spec gate
        await app.handle_message("fix it")
        assert app.spec_passed is False


@pytest.mark.asyncio
async def test_forge_app_intake_markers_pass(tmp_path, monkeypatch):
    app = ForgeApp(repo=str(tmp_path))

    async def noop_build() -> None:
        return None

    monkeypatch.setattr(app, "_build", noop_build)
    request = (
        "build a cli tool that sorts lines\n"
        "scope: read stdin and write sorted stdout\n"
        "non_goals: no network access\n"
        "acceptance: piping unsorted input prints sorted output\n"
        "test_plan: pytest\n"
        "repo: .\n"
    )
    async with app.run_test():
        await app.handle_message(request)
    assert app.spec_passed is True
    assert (tmp_path / ".forge-spec.md").exists()


@pytest.mark.asyncio
async def test_forge_app_spec_slash_uses_new_text(tmp_path, monkeypatch):
    app = ForgeApp(repo=str(tmp_path))

    async def noop_build() -> None:
        return None

    monkeypatch.setattr(app, "_build", noop_build)
    request = (
        "build a cli tool that sorts lines\n"
        "scope: read stdin and write sorted stdout\n"
        "non_goals: no network access\n"
        "acceptance: piping unsorted input prints sorted output\n"
        "test_plan: pytest\n"
        "repo: .\n"
    )
    async with app.run_test():
        await app.handle_slash("/spec " + request)
    assert app.spec_passed is True


def test_forge_entry_help(capsys):
    import sys

    from agency.forge.cli import main

    old = sys.argv
    sys.argv = ["forge", "--help"]
    try:
        main()
    finally:
        sys.argv = old
    assert "Forge TUI" in capsys.readouterr().out
