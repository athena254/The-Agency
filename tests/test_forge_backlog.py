from __future__ import annotations

import hashlib
from pathlib import Path

from agency.forge import BacklogStore, slice_prd
from agency.forge.backlog import CAVEATS, MAX_ITEMS


def test_slice_by_headings(tmp_path: Path):
    prd = "# First\nbody one\n## Second\nbody two\n"
    report = slice_prd(prd)
    assert report.gate == "PASS"
    assert len(report.items) == 2
    assert report.items[0].title == "First"
    assert report.items[0].order == 1
    assert "body one" in report.items[0].body
    assert report.items[1].title == "Second"
    assert report.items[1].order == 2
    assert set(CAVEATS) <= set(report.caveats)


def test_slice_no_headings(tmp_path: Path):
    report = slice_prd("just text\nmore")
    assert len(report.items) == 1
    assert report.items[0].title == "just text"


def test_slice_cap(tmp_path: Path):
    prd = "\n".join(f"# H{i}\nbody" for i in range(MAX_ITEMS + 5))
    report = slice_prd(prd)
    assert len(report.items) == MAX_ITEMS
    assert report.gate == "FAIL"


def test_backlog_persistence(tmp_path: Path):
    report = slice_prd("# A\nb")
    db = tmp_path / "b.db"
    store = BacklogStore(db)
    store.save(report)
    store.close()
    reopened = BacklogStore(db)
    try:
        loaded = reopened.load(report.report_id)
    finally:
        reopened.close()
    assert loaded is not None
    assert loaded.items[0].title == "A"


def test_backlog_hash():
    report = slice_prd("# A\nb")
    assert report.prd_hash == hashlib.sha256(b"# A\nb").hexdigest()
