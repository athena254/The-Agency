"""Legacy absorber remains independent and never commits by default."""
from __future__ import annotations

import subprocess

import pytest

from agency.addons.dark_factory.absorption import RepoAbsorber
from agency.addons.dark_factory.factory import DarkFactory


def test_absorb_never_commits_without_explicit_opt_in(tmp_path, monkeypatch):
    repo = tmp_path / "sample"
    repo.mkdir()
    (repo / "sample.py").write_text("value = 1\n", encoding="utf-8")
    absorber = RepoAbsorber(workspace=tmp_path / "clones")
    monkeypatch.setattr(absorber, "clone_repo", lambda url: repo)
    committed = []
    monkeypatch.setattr(
        absorber, "commit_changes", lambda path, paths: committed.append((path, paths)) or "abc123"
    )

    result = absorber.absorb("https://github.com/example/sample")
    assert result.validation_passed
    assert not result.committed
    assert committed == []
    assert "Agency module" in (repo / "sample.py").read_text(encoding="utf-8")
    assert "Athena-native" not in (repo / "sample.py").read_text(encoding="utf-8")

    (repo / "second.py").write_text("value = 2\n", encoding="utf-8")
    result = absorber.absorb("https://github.com/example/sample", commit=True)
    assert result.committed
    assert committed == [(repo, ["second.py"])]


def test_dark_factory_does_not_request_commit_by_default(tmp_path, monkeypatch):
    absorber = RepoAbsorber(workspace=tmp_path)
    received = []
    monkeypatch.setattr(absorber, "absorb", lambda url, *, commit: received.append(commit) or "ok")
    factory = DarkFactory(absorber=absorber)
    assert factory.absorb_repo("https://github.com/example/sample") == "ok"
    assert received == [False]


def test_opt_in_does_not_commit_without_rewrites(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    absorber = RepoAbsorber(tmp_path / "workspace")
    monkeypatch.setattr(absorber, "clone_repo", lambda url: repo)
    monkeypatch.setattr(absorber, "commit_changes", lambda *args: pytest.fail("unexpected commit"))
    result = absorber.absorb("https://github.com/example/repo", commit=True)
    assert not result.committed
    assert result.details["sha"] is None


def test_rewrite_does_not_follow_symlink_file_or_directory(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    source = external / "external.py"
    source.write_text("outside = 1\n", encoding="utf-8")
    try:
        (repo / "linked.py").symlink_to(source)
        (repo / "linked_dir").symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    result = RepoAbsorber(tmp_path / "workspace").rewrite_to_agency(repo)
    assert result["count"] == 0
    assert source.read_text(encoding="utf-8") == "outside = 1\n"
    assert "linked.py" in result["skipped"]


def test_validation_rejects_symlink_and_unreadable_file(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "ok.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "bad.py").write_text("x = 2\n", encoding="utf-8")
    original_open = __import__("os").open

    def deny_bad(path, flags, *args, **kwargs):
        if str(path) == str(repo / "bad.py"):
            raise PermissionError("denied")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr("agency.addons.dark_factory.absorption.os.open", deny_bad)
    report = RepoAbsorber(tmp_path / "workspace").validate(repo)
    assert not report["passed"]
    assert str(repo / "bad.py") in report["failures"]


def test_validation_rejects_symlinked_source(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    external = tmp_path / "external.py"
    external.write_text("value = 1\n", encoding="utf-8")
    try:
        (repo / "link.py").symlink_to(external)
    except OSError:
        pytest.skip("symlinks unavailable")
    report = RepoAbsorber(tmp_path / "workspace").validate(repo)
    assert report["checked"] == 1
    assert not report["passed"]
    assert str(repo / "link.py") in report["failures"]


def test_commit_only_rewritten_files_leaves_unrelated_changes_staged(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()

    git("init")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    (repo / "code.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "unrelated.txt").write_text("original\n", encoding="utf-8")
    git("add", "--", "code.py", "unrelated.txt")
    git("commit", "-m", "initial")
    (repo / "code.py").write_text("x = 2\n", encoding="utf-8")
    (repo / "unrelated.txt").write_text("staged\n", encoding="utf-8")
    git("add", "--", "unrelated.txt")
    (repo / "extra.txt").write_text("untracked\n", encoding="utf-8")

    sha = RepoAbsorber(tmp_path / "workspace").commit_changes(repo, ["code.py"])
    assert sha == git("rev-parse", "HEAD")
    assert git("show", "--format=", "--name-only", "HEAD") == "code.py"
    assert git("diff", "--cached", "--name-only") == "unrelated.txt"
    assert (repo / "extra.txt").exists()
