"""Tests for Forge Prove: multi-language runner + reviewer gate."""

from __future__ import annotations

import json
from pathlib import Path

from agency.forge import detect_language, run_pipeline, run_tests


def _write(root: Path, name: str, data: str) -> None:
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(data, encoding="utf-8")


def test_detect_language(tmp_path: Path):
    assert detect_language(tmp_path) == "python"
    _write(tmp_path, "package.json", "{}")
    assert detect_language(tmp_path) == "node"
    (tmp_path / "package.json").unlink()
    _write(tmp_path, "go.mod", "module example\n\ngo 1.22\n")
    assert detect_language(tmp_path) == "go"
    (tmp_path / "go.mod").unlink()
    _write(tmp_path, "Cargo.toml", '[package]\nname = "x"\n')
    assert detect_language(tmp_path) == "rust"


def test_python_pass(tmp_path: Path):
    _write(tmp_path, "pyproject.toml", '[project]\nname = "x"\nversion = "0.1.0"\n')
    _write(tmp_path, "test_ok.py", "def test_one():\n    assert 1 + 1 == 2\n")
    report = run_tests(tmp_path, ["test_ok.py"])
    assert report.language == "python"
    assert report.gate == "PASS"
    assert report.command[1] == "-m"


def test_node_skip_or_pass(tmp_path: Path):
    _write(
        tmp_path,
        "package.json",
        json.dumps({"name": "x", "scripts": {"test": 'node -e "process.exit(0)"'}}),
    )
    report = run_tests(tmp_path, [])
    assert report.language == "node"
    assert report.gate in ("PASS", "SKIP")


def test_missing_tool_is_skip_not_fail(tmp_path: Path, monkeypatch):
    _write(tmp_path, "go.mod", "module example\n\ngo 1.22\n")
    monkeypatch.setattr("agency.forge.test_runner.shutil.which", lambda _name: None)
    report = run_tests(tmp_path, [])
    assert report.language == "go"
    assert report.gate == "SKIP"


def test_reviewer_gate_blocks_pipeline(tmp_path: Path):
    _write(tmp_path, "bad.py", "result = eval(user_input)\n")
    report = run_pipeline(tmp_path, ["bad.py"])
    assert report.review is not None
    assert report.review.gate == "FAIL"
    assert report.gate == "FAIL"


def test_reviewer_gate_passes_clean_pipeline(tmp_path: Path):
    _write(tmp_path, "ok.py", "x = 1\n")
    _write(tmp_path, "test_ok.py", "def test_one():\n    assert 1 + 1 == 2\n")
    report = run_pipeline(tmp_path, ["ok.py"], run_tests_flag=True, test_paths=["test_ok.py"])
    assert report.review is not None
    assert report.review.gate == "PASS"
    assert report.gate == "PASS"
