"""Spec-gated Forge TUI build loop — driven entirely by fake agents/pipelines.

No real coding agent (opencode) is launched in any of these tests.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from agency.tui.forge_app import BuildResult, ForgeApp, build_from_spec

VALID_SPEC = (
    "build a cli tool that sorts lines\n"
    "scope: read stdin and write sorted stdout\n"
    "acceptance: piping unsorted input prints sorted output\n"
    "test_plan: pytest\n"
    "repo: .\n"
)


@dataclass(frozen=True)
class FakeAgentReport:
    gate: str = "PASS"
    changed_paths: tuple[str, ...] = ("new_feature.py",)
    worktree_path: str = ""
    isolation_violations: tuple[str, ...] = ()
    main_repo_changes: tuple[str, ...] = ()


@dataclass(frozen=True)
class FakeGate:
    gate: str = "PASS"


@dataclass(frozen=True)
class FakePipelineReport:
    gate: str = "PASS"
    inspection: FakeGate = field(default_factory=FakeGate)
    security: FakeGate = field(default_factory=FakeGate)
    tests: FakeGate = field(default_factory=FakeGate)


def _fake_agent(calls: list[dict[str, Any]], report: FakeAgentReport) -> Any:
    def runner(repo_root: Any, command: Any, task: str, **kwargs: Any) -> FakeAgentReport:
        calls.append({"repo_root": repo_root, "command": command, "task": task, **kwargs})
        return report

    return runner


def _fake_pipeline(calls: list[dict[str, Any]], report: FakePipelineReport) -> Any:
    def runner(root: Any, changed: Any, **kwargs: Any) -> FakePipelineReport:
        calls.append({"root": root, "changed": changed, **kwargs})
        return report

    return runner


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (
        ["init"],
        ["config", "user.email", "t@t.com"],
        ["config", "user.name", "t"],
    ):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    (repo / "README.md").write_text("initial\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True
    )
    return repo


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file() and ".git" not in p.parts
    }


@pytest.mark.asyncio
async def test_thin_spec_blocked_and_no_agent_invoked(tmp_path, monkeypatch):
    agent_calls: list[dict[str, Any]] = []
    pipeline_calls: list[dict[str, Any]] = []
    app = ForgeApp(
        repo=str(tmp_path),
        agent_runner=_fake_agent(agent_calls, FakeAgentReport()),
        pipeline_runner=_fake_pipeline(pipeline_calls, FakePipelineReport()),
    )
    said: list[str] = []
    app.say = said.append

    async def _raise(*_a: Any, **_k: Any) -> str:
        raise RuntimeError("no llm in tests")

    monkeypatch.setattr(app.brain, "ask", _raise)

    async with app.run_test():
        await app.handle_message("fix it")

    assert app.spec_passed is False
    assert agent_calls == []
    assert pipeline_calls == []
    assert not (tmp_path / ".forge-spec.md").exists()
    assert any("Not enough to build" in line for line in said)


@pytest.mark.asyncio
async def test_valid_spec_invokes_fake_agent_and_surfaces_verdict(tmp_path):
    agent_calls: list[dict[str, Any]] = []
    pipeline_calls: list[dict[str, Any]] = []
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    app = ForgeApp(
        repo=str(tmp_path),
        agent_runner=_fake_agent(
            agent_calls,
            FakeAgentReport(
                changed_paths=("feature.py", "tests/test_feature.py"), worktree_path=str(worktree)
            ),
        ),
        pipeline_runner=_fake_pipeline(pipeline_calls, FakePipelineReport()),
    )
    said: list[str] = []
    app.say = said.append

    async with app.run_test():
        await app.handle_message(VALID_SPEC)

    assert app.spec_passed is True
    assert len(agent_calls) == 1
    assert agent_calls[0]["repo_root"] == Path(str(tmp_path))
    assert agent_calls[0]["cleanup"] is False
    assert "spec minimum met" in " ".join(said)
    assert any("build gate=PASS" in line for line in said)
    assert any("feature.py, tests/test_feature.py" in line for line in said)


def test_pipeline_runs_with_tests_enabled_on_worktree(tmp_path):
    pipeline_calls: list[dict[str, Any]] = []
    worktree = tmp_path / "wt"
    worktree.mkdir()
    result = build_from_spec(
        tmp_path,
        VALID_SPEC,
        agent_runner=_fake_agent([], FakeAgentReport(worktree_path=str(worktree))),
        pipeline_runner=_fake_pipeline(pipeline_calls, FakePipelineReport()),
    )

    assert isinstance(result, BuildResult)
    assert len(pipeline_calls) == 1
    assert pipeline_calls[0]["run_tests_flag"] is True
    assert pipeline_calls[0]["root"] == str(worktree)
    assert pipeline_calls[0]["changed"] == ["new_feature.py"]
    assert result.tests_gate == "PASS"
    assert result.gate == "PASS"


def test_original_checkout_never_written_to(tmp_path):
    repo = _git_repo(tmp_path)
    before = _snapshot(repo)
    worktree = tmp_path / "wt"
    worktree.mkdir()

    def fake_agent(repo_root: Any, command: Any, task: str, **kwargs: Any) -> FakeAgentReport:
        (worktree / "new_feature.py").write_text("x = 1\n", encoding="utf-8")
        return FakeAgentReport(changed_paths=("new_feature.py",), worktree_path=str(worktree))

    pipeline_calls: list[dict[str, Any]] = []
    result = build_from_spec(
        repo,
        VALID_SPEC,
        agent_runner=fake_agent,
        pipeline_runner=_fake_pipeline(pipeline_calls, FakePipelineReport()),
    )

    assert _snapshot(repo) == before
    assert pipeline_calls[0]["root"] == str(worktree)
    assert (worktree / "new_feature.py").exists()
    assert result.gate == "PASS"


def test_agent_skips_review_so_the_llm_runs_once(tmp_path):
    """The verification pipeline runs the LLM review; the agent pass must not too."""
    agent_calls: list[dict[str, Any]] = []
    pipeline_calls: list[dict[str, Any]] = []
    worktree = tmp_path / "wt"
    worktree.mkdir()

    build_from_spec(
        tmp_path,
        VALID_SPEC,
        agent_runner=_fake_agent(agent_calls, FakeAgentReport(worktree_path=str(worktree))),
        pipeline_runner=_fake_pipeline(pipeline_calls, FakePipelineReport()),
    )

    assert agent_calls[0]["run_tests_flag"] is False
    assert agent_calls[0]["run_review_flag"] is False
    assert pipeline_calls[0]["run_tests_flag"] is True
