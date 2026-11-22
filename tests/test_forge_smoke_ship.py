"""Tests for Forge Run+Ship slices: smoke and auto-ship."""

from __future__ import annotations

import subprocess
from pathlib import Path

from agency.forge.ship import ship_branch
from agency.forge.smoke import smoke_app
from agency.forge.worktree import create_worktree


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _git_out(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "-b", "main", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    _git(path, "config", "user.email", "forge@test.local")
    _git(path, "config", "user.name", "Forge Test")
    (path / "README.md").write_text("base\n", encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-m", "base")


def _make_worktree(repo: Path, tmp: Path) -> Path:
    _git(repo, "branch", "feature-x")
    info = create_worktree(repo, "feature-x", base_ref="feature-x", worktree_root=tmp / "wts")
    wt = Path(info.worktree_path)
    (wt / "feature.txt").write_text("feature\n", encoding="utf-8")
    _git(wt, "add", ".")
    _git(wt, "commit", "-m", "feature")
    return wt


def test_smoke_pass(tmp_path: Path):
    (tmp_path / "app.py").write_text(
        "from fastapi import FastAPI\n"
        "app = FastAPI()\n"
        "@app.get('/')\n"
        "def root():\n"
        "    return {'ok': True}\n",
        encoding="utf-8",
    )
    report = smoke_app(tmp_path, timeout_s=10)
    assert report.gate == "PASS"
    assert report.status_code == 200
    assert report.port > 0


def test_smoke_pass_http_server_fallback(tmp_path: Path):
    (tmp_path / "index.html").write_text("<h1>hi</h1>\n", encoding="utf-8")
    report = smoke_app(tmp_path, timeout_s=10)
    assert report.gate == "PASS"
    assert report.status_code == 200


def test_smoke_fail(tmp_path: Path):
    (tmp_path / "app.py").write_text(
        "import sys\nsys.exit(1)\n",
        encoding="utf-8",
    )
    report = smoke_app(tmp_path, timeout_s=3)
    assert report.gate == "FAIL"
    assert report.status_code != 200


def test_ship_refuses_on_fail(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    wt = _make_worktree(repo, tmp_path)
    report = ship_branch(
        wt,
        "main",
        pipeline_gate="FAIL",
        smoke_gate="PASS",
        allow_auto_merge=True,
        proof_log=tmp_path / "proof.jsonl",
    )
    assert report.gate == "FAIL"
    assert report.merged is False
    assert any("pipeline_gate_not_pass" in r for r in report.blocked_reasons)
    # Branch still exists, main unchanged.
    assert _git_out(repo, "rev-parse", "--verify", "refs/heads/feature-x")
    assert not (repo / "feature.txt").exists()

    report2 = ship_branch(
        wt,
        "main",
        pipeline_gate="PASS",
        smoke_gate="FAIL",
        allow_auto_merge=True,
        proof_log=tmp_path / "proof.jsonl",
    )
    assert report2.merged is False
    assert any("smoke_gate_not_pass" in r for r in report2.blocked_reasons)


def test_ship_requires_human_approval_by_default(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    wt = _make_worktree(repo, tmp_path)
    report = ship_branch(
        wt,
        "main",
        pipeline_gate="PASS",
        smoke_gate="PASS",
        proof_log=tmp_path / "proof.jsonl",
    )
    assert report.gate == "FAIL"
    assert report.merged is False
    assert any("human_approval_required" in r for r in report.blocked_reasons)
    assert _git_out(repo, "rev-parse", "--verify", "refs/heads/feature-x")
    assert not (repo / "feature.txt").exists()


def test_ship_succeeds_on_pass(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    wt = _make_worktree(repo, tmp_path)
    proof = tmp_path / "proof.jsonl"
    report = ship_branch(
        wt,
        "main",
        pipeline_gate="PASS",
        smoke_gate="PASS",
        allow_auto_merge=True,
        proof_log=proof,
    )
    assert report.gate == "PASS"
    assert report.merged is True
    assert report.source_branch == "feature-x"
    assert (repo / "feature.txt").exists()
    assert (repo / "feature.txt").read_text(encoding="utf-8") == "feature\n"
    # Branch cleaned up.
    proc = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", "refs/heads/feature-x"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    # Proof event appended.
    assert proof.is_file()
    content = proof.read_text(encoding="utf-8")
    assert "feature-x" in content
    assert '"merged": true' in content


def test_ship_default_proof_path(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    wt = _make_worktree(repo, tmp_path)
    report = ship_branch(wt, "main", pipeline_gate="PASS", smoke_gate="PASS", approved=True)
    assert report.merged is True
    assert Path(report.proof_path).is_file()


def test_ship_fail_gate_never_merges_even_with_approval(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    wt = _make_worktree(repo, tmp_path)
    report = ship_branch(
        wt,
        "main",
        pipeline_gate="FAIL",
        smoke_gate="PASS",
        allow_auto_merge=True,
        approved=True,
        proof_log=tmp_path / "proof.jsonl",
    )
    assert report.gate == "FAIL"
    assert report.merged is False
    assert any("pipeline_gate_not_pass" in r for r in report.blocked_reasons)
    assert _git_out(repo, "rev-parse", "--verify", "refs/heads/feature-x")
    assert not (repo / "feature.txt").exists()


# --------------------------------------------------------------------------- #
# branch-ownership rules: never desync a checkout
# --------------------------------------------------------------------------- #
def test_ship_refuses_target_checked_out_in_sibling_worktree(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    _git(repo, "branch", "scratch")
    _git(repo, "checkout", "scratch")
    sibling = tmp_path / "wt-main"
    _git(repo, "worktree", "add", str(sibling), "main")
    wt = _make_worktree(repo, tmp_path)

    report = ship_branch(wt, "main", pipeline_gate="PASS", smoke_gate="PASS", approved=True)
    assert report.gate == "FAIL"
    assert any(r.startswith("target_checked_out_elsewhere:") for r in report.blocked_reasons)
    assert report.merged is False
    # untouched: the sibling worktree still sees main at its old sha
    assert _git_out(sibling, "rev-parse", "HEAD") == _git_out(repo, "rev-parse", "main")


def test_ship_refuses_when_main_repo_dirty(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    wt = _make_worktree(repo, tmp_path)
    (repo / "uncommitted.txt").write_text("user work\n", encoding="utf-8")

    report = ship_branch(wt, "main", pipeline_gate="PASS", smoke_gate="PASS", approved=True)
    assert report.gate == "FAIL"
    assert "main_repo_dirty" in report.blocked_reasons
    assert report.merged is False
    assert _git_out(repo, "rev-parse", "HEAD") == _git_out(repo, "rev-parse", "main")


def test_ship_ref_only_fast_forward_when_target_not_checked_out(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    _git(repo, "branch", "dev")
    _git(repo, "checkout", "dev")
    (repo / "dev.txt").write_text("dev work\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "dev commit")
    wt = _make_worktree(repo, tmp_path)
    feature_sha = _git_out(wt, "rev-parse", "HEAD")
    dev_sha = _git_out(repo, "rev-parse", "HEAD")

    report = ship_branch(wt, "main", pipeline_gate="PASS", smoke_gate="PASS", approved=True)
    assert report.gate == "PASS"
    assert report.merged is True
    # ref moved, working tree untouched
    assert _git_out(repo, "rev-parse", "main") == feature_sha
    assert _git_out(repo, "rev-parse", "HEAD") == dev_sha
    status = [x for x in _git_out(repo, "status", "--porcelain").splitlines() if ".forge-proof.jsonl" not in x]
    assert status == []
    assert (repo / "dev.txt").read_text(encoding="utf-8") == "dev work\n"
