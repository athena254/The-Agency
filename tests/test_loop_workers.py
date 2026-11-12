"""Tests for loop workers: prompts, external CLI runs, editing, presets."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from agency.autonomy.loop_state import LoopItem
from agency.autonomy.loop_workers import (
    ClaudeCodeWorker,
    CodexWorker,
    CursorWorker,
    EditingWorker,
    ExternalWorker,
    NoopWorker,
    OpencodeWorker,
    PiWorker,
    build_task_prompt,
)

PYTHON = sys.executable


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "seed.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


def _item(item_id: str = "a", **over: Any) -> LoopItem:
    return LoopItem(id=item_id, title=f"task {item_id}", **over)


# ---------------------------------------------------------------------------
# build_task_prompt
# ---------------------------------------------------------------------------


def test_build_task_prompt_includes_acceptance_and_omits_empty_feedback():
    item = _item("a", description="do the thing", acceptance=("tests/test_x.py", "holdout: demo"))
    prompt = build_task_prompt(item, "")
    assert "task a" in prompt
    assert "do the thing" in prompt
    assert "tests/test_x.py" in prompt
    assert "holdout: demo" in prompt
    assert "Previous attempt feedback" not in prompt


def test_build_task_prompt_appends_feedback_when_present():
    item = _item("a", acceptance=("tests/test_x.py",))
    prompt = build_task_prompt(item, "tests failed: test_x")
    assert "Previous attempt feedback" in prompt
    assert "tests failed: test_x" in prompt


# ---------------------------------------------------------------------------
# ExternalWorker
# ---------------------------------------------------------------------------


def test_external_worker_creates_file(tmp_path):
    repo = _git_repo(tmp_path)
    worker = ExternalWorker([PYTHON, "-c", "open('out.txt','w').write('x')"])
    result = worker.run(_item(), repo, "")
    assert result.gate == "PASS"
    assert result.changed_paths == ("out.txt",)
    assert (repo / "out.txt").read_text(encoding="utf-8") == "x"
    assert result.duration_s >= 0.0
    assert result.cost_usd == 0.0


def test_external_worker_no_change_fails(tmp_path):
    repo = _git_repo(tmp_path)
    worker = ExternalWorker([PYTHON, "-c", "pass"])
    result = worker.run(_item(), repo, "")
    assert result.gate == "FAIL"
    assert result.changed_paths == ()


def test_external_worker_nonzero_exit_fails(tmp_path):
    repo = _git_repo(tmp_path)
    worker = ExternalWorker([PYTHON, "-c", "import sys; sys.exit(3)"])
    result = worker.run(_item(), repo, "")
    assert result.gate == "FAIL"
    assert "3" in result.detail


def test_external_worker_timeout_fails_fast(tmp_path):
    repo = _git_repo(tmp_path)
    worker = ExternalWorker([PYTHON, "-c", "import time; time.sleep(30)"], timeout_s=2)
    start = time.monotonic()
    result = worker.run(_item(), repo, "")
    elapsed = time.monotonic() - start
    assert result.gate == "FAIL"
    assert "timeout" in result.detail
    assert elapsed < 10.0


# ---------------------------------------------------------------------------
# EditingWorker
# ---------------------------------------------------------------------------


def test_editing_worker_applies_create_write_patch(tmp_path):
    repo = _git_repo(tmp_path)

    def provider(item, worktree, feedback):
        return [
            {"op": "create_file", "path": "new.txt", "content": "hello\n"},
            {"op": "write_file", "path": "nested/dir/file.txt", "content": "world\n"},
            {"op": "patch", "path": "seed.py", "old": "VALUE = 1", "new": "VALUE = 2"},
        ]

    worker = EditingWorker(provider)
    result = worker.run(_item(), repo, "")
    assert result.gate == "PASS"
    assert result.changed_paths == ("nested/dir/file.txt", "new.txt", "seed.py")
    assert (repo / "new.txt").read_text(encoding="utf-8") == "hello\n"
    assert (repo / "nested" / "dir" / "file.txt").read_text(encoding="utf-8") == "world\n"
    assert (repo / "seed.py").read_text(encoding="utf-8") == "VALUE = 2\n"


def test_editing_worker_patch_requires_single_occurrence(tmp_path):
    repo = _git_repo(tmp_path)
    (repo / "dup.txt").write_text("x\nx\n", encoding="utf-8")

    def provider(item, worktree, feedback):
        return [{"op": "patch", "path": "dup.txt", "old": "x", "new": "y"}]

    worker = EditingWorker(provider)
    with pytest.raises(ValueError):
        worker.run(_item(), repo, "")


def test_editing_worker_create_file_existing_raises(tmp_path):
    repo = _git_repo(tmp_path)

    def provider(item, worktree, feedback):
        return [{"op": "create_file", "path": "seed.py", "content": "nope"}]

    worker = EditingWorker(provider)
    with pytest.raises(ValueError):
        worker.run(_item(), repo, "")


def test_noop_worker_passes_without_changes(tmp_path):
    repo = _git_repo(tmp_path)
    result = NoopWorker().run(_item(), repo, "")
    assert result.gate == "PASS"
    assert result.changed_paths == ()


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------


def test_preset_commands_and_names():
    assert list(OpencodeWorker().command) == ["opencode", "run", "--auto", "--dir", "."]
    assert list(OpencodeWorker(model="gpt-5").command) == [
        "opencode",
        "run",
        "--auto",
        "--dir",
        ".",
        "--model",
        "gpt-5",
    ]
    assert list(ClaudeCodeWorker().command) == ["claude", "-p", "--output-format", "text"]
    assert list(CodexWorker().command) == ["codex", "--full-auto", "-"]
    assert list(PiWorker().command) == ["pi", "--prompt"]
    assert list(CursorWorker().command) == ["cursor-agent", "-p"]
    assert OpencodeWorker().name == "opencode"
    assert ClaudeCodeWorker().name == "claude"
