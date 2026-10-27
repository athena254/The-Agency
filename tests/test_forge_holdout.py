"""Tests for Forge v3 holdout runner."""

from __future__ import annotations

import sys
from pathlib import Path

from agency.forge import HoldoutStore, run_holdout
from agency.forge.holdout import CAVEATS, HoldoutScenario


def _write(root: Path, name: str, data: str) -> str:
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(data, encoding="utf-8")
    return name


def test_holdout_all_pass(tmp_path: Path):
    _write(tmp_path, "ok.py", "x = 1\n")
    scenarios = [
        {"name": "t1", "command": [sys.executable, "-c", "print('ok')"]},
        HoldoutScenario(name="t2", command=(sys.executable, "-c", "import sys; sys.exit(0)")),
    ]
    report = run_holdout(tmp_path, scenarios)
    assert report.gate == "PASS"
    assert all(s.gate == "PASS" for s in report.scenarios)
    assert set(CAVEATS) <= set(report.caveats)


def test_holdout_fail_on_nonzero(tmp_path: Path):
    _write(tmp_path, "ok.py", "x = 1\n")
    report = run_holdout(
        tmp_path, [{"name": "bad", "command": [sys.executable, "-c", "raise SystemExit(1)"]}]
    )
    assert report.gate == "FAIL"
    assert report.scenarios[0].gate == "FAIL"


def test_holdout_timeout(tmp_path: Path):
    _write(tmp_path, "ok.py", "x = 1\n")
    report = run_holdout(
        tmp_path,
        [{"name": "slow", "command": [sys.executable, "-c", "import time; time.sleep(10)"]}],
        timeout_s=1,
    )
    assert report.gate == "FAIL"
    assert report.scenarios[0].gate == "TIMEOUT"


def test_holdout_persistence(tmp_path: Path):
    _write(tmp_path, "ok.py", "x = 1\n")
    report = run_holdout(
        tmp_path, [{"name": "t", "command": [sys.executable, "-c", "print('ok')"]}]
    )
    db = tmp_path / "h.db"
    store = HoldoutStore(db)
    store.save(report)
    store.close()
    reopened = HoldoutStore(db)
    try:
        loaded = reopened.load(report.report_id)
    finally:
        reopened.close()
    assert loaded is not None
    assert loaded.gate == report.gate


def test_holdout_tuple_scenario(tmp_path: Path):
    _write(tmp_path, "ok.py", "x = 1\n")
    report = run_holdout(tmp_path, [("named", sys.executable, "-c", "print('ok')")])
    assert report.gate == "PASS"
    assert report.scenarios[0].name == "named"
