"""Tests for Forge's isolated test runner size accounting."""

from __future__ import annotations

from pathlib import Path

import pytest

from agency.forge import test_runner


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname = 'probe'\n", encoding="utf-8")
    (repo / "test_probe.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    return repo


def test_virtualenv_excluded_from_size_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path)
    venv = repo / ".venv"
    venv.mkdir()
    (venv / "blob.bin").write_bytes(b"x" * 2000)
    monkeypatch.setattr(test_runner, "MAX_REPO_BYTES", 500)

    report = test_runner.run_tests(repo, ["test_probe.py"])

    assert report.gate == "PASS", report.note
    assert "size cap" not in report.note


def test_oversized_source_still_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path)
    (repo / "large.bin").write_bytes(b"x" * 2000)
    monkeypatch.setattr(test_runner, "MAX_REPO_BYTES", 500)

    report = test_runner.run_tests(repo, ["test_probe.py"])

    assert report.gate == "ERROR"
    assert "size cap" in report.note
