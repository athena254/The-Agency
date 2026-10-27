"""Tests for Forge v3 evidence capture."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from agency.forge import EvidenceStore, capture_evidence
from agency.forge.evidence import CAVEATS


def _write(root: Path, name: str, data: str) -> str:
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(data, encoding="utf-8")
    return name


def test_evidence_capture_no_command(tmp_path: Path):
    _write(tmp_path, "a.py", "x = 1\n")
    report = capture_evidence(tmp_path, ["a.py"])
    assert report.gate == "PASS"
    assert "a.py" in report.before
    assert report.before["a.py"] == report.after["a.py"]
    assert set(CAVEATS) <= set(report.caveats)


def test_evidence_hash_matches(tmp_path: Path):
    data = "print('hi')\n"
    _write(tmp_path, "a.py", data)
    report = capture_evidence(tmp_path, ["a.py"])
    assert report.before["a.py"] == hashlib.sha256((tmp_path / "a.py").read_bytes()).hexdigest()


def test_evidence_command_runs_in_copy(tmp_path: Path):
    _write(tmp_path, "a.py", "x = 1\n")
    report = capture_evidence(tmp_path, ["a.py"], command=[sys.executable, "-c", "print('hello')"])
    assert report.gate == "PASS"
    assert report.returncode == 0
    assert "hello" in report.stdout
    # original unchanged
    assert (tmp_path / "a.py").read_text() == "x = 1\n"


def test_evidence_persistence(tmp_path: Path):
    _write(tmp_path, "a.py", "x = 1\n")
    report = capture_evidence(tmp_path, ["a.py"])
    db = tmp_path / "e.db"
    store = EvidenceStore(db)
    store.save(report)
    store.close()
    reopened = EvidenceStore(db)
    try:
        loaded = reopened.load(report.report_id)
    finally:
        reopened.close()
    assert loaded is not None
    assert loaded.gate == report.gate


def test_evidence_before_after_aliases(tmp_path: Path):
    _write(tmp_path, "a.py", "x = 1\n")
    report = capture_evidence(tmp_path, ["a.py"])
    assert report.before_hashes == report.before
    assert report.after_hashes == report.after
