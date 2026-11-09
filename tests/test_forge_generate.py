"""Tests for Forge coding-agent create/write ops and app generation."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agency.agents.coding import AgencyCodingAgent
from agency.forge import generate_app, run_coding_agent
from agency.forge.generate import TEMPLATES_DIR


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "m.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


def test_create_file_op(tmp_path: Path):
    repo = _git_repo(tmp_path)
    report = run_coding_agent(
        repo, "feat/create", [{"op": "create_file", "path": "new.py", "content": "y = 2\n"}]
    )
    assert report.gate == "PASS"
    assert report.edits_applied == 1
    assert Path(report.worktree_path, "new.py").read_text(encoding="utf-8") == "y = 2\n"


def test_write_file_creates_dirs(tmp_path: Path):
    repo = _git_repo(tmp_path)
    report = run_coding_agent(
        repo,
        "feat/write",
        [{"op": "write_file", "path": "pkg/sub/mod.py", "content": "z = 3\n"}],
    )
    assert report.gate == "PASS"
    assert Path(report.worktree_path, "pkg/sub/mod.py").is_file()


def test_create_file_fails_if_exists(tmp_path: Path):
    repo = _git_repo(tmp_path)
    report = run_coding_agent(
        repo, "feat/exists", [{"op": "create_file", "path": "m.py", "content": "y = 2\n"}]
    )
    assert report.gate == "FAIL"


def test_create_file_protected_rejected(tmp_path: Path):
    repo = _git_repo(tmp_path)
    with pytest.raises(ValueError, match="protected"):
        run_coding_agent(
            repo, "feat/prot", [{"op": "create_file", "path": ".env", "content": "A=1\n"}]
        )


def test_patch_still_single_occurrence(tmp_path: Path):
    repo = _git_repo(tmp_path)
    report = run_coding_agent(
        repo, "feat/patch", [{"path": "m.py", "old": "x = 1", "new": "x = 9"}]
    )
    assert report.gate == "PASS"


def test_templates_exist():
    for name in ("pyproject.toml", "Dockerfile", ".env.example", "README.md"):
        assert (TEMPLATES_DIR / name).is_file(), name


def test_generate_default_app(tmp_path: Path):
    out = tmp_path / "app"
    report = generate_app(None, out)
    assert report.gate == "PASS"
    assert (out / "app.py").is_file()
    assert (out / "test_app.py").is_file()
    assert (out / "pyproject.toml").is_file()
    assert (out / "README.md").is_file()
    assert report.pipeline is not None and report.pipeline.gate == "PASS"


def test_generate_from_spec(tmp_path: Path):
    out = tmp_path / "spec-app"
    spec = {
        "files": {
            "hello.py": "VALUE = 42\n",
            "test_hello.py": "from hello import VALUE\n\n\ndef test_v():\n    assert VALUE == 42\n",
        }
    }
    report = generate_app(spec, out)
    assert report.gate == "PASS"
    assert (out / "hello.py").is_file()


def test_generate_with_agent_hook(tmp_path: Path):
    repo = _git_repo(tmp_path)
    agent = AgencyCodingAgent(workspace=repo)
    report = generate_app(None, repo, agent)
    assert report.gate == "PASS"
    assert report.coding_agent_report_id
    assert Path(report.root, "app.py").is_file()
