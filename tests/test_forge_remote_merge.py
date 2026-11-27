"""Tests for the gated remote merge primitive (no real remote writes)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agency.forge.remote_merge import plan_remote_merge


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


@pytest.fixture()
def repo_and_remote(tmp_path: Path) -> tuple[Path, Path]:
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-b", "main")
    _git(work, "config", "user.email", "test@example.com")
    _git(work, "config", "user.name", "Test")
    (work / "a.txt").write_text("one\n", encoding="utf-8")
    _git(work, "add", "a.txt")
    _git(work, "commit", "-m", "init")
    bare = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", str(bare)],
        capture_output=True,
        check=True,
    )
    _git(work, "remote", "add", "origin", str(bare))
    _git(work, "push", "-u", "origin", "main")
    return work, bare


def _feature_commit(work: Path) -> None:
    _git(work, "checkout", "-b", "feature")
    (work / "b.txt").write_text("two\n", encoding="utf-8")
    _git(work, "add", "b.txt")
    _git(work, "commit", "-m", "feature")


def _all_pass() -> dict[str, str]:
    return {"pipeline": "PASS", "security": "PASS", "tests": "PASS", "review": "PASS"}


def test_dry_run_plans_without_writing(repo_and_remote: tuple[Path, Path]) -> None:
    work, bare = repo_and_remote
    _feature_commit(work)
    before = _git(bare, "rev-parse", "refs/heads/main")
    report = plan_remote_merge(
        work,
        "origin",
        "main",
        gates=_all_pass(),
        approved_by="alice",
        allow_remotes=["origin"],
    )
    assert report.gate == "PASS"
    assert report.pushed is False
    assert report.dry_run is True
    assert any("push" in command for command in report.commands)
    assert "dry_run" in report.caveats
    assert "release_not_authorized" in report.caveats
    assert _git(bare, "rev-parse", "refs/heads/main") == before


def test_missing_approval_blocks(repo_and_remote: tuple[Path, Path]) -> None:
    work, _ = repo_and_remote
    report = plan_remote_merge(work, "origin", "main", gates=_all_pass(), allow_remotes=["origin"])
    assert report.gate == "BLOCKED"
    assert "human_approval_required" in report.blocked_reasons
    assert report.pushed is False


def test_generic_approver_blocks(repo_and_remote: tuple[Path, Path]) -> None:
    work, _ = repo_and_remote
    report = plan_remote_merge(
        work,
        "origin",
        "main",
        gates=_all_pass(),
        approved_by="agent",
        allow_remotes=["origin"],
    )
    assert report.gate == "BLOCKED"
    assert "human_approval_required" in report.blocked_reasons


def test_gate_failure_blocks(repo_and_remote: tuple[Path, Path]) -> None:
    work, _ = repo_and_remote
    report = plan_remote_merge(
        work,
        "origin",
        "main",
        gates={**_all_pass(), "review": "FAIL"},
        approved_by="alice",
        allow_remotes=["origin"],
    )
    assert report.gate == "BLOCKED"
    assert "gate_not_pass:review=FAIL" in report.blocked_reasons


def test_remote_not_allow_listed_blocks(repo_and_remote: tuple[Path, Path]) -> None:
    work, _ = repo_and_remote
    report = plan_remote_merge(
        work,
        "origin",
        "main",
        gates=_all_pass(),
        approved_by="alice",
        allow_remotes=["upstream"],
    )
    assert report.gate == "BLOCKED"
    assert "remote_not_allow_listed" in report.blocked_reasons


def test_dirty_tree_blocks(repo_and_remote: tuple[Path, Path]) -> None:
    work, _ = repo_and_remote
    (work / "dirty.txt").write_text("uncommitted\n", encoding="utf-8")
    report = plan_remote_merge(
        work,
        "origin",
        "main",
        gates=_all_pass(),
        approved_by="alice",
        allow_remotes=["origin"],
    )
    assert report.gate == "BLOCKED"
    assert "working_tree_dirty" in report.blocked_reasons


def test_unreviewed_paths_in_merge_block(repo_and_remote: tuple[Path, Path]) -> None:
    work, _ = repo_and_remote
    _feature_commit(work)
    report = plan_remote_merge(
        work,
        "origin",
        "main",
        gates=_all_pass(),
        approved_by="alice",
        allow_remotes=["origin"],
        changed_paths=["a.txt"],  # b.txt is in the merge but was never reviewed
        execute=True,
        dry_run=False,
    )
    assert report.gate == "BLOCKED"
    assert "unreviewed_paths_in_merge" in report.blocked_reasons
    assert report.pushed is False


def test_execute_pushes_fast_forward_and_writes_proof(repo_and_remote: tuple[Path, Path]) -> None:
    work, bare = repo_and_remote
    _feature_commit(work)
    report = plan_remote_merge(
        work,
        "origin",
        "main",
        gates=_all_pass(),
        approved_by="alice",
        allow_remotes=["origin"],
        execute=True,
        dry_run=False,
    )
    assert report.gate == "PASS"
    assert report.pushed is True
    assert "human_approval_recorded" in report.caveats
    assert _git(bare, "rev-parse", "refs/heads/main") == _git(work, "rev-parse", "HEAD")
    proof = Path(report.proof_path)
    assert proof.is_file()
    event = json.loads(proof.read_text(encoding="utf-8").splitlines()[-1])
    assert event["report_id"] == report.report_id
    assert event["approved_by"] == "alice"
    assert all("--force" not in command for command in report.commands)


def test_non_fast_forward_blocked(repo_and_remote: tuple[Path, Path], tmp_path: Path) -> None:
    work, bare = repo_and_remote
    _feature_commit(work)
    other = tmp_path / "other"
    subprocess.run(
        ["git", "clone", str(bare), str(other)],
        capture_output=True,
        check=True,
    )
    _git(other, "config", "user.email", "test@example.com")
    _git(other, "config", "user.name", "Test")
    _git(other, "checkout", "-b", "main", "--track", "origin/main")
    (other / "c.txt").write_text("three\n", encoding="utf-8")
    _git(other, "add", "c.txt")
    _git(other, "commit", "-m", "other-wins")
    _git(other, "push", "origin", "main")
    report = plan_remote_merge(
        work,
        "origin",
        "main",
        gates=_all_pass(),
        approved_by="alice",
        allow_remotes=["origin"],
    )
    assert report.gate == "BLOCKED"
    assert "non_fast_forward" in report.blocked_reasons
    assert report.pushed is False


def test_credentials_in_remote_never_land_in_report(repo_and_remote: tuple[Path, Path]) -> None:
    work, _ = repo_and_remote
    secret_remote = "https://user:supersecret@example.invalid/repo.git"
    report = plan_remote_merge(
        work,
        secret_remote,
        "main",
        gates=_all_pass(),
        approved_by="alice",
        allow_remotes=[secret_remote],
    )
    assert report.gate == "BLOCKED"
    assert "remote_credential_or_scheme_invalid" in report.blocked_reasons
    assert "supersecret" not in report.model_dump_json()
