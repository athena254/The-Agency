"""Tests for the Forge v1 read-only inspection gate (bounded, non-executing)."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from agency.forge.inspection import (
    CAVEATS,
    MAX_FILE_BYTES,
    MAX_FILES,
    InspectionReport,
    InspectionStore,
    inspect_changes,
)


def _write(root: Path, name: str, data: bytes) -> str:
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return name


def test_pass_valid_python_inventory_and_hash(tmp_path: Path):
    _write(tmp_path, "ok.py", b"x = 1\n")
    _write(tmp_path, "notes.txt", b"hello\n")
    report = inspect_changes(tmp_path, ["ok.py", "notes.txt"])
    assert report.gate == "PASS"
    assert report.rejected == ()
    assert set(CAVEATS) <= set(report.caveats)
    by_path = {f.path: f for f in report.files}
    assert by_path["ok.py"].syntax_ok is True
    assert by_path["ok.py"].security_reviewed is False
    assert by_path["notes.txt"].kind == "other"
    assert by_path["notes.txt"].syntax_ok is None


def test_content_hash_matches_sha256(tmp_path: Path):
    data = b"print('hi')\n"
    _write(tmp_path, "a.py", data)
    report = inspect_changes(tmp_path, ["a.py"])
    assert report.files[0].sha256 == hashlib.sha256(data).hexdigest()
    assert report.files[0].size_bytes == len(data)


def test_syntax_failure_gives_fail_not_crash(tmp_path: Path):
    _write(tmp_path, "bad.py", b"def broken(:\n")
    report = inspect_changes(tmp_path, ["bad.py"])
    assert report.gate == "FAIL"
    assert len(report.files) == 1
    assert report.files[0].syntax_ok is False
    assert report.files[0].syntax_error


def test_bad_utf8_python_source_rejected(tmp_path: Path):
    _write(tmp_path, "bin.py", b"\xff\xfe\x00bad = 1\n")
    report = inspect_changes(tmp_path, ["bin.py"])
    assert report.gate == "FAIL"
    assert report.files == ()
    assert any("bad_utf8" in r.reason for r in report.rejected)


def test_binary_nonpython_hashed_not_security_reviewed(tmp_path: Path):
    _write(tmp_path, "blob.bin", bytes([0, 159, 146, 150, 255, 0, 1]))
    report = inspect_changes(tmp_path, ["blob.bin"])
    assert report.gate == "PASS"
    assert report.files[0].kind == "other"
    assert report.files[0].security_reviewed is False


def test_path_traversal_rejected(tmp_path: Path):
    _write(tmp_path, "ok.py", b"x = 1\n")
    report = inspect_changes(tmp_path, ["../escape.py", "sub/../../evil.py", "/etc/passwd"])
    assert report.gate == "FAIL"
    assert len(report.rejected) == 3
    assert report.files == ()


def test_absolute_windows_path_rejected(tmp_path: Path):
    report = inspect_changes(tmp_path, ["C:\\Windows\\x.py", "C:/evil.py"])
    assert report.gate == "FAIL"
    assert all("absolute" in r.reason for r in report.rejected)


def test_missing_file_rejected(tmp_path: Path):
    report = inspect_changes(tmp_path, ["ghost.py"])
    assert report.gate == "FAIL"
    assert report.rejected[0].reason == "missing_file"


def test_directory_rejected_as_nonregular(tmp_path: Path):
    (tmp_path / "pkg").mkdir()
    report = inspect_changes(tmp_path, ["pkg"])
    assert report.gate == "FAIL"
    assert report.rejected[0].reason == "nonregular_path"


def test_symlink_rejected(tmp_path: Path):
    _write(tmp_path, "real.py", b"x = 1\n")
    link = tmp_path / "link.py"
    try:
        link.symlink_to(tmp_path / "real.py")
    except OSError:
        # Platforms without symlink privilege cannot exercise this path.
        return
    report = inspect_changes(tmp_path, ["link.py"])
    assert report.gate == "FAIL"
    assert any("symlink" in r.reason for r in report.rejected)


def test_symlink_parent_escape_rejected(tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "evil.py").write_bytes(b"x = 1\n")
    inner = tmp_path / "root"
    inner.mkdir()
    link = inner / "leak"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        return
    report = inspect_changes(inner, ["leak/evil.py"])
    assert report.gate == "FAIL"
    assert any("symlink" in r.reason or "escapes" in r.reason for r in report.rejected)


def test_file_cap_enforced(tmp_path: Path):
    names: list[str] = []
    for i in range(MAX_FILES + 1):
        names.append(_write(tmp_path, f"f{i}.txt", b"x"))
    report = inspect_changes(tmp_path, names)
    assert report.gate == "FAIL"
    assert report.files == ()
    assert len(report.rejected) == 1
    assert report.rejected[0].reason == f"file_count_exceeds_limit:{MAX_FILES}"


def test_oversized_file_rejected(tmp_path: Path):
    _write(tmp_path, "big.txt", b"a" * (MAX_FILE_BYTES + 1))
    report = inspect_changes(tmp_path, ["big.txt"])
    assert report.gate == "FAIL"
    assert any("file_too_large" in r.reason for r in report.rejected)


def test_persistence_survives_restart(tmp_path: Path):
    _write(tmp_path, "ok.py", b"x = 1\n")
    report = inspect_changes(tmp_path, ["ok.py"])
    db = tmp_path / "reports.db"
    store = InspectionStore(db)
    store.save(report)
    store.close()
    reopened = InspectionStore(db)
    try:
        loaded = reopened.load(report.report_id)
    finally:
        reopened.close()
    assert loaded is not None
    assert loaded == report
    assert loaded.gate == "PASS"


def test_persisted_document_has_no_source_content(tmp_path: Path):
    marker = b"super_secret_marker_xyz = 1\n"
    _write(tmp_path, "s.py", marker)
    report = inspect_changes(tmp_path, ["s.py"])
    db = tmp_path / "r.db"
    store = InspectionStore(db)
    try:
        store.save(report)
        conn = sqlite3.connect(db)
        try:
            row = conn.execute("SELECT document FROM forge_inspection_reports").fetchone()
        finally:
            conn.close()
    finally:
        store.close()
    assert row is not None
    assert "super_secret_marker_xyz" not in row[0]


def test_no_process_execution_in_feature_source():
    source = (
        Path(__file__).parent.parent / "src" / "agency" / "forge" / "inspection.py"
    ).read_text(encoding="utf-8")
    forbidden = ["subprocess", "os.system", "os.popen", "Popen", "eval(", "exec(", "__import__"]
    for token in forbidden:
        assert token not in source, f"forbidden token in inspection gate: {token}"
    assert "import importlib" not in source


def test_empty_and_unbounded_path_inputs_fail_closed(tmp_path: Path):
    empty = inspect_changes(tmp_path, [])
    assert empty.gate == "FAIL"
    assert isinstance(empty.files, tuple)
    assert isinstance(empty.caveats, tuple)
    assert empty.rejected[0].reason == "empty_change_set"
    huge = inspect_changes(tmp_path, ["a.py"] * 10000)
    assert huge.gate == "FAIL"
    assert len(huge.rejected) == 1
    assert huge.rejected[0].path == "<batch>"
    long_path = inspect_changes(tmp_path, ["x" * 10000])
    assert long_path.gate == "FAIL"
    assert len(long_path.rejected[0].path) <= 512


def test_report_carries_bounded_gate_caveats(tmp_path: Path):
    _write(tmp_path, "ok.py", b"x = 1\n")
    report = inspect_changes(tmp_path, ["ok.py"])
    assert isinstance(report, InspectionReport)
    assert report.gate == "PASS"
    assert "tests_not_run" in report.caveats
    assert "security_review_not_run" in report.caveats
    assert "release_not_authorized" in report.caveats
    assert "merge" in report.note.lower() or "deploy" in report.note.lower()
