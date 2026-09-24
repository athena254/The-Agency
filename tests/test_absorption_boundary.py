"""Legacy absorber remains independent and never commits by default."""
from __future__ import annotations

from agency.addons.dark_factory.absorption import RepoAbsorber
from agency.addons.dark_factory.factory import DarkFactory


def test_absorb_never_commits_without_explicit_opt_in(tmp_path, monkeypatch):
    repo = tmp_path / "sample"
    repo.mkdir()
    (repo / "sample.py").write_text("value = 1\n", encoding="utf-8")
    absorber = RepoAbsorber(workspace=tmp_path / "clones")
    monkeypatch.setattr(absorber, "clone_repo", lambda url: repo)
    committed = []
    monkeypatch.setattr(absorber, "commit_changes", lambda path: committed.append(path) or "abc123")

    result = absorber.absorb("https://github.com/example/sample")
    assert result.validation_passed
    assert not result.committed
    assert committed == []
    assert "Agency module" in (repo / "sample.py").read_text(encoding="utf-8")
    assert "Athena-native" not in (repo / "sample.py").read_text(encoding="utf-8")

    result = absorber.absorb("https://github.com/example/sample", commit=True)
    assert result.committed
    assert committed == [repo]


def test_dark_factory_does_not_request_commit_by_default(tmp_path, monkeypatch):
    absorber = RepoAbsorber(workspace=tmp_path)
    received = []
    monkeypatch.setattr(absorber, "absorb", lambda url, *, commit: received.append(commit) or "ok")
    factory = DarkFactory(absorber=absorber)
    assert factory.absorb_repo("https://github.com/example/sample") == "ok"
    assert received == [False]
