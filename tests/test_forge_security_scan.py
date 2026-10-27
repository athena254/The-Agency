"""Tests for Forge v2 static security scan."""

from __future__ import annotations

from pathlib import Path

from agency.forge import scan_changes


def _write(root: Path, name: str, data: str) -> str:
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(data, encoding="utf-8")
    return name


def test_clean_python_passes(tmp_path: Path):
    _write(tmp_path, "ok.py", "x = 1\n")
    report = scan_changes(tmp_path, ["ok.py"])
    assert report.gate == "PASS"
    assert report.findings == ()
    assert report.scanned_files == 1


def test_eval_flagged_critical(tmp_path: Path):
    _write(tmp_path, "bad.py", "result = eval(user_input)\n")
    report = scan_changes(tmp_path, ["bad.py"])
    assert report.gate == "FAIL"
    assert any(f.rule_id == "SEC001" for f in report.findings)
    assert any(f.severity == "critical" for f in report.findings)


def test_hardcoded_secret_flagged(tmp_path: Path):
    _write(tmp_path, "cfg.py", 'API_KEY = "supersecretvalue1234"\n')
    report = scan_changes(tmp_path, ["cfg.py"])
    assert report.gate == "FAIL"
    assert any("SEC100" in f.rule_id for f in report.findings)


def test_subprocess_flagged(tmp_path: Path):
    _write(tmp_path, "run.py", "import subprocess\nsubprocess.run(['ls'])\n")
    report = scan_changes(tmp_path, ["run.py"])
    assert report.gate == "FAIL"
    assert any("SEC007" in f.rule_id for f in report.findings)


def test_path_traversal_rejected(tmp_path: Path):
    _write(tmp_path, "ok.py", "x = 1\n")
    report = scan_changes(tmp_path, ["../escape.py"])
    assert report.skipped_files >= 1


def test_empty_change_set_fails(tmp_path: Path):
    report = scan_changes(tmp_path, [])
    assert report.gate == "FAIL"


def test_secret_in_nonpython_flagged(tmp_path: Path):
    _write(tmp_path, "config.env", 'PASSWORD = "hunter2hunter2"\n')
    report = scan_changes(tmp_path, ["config.env"])
    assert report.gate == "FAIL"


def test_persistence_not_required_here(tmp_path: Path):
    _write(tmp_path, "a.py", "print('hi')\n")
    r = scan_changes(tmp_path, ["a.py"])
    assert r.scanned_files == 1
