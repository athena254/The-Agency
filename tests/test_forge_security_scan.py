"""Tests for Forge v2 static security scan."""

from __future__ import annotations

from pathlib import Path

import pytest

from agency.forge import scan_changes

_SUBPROCESS_RULE_IDS = {"SEC007", "SEC008", "SEC009", "SEC010"}


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


def test_subprocess_in_production_raises_high_and_blocks_pass(tmp_path: Path):
    _write(tmp_path, "run.py", "import subprocess\nsubprocess.run(['ls'])\n")
    report = scan_changes(tmp_path, ["run.py"])
    assert report.gate == "FAIL"
    assert any(f.rule_id == "SEC007" and f.severity == "high" for f in report.findings)


def test_all_subprocess_rules_still_flagged_in_production(tmp_path: Path):
    source = (
        "import subprocess\n"
        "subprocess.run(['ls'])\n"
        "subprocess.call(['ls'])\n"
        "subprocess.check_output(['ls'])\n"
        "subprocess.Popen(['ls'])\n"
    )
    _write(tmp_path, "run.py", source)
    report = scan_changes(tmp_path, ["run.py"])
    assert report.gate == "FAIL"
    assert {f.rule_id for f in report.findings} == _SUBPROCESS_RULE_IDS


@pytest.mark.parametrize(
    "name",
    [
        "test_cli.py",
        "cli_test.py",
        "conftest.py",
        "tests/helpers/cli.py",
        "testing/helpers/cli.py",
        "TESTS/helpers/cli.py",
    ],
)
def test_subprocess_in_test_files_raises_nothing(tmp_path: Path, name: str):
    _write(tmp_path, name, "import subprocess\nsubprocess.run(['ls'])\n")
    report = scan_changes(tmp_path, [name])
    assert report.gate == "PASS"
    assert report.findings == ()
    assert report.scanned_files == 1


def test_all_subprocess_rules_suppressed_in_test_file(tmp_path: Path):
    source = (
        "import subprocess\n"
        "subprocess.run(['ls'])\n"
        "subprocess.call(['ls'])\n"
        "subprocess.check_output(['ls'])\n"
        "subprocess.Popen(['ls'])\n"
    )
    _write(tmp_path, "test_cli.py", source)
    report = scan_changes(tmp_path, ["test_cli.py"])
    assert report.gate == "PASS"
    assert report.findings == ()


@pytest.mark.parametrize("name", ["test_cli.py", "cli_test.py", "tests/cli.py"])
def test_test_file_with_eval_still_flagged(tmp_path: Path, name: str):
    source = "import subprocess\nsubprocess.run(['ls'])\nresult = eval(user_input)\n"
    _write(tmp_path, name, source)
    report = scan_changes(tmp_path, [name])
    assert report.gate == "FAIL"
    assert any(f.rule_id == "SEC001" and f.severity == "critical" for f in report.findings)
    assert len(report.findings) == 1
    assert all(f.rule_id not in _SUBPROCESS_RULE_IDS for f in report.findings)


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
