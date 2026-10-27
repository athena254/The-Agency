"""Tests for Forge v2 pipeline."""

from __future__ import annotations

from pathlib import Path

from agency.forge import run_pipeline


def _write(root: Path, name: str, data: str) -> str:
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(data, encoding="utf-8")
    return name


def test_pipeline_pass(tmp_path: Path):
    _write(tmp_path, "ok.py", "x = 1\n")
    report = run_pipeline(tmp_path, ["ok.py"])
    assert report.gate == "PASS"
    assert report.inspection.gate == "PASS"
    assert report.security.gate == "PASS"
    assert report.tests is None


def test_pipeline_fail_on_syntax(tmp_path: Path):
    _write(tmp_path, "bad.py", "def broken(:\n")
    report = run_pipeline(tmp_path, ["bad.py"])
    assert report.gate == "FAIL"


def test_pipeline_with_tests(tmp_path: Path):
    _write(tmp_path, "ok.py", "x = 1\n")
    # create a trivial test file
    (tmp_path / "test_ok.py").write_text(
        "def test_one():\n    assert 1 + 1 == 2\n", encoding="utf-8"
    )
    report = run_pipeline(tmp_path, ["ok.py"], run_tests_flag=True, test_paths=["test_ok.py"])
    assert report.gate == "PASS"
    assert report.tests is not None
    assert report.tests.gate == "PASS"
    assert "tests_not_run" not in report.caveats


def test_pipeline_persistence(tmp_path: Path):
    from agency.forge.pipeline import ForgePipelineStore

    _write(tmp_path, "ok.py", "x = 1\n")
    report = run_pipeline(tmp_path, ["ok.py"])
    db = tmp_path / "p.db"
    store = ForgePipelineStore(db)
    store.save(report)
    store.close()
    reopened = ForgePipelineStore(db)
    try:
        loaded = reopened.load(report.report_id)
    finally:
        reopened.close()
    assert loaded is not None
    assert loaded.gate == report.gate
