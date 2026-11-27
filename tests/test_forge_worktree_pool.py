"""Tests for the bounded Forge worktree pool."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from agency.forge.worktree_pool import WorktreePool


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "pool@local"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "pool"], check=True)
    (repo / "README.md").write_text("pool probe\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-qm", "init"], check=True, capture_output=True
    )
    return repo


def test_acquire_and_release(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    pool = WorktreePool(repo, base_dir=tmp_path / "pool", max_slots=2)
    report = pool.acquire("task-one", changed_paths=["src/a.py"])
    assert report.gate == "PASS"
    assert Path(report.worktree_path).is_dir()
    assert pool.active_slots() == 1
    assert pool.release("task-one") is True
    assert not Path(report.worktree_path).exists()
    assert pool.active_slots() == 0


def test_conflict_and_cap(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    pool = WorktreePool(repo, base_dir=tmp_path / "pool", max_slots=1)
    assert pool.acquire("task-one", changed_paths=["src/a.py"]).gate == "PASS"
    conflict = pool.acquire("task-two", changed_paths=["src/a.py"])
    assert conflict.gate == "BLOCKED"
    assert any(reason.startswith("path_conflict") for reason in conflict.blocked_reasons)
    full = pool.acquire("task-three")
    assert full.gate == "BLOCKED"
    assert "pool_full" in full.blocked_reasons


def test_reap_expired(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    pool = WorktreePool(repo, base_dir=tmp_path / "pool", ttl_s=0)
    report = pool.acquire("task-one")
    assert report.gate == "PASS"
    time.sleep(0.02)
    assert pool.reap() == ("task-one",)
    assert pool.active_slots() == 0


def test_invalid_task_id_is_blocked(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    pool = WorktreePool(repo, base_dir=tmp_path / "pool")
    report = pool.acquire("bad id")
    assert report.gate == "BLOCKED"
    assert "invalid_task_id" in report.blocked_reasons
