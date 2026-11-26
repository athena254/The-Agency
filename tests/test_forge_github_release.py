"""Tests for the guarded GitHub tag + release slice (no real network).

Every test uses a tmp_path git repository. subprocess/gh are intercepted at the
module boundary: local git operations really run, while push/ls-remote/gh are
recorded and never contact a remote.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agency.forge import github_release as gr
from agency.forge.github_release import plan_github_release

_REAL_RUN = subprocess.run


def _git(repo: Path, *args: str) -> str:
    proc = _REAL_RUN(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )
    return proc.stdout.strip()


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    _REAL_RUN(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True, text=True)
    _git(repo, "config", "user.email", "forge@test.local")
    _git(repo, "config", "user.name", "Forge Test")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    return repo


def _plan(root: Path, **overrides: Any) -> Any:
    options: dict[str, Any] = {
        "tag": "v1.2.3",
        "gates": True,
        "approved_by": "alice",
        "repo": "acme/widgets",
        "allow_repos": ["acme/widgets"],
        "dry_run": True,
    }
    options.update(overrides)
    tag = options.pop("tag")
    return plan_github_release(root, tag, **options)


@pytest.fixture(autouse=True)
def _fake_remote(monkeypatch: pytest.MonkeyPatch) -> tuple[list[list[str]], dict[str, int]]:
    calls: list[list[str]] = []
    state = {"gh_auth_rc": 0, "push_rc": 0, "release_rc": 0}

    def fake_run(args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        argv = [str(arg) for arg in args]
        calls.append(argv)
        if "ls-remote" in argv:
            return subprocess.CompletedProcess(args, 2, "", "")
        if "push" in argv:
            return subprocess.CompletedProcess(args, state["push_rc"], "", "")
        if argv and argv[0] == "gh":
            rc = state["gh_auth_rc"] if "auth" in argv else state["release_rc"]
            return subprocess.CompletedProcess(args, rc, "", "")
        return _REAL_RUN(args, **kwargs)

    monkeypatch.setattr(gr, "subprocess", SimpleNamespace(run=fake_run))
    monkeypatch.setattr(gr, "shutil", SimpleNamespace(which=lambda name: f"/fake/bin/{name}"))
    monkeypatch.delenv("AGENCY_FORGE_ALLOWED_RELEASE_REPOS", raising=False)
    return calls, state


# 1 ------------------------------------------------------------------------ #
def test_dry_run_plans_exact_commands_and_creates_nothing(
    tmp_path: Path, _fake_remote: tuple[list[list[str]], dict[str, int]]
) -> None:
    calls, _state = _fake_remote
    repo = _init_repo(tmp_path)
    report = _plan(repo, gates=[True, "PASS", SimpleNamespace(gate="PASS")])

    assert report.gate == "PASS"
    assert report.created is False
    assert report.dry_run is True
    assert report.source_sha == _git(repo, "rev-parse", "HEAD")
    assert len(report.commands) == 3
    assert report.commands[0] == f"git -C {repo} tag -a v1.2.3 -m v1.2.3"
    assert report.commands[1] == (
        f"git -C {repo} push origin refs/tags/v1.2.3:refs/tags/v1.2.3"
    )
    assert report.commands[2] == (
        "gh release create v1.2.3 --repo acme/widgets --title v1.2.3 "
        "--notes Release v1.2.3 --verify-tag"
    )
    assert "release_authorization_is_owner_scoped" in report.caveats
    assert "release_not_authorized" in report.caveats

    # Nothing was created; no push/gh invocation happened.
    assert not any("push" in call or "gh" in call for call in calls)
    assert not (repo / ".forge-release-proof.jsonl").exists()
    check = _REAL_RUN(
        ["git", "-C", str(repo), "rev-parse", "--verify", "refs/tags/v1.2.3"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert check.returncode != 0


# 2 ------------------------------------------------------------------------ #
def test_missing_or_machine_approval_blocks(
    tmp_path: Path, _fake_remote: tuple[list[list[str]], dict[str, int]]
) -> None:
    calls, _state = _fake_remote
    repo = _init_repo(tmp_path)

    missing = _plan(repo, approved_by="")
    assert missing.gate == "BLOCKED"
    assert missing.created is False
    assert missing.commands == ()
    assert "approval_missing" in missing.blocked_reasons
    assert "release_not_authorized" in missing.caveats

    for machine in ("agent", "forge", "auto", "system", "cli", "bot"):
        report = _plan(repo, approved_by=machine)
        assert report.gate == "BLOCKED"
        assert report.commands == ()
        assert "approver_not_human" in report.blocked_reasons

    assert not any("push" in call or "gh" in call for call in calls)


# 3 ------------------------------------------------------------------------ #
def test_non_pass_gate_blocks(
    tmp_path: Path, _fake_remote: tuple[list[list[str]], dict[str, int]]
) -> None:
    repo = _init_repo(tmp_path)
    for gates in ("FAIL", False, [True, SimpleNamespace(gate="PASS"), "FAIL"], None, {}):
        report = _plan(repo, gates=gates)
        assert report.gate == "BLOCKED"
        assert report.created is False
        assert report.commands == ()
        assert any(reason.startswith("gates_not_pass:") for reason in report.blocked_reasons)


# 4 ------------------------------------------------------------------------ #
def test_disallowed_repo_blocks_and_env_allow_list_works(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _fake_remote: tuple[list[list[str]], dict[str, int]],
) -> None:
    repo = _init_repo(tmp_path)
    report = _plan(repo, repo="evil/attacker")
    assert report.gate == "BLOCKED"
    assert report.created is False
    assert report.commands == ()
    assert "repo_not_allowlisted" in report.blocked_reasons

    monkeypatch.setenv("AGENCY_FORGE_ALLOWED_RELEASE_REPOS", "acme/widgets, other/repo")
    allowed_by_env = _plan(repo, allow_repos=None)
    assert allowed_by_env.gate == "PASS"
    assert allowed_by_env.blocked_reasons == ()


# 5 ------------------------------------------------------------------------ #
@pytest.mark.parametrize("tag", ["v1.2", "1.2.3", "v1.2.3;rm-rf", "release-1", "v1.2.3 "])
def test_invalid_tag_blocks(
    tmp_path: Path,
    tag: str,
    _fake_remote: tuple[list[list[str]], dict[str, int]],
) -> None:
    repo = _init_repo(tmp_path)
    report = _plan(repo, tag=tag)
    assert report.gate == "BLOCKED"
    assert report.created is False
    assert report.commands == ()
    assert "invalid_tag_format" in report.blocked_reasons


# 6 ------------------------------------------------------------------------ #
def test_missing_gh_blocks_on_execute(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _fake_remote: tuple[list[list[str]], dict[str, int]],
) -> None:
    calls, state = _fake_remote
    repo = _init_repo(tmp_path)
    proof = tmp_path / "proof.jsonl"

    with monkeypatch.context() as scoped:
        scoped.setattr(gr.shutil, "which", lambda name: None)
        missing = _plan(repo, dry_run=False, proof_log=proof)
    assert missing.gate == "BLOCKED"
    assert missing.created is False
    assert missing.commands == ()
    assert "gh_cli_unavailable" in missing.blocked_reasons

    state["gh_auth_rc"] = 1
    unauthenticated = _plan(repo, dry_run=False, proof_log=proof)
    assert unauthenticated.gate == "BLOCKED"
    assert unauthenticated.created is False
    assert unauthenticated.commands == ()
    assert "gh_auth_status_failed" in unauthenticated.blocked_reasons

    # No tag, no proof, no push/release commands ran in either case.
    assert not proof.exists()
    assert not any("push" in call or "release" in call for call in calls)
    check = _REAL_RUN(
        ["git", "-C", str(repo), "rev-parse", "--verify", "refs/tags/v1.2.3"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert check.returncode != 0


# 7 ------------------------------------------------------------------------ #
def test_execute_creates_annotated_tag_and_proof(
    tmp_path: Path, _fake_remote: tuple[list[list[str]], dict[str, int]]
) -> None:
    calls, _state = _fake_remote
    repo = _init_repo(tmp_path)
    proof = tmp_path / "proof.jsonl"
    head = _git(repo, "rev-parse", "HEAD")
    report = _plan(
        repo,
        dry_run=False,
        proof_log=proof,
        title="Release 1.2.3",
        notes="Ship it",
    )

    assert report.gate == "PASS"
    assert report.created is True
    assert report.dry_run is False
    assert report.source_sha == head
    assert report.caveats == (
        "release_authorization_is_owner_scoped",
        "human_approval_recorded",
    )
    assert "release_not_authorized" not in report.caveats

    assert _git(repo, "rev-parse", "v1.2.3^{commit}") == head
    assert _git(repo, "cat-file", "-t", "v1.2.3") == "tag"

    event = json.loads(proof.read_text(encoding="utf-8").strip())
    assert event == {
        "event": "github_release",
        "report_id": report.report_id,
        "tag": "v1.2.3",
        "sha": head,
        "repo": "acme/widgets",
        "approver": "alice",
        "gate": "PASS",
    }

    assert report.commands[2] == (
        "gh release create v1.2.3 --repo acme/widgets --title Release 1.2.3 "
        "--notes Ship it --verify-tag"
    )
    assert all("--force" not in command for command in report.commands)
    assert all("--draft" not in command for command in report.commands)
    assert any("ls-remote" in call for call in calls)
    assert any("push" in call for call in calls)
    assert any(call[:2] == ["gh", "auth"] for call in calls)


# 8 ------------------------------------------------------------------------ #
def test_no_secrets_or_tokens_in_any_report_field(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _fake_remote: tuple[list[list[str]], dict[str, int]],
) -> None:
    repo = _init_repo(tmp_path)
    secrets = {
        "GH_TOKEN": "ghp_SUPERSECRET_abc123",
        "GITHUB_TOKEN": "ghs_TOPSECRET_xyz789",
        "AGENCY_FORGE_REVIEW_API_KEYS": "sk-ANOTHERSECRET_456",
    }
    for key, value in secrets.items():
        monkeypatch.setenv(key, value)

    reports = [
        _plan(repo),
        _plan(repo, dry_run=False, proof_log=tmp_path / "proof.jsonl", notes="ordinary notes"),
    ]
    for report in reports:
        serialized = report.model_dump_json()
        for secret in secrets.values():
            assert secret not in serialized
        for field_name in type(report).model_fields:
            value = getattr(report, field_name)
            assert all(secret not in str(value) for secret in secrets.values())
            assert not any(
                marker in field_name.lower()
                for marker in ("token", "secret", "password", "api_key")
            )


# Additional hard preconditions -------------------------------------------- #
def test_dirty_tree_existing_tag_and_sha_mismatch_block(
    tmp_path: Path, _fake_remote: tuple[list[list[str]], dict[str, int]]
) -> None:
    repo = _init_repo(tmp_path)

    (repo / "dirty.txt").write_text("uncommitted\n", encoding="utf-8")
    dirty = _plan(repo)
    assert dirty.gate == "BLOCKED"
    assert dirty.commands == ()
    assert "working_tree_dirty" in dirty.blocked_reasons
    (repo / "dirty.txt").unlink()

    wrong_sha = _plan(repo, sha="0" * 40)
    assert wrong_sha.gate == "BLOCKED"
    assert "sha_mismatch" in wrong_sha.blocked_reasons

    right_sha = _plan(repo, sha=_git(repo, "rev-parse", "HEAD"))
    assert right_sha.gate == "PASS"

    _git(repo, "tag", "-a", "v1.2.3", "-m", "preexisting")
    existing = _plan(repo)
    assert existing.gate == "BLOCKED"
    assert "tag_exists_locally" in existing.blocked_reasons
    assert existing.created is False
    assert existing.commands == ()
