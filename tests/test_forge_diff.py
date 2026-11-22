"""Tests for the bounded, risk-ranked diff bundle (forge.diff)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agency.forge.diff import (
    BUNDLE_EXHAUSTED_MARK,
    FILE_TRUNCATION_MARK,
    MAX_PER_FILE_BYTES,
    build_diff_bundle,
    delta_paths,
    head_sha,
)


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (
        ["git", "init", str(repo)],
        ["git", "-C", str(repo), "config", "user.email", "t@t.com"],
        ["git", "-C", str(repo), "config", "user.name", "t"],
    ):
        subprocess.run(args, check=True, capture_output=True)
    (repo / "base.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True
    )
    return repo


def test_head_sha_returns_commit_and_delta_paths(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    base = head_sha(repo)
    assert base
    (repo / "new.py").write_text("NEW = 2\n", encoding="utf-8")
    (repo / "base.py").write_text("VALUE = 3\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "change"], check=True, capture_output=True
    )
    delta = delta_paths(repo, base)
    assert delta == ("base.py", "new.py")
    assert delta_paths(repo, "deadbeef" * 5) is None
    assert delta_paths(repo, "") is None


def test_bundle_carries_tracked_and_untracked_content(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (repo / "base.py").write_text("VALUE = 42\n", encoding="utf-8")
    (repo / "added.py").write_text("ADDED = True\n", encoding="utf-8")
    bundle = build_diff_bundle(repo, ["base.py", "added.py"])
    assert "+VALUE = 42" in bundle.text
    assert "ADDED = True" in bundle.text
    assert bundle.included_paths == ("added.py", "base.py")
    assert bundle.omitted_paths == ()
    assert bundle.truncated_paths == ()


def test_bundle_per_file_truncation_records_the_path(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    big = "x = 1\n" * (MAX_PER_FILE_BYTES // 4)
    (repo / "big.py").write_text(big, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    bundle = build_diff_bundle(repo, ["big.py"])
    assert bundle.truncated_paths == ("big.py",)
    assert FILE_TRUNCATION_MARK in bundle.text


def test_bundle_respects_total_budget(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    for i in range(12):
        (repo / f"file_{i}.py").write_text(f"line = {i}\n" + "pad\n" * 900, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    bundle = build_diff_bundle(repo, [f"file_{i}.py" for i in range(12)])
    assert bundle.total_bytes <= 60_000
    assert bundle.omitted_paths
    assert BUNDLE_EXHAUSTED_MARK in bundle.text
    assert all(p in bundle.text for p in bundle.included_paths)


def test_bundle_prioritizes_protected_paths(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    # Many large safe files plus one small protected path: the protected path
    # must be included even when the bulk is omitted.
    for i in range(40):
        (repo / f"pad_{i}.py").write_text("pad\n" * 1400, encoding="utf-8")
    (repo / "secrets").mkdir()
    (repo / "secrets" / "key.txt").write_text("SECRET = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    paths = [f"pad_{i}.py" for i in range(40)] + ["secrets/key.txt"]
    bundle = build_diff_bundle(repo, paths)
    assert "secrets/key.txt" in bundle.included_paths
    assert "+SECRET = 1" in bundle.text
    assert bundle.included_paths[0] == "secrets/key.txt"


def test_bundle_empty_change_set(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    bundle = build_diff_bundle(repo, [])
    assert bundle.text == ""
    assert bundle.total_bytes == 0


@pytest.mark.parametrize("bad", ["..", "/abs/path"])
def test_bundle_ignores_unresolvable_paths(tmp_path: Path, bad: str) -> None:
    repo = _git_repo(tmp_path)
    bundle = build_diff_bundle(repo, [bad])
    assert bundle.text == ""
