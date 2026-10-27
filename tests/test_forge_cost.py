from __future__ import annotations

from pathlib import Path

from agency.forge import CostStore, record_cost
from agency.forge.cost import CostEntry


def test_record_cost(tmp_path: Path):
    db = tmp_path / "c.db"
    store = CostStore(db)
    report = record_cost(
        store,
        run_id="run1",
        model="gpt-4",
        prompt_tokens=10,
        completion_tokens=5,
        cost=0.01,
    )
    assert report.total_tokens == 15
    assert abs(report.total_cost - 0.01) < 1e-9
    assert len(report.entries) == 1
    assert report.entries[0].model == "gpt-4"
    assert isinstance(report.entries[0], CostEntry)
    store.close()


def test_aggregate_totals(tmp_path: Path):
    db = tmp_path / "c.db"
    store = CostStore(db)
    record_cost(store, run_id="r1", model="gpt-4", prompt_tokens=10, completion_tokens=5, cost=0.01)
    record_cost(
        store, run_id="r2", model="gpt-3.5", prompt_tokens=4, completion_tokens=2, cost=0.002
    )
    report = store.aggregate()
    assert report.total_tokens == 15 + 6
    assert abs(report.total_cost - 0.012) < 1e-9
    assert "gpt-4" in report.by_model
    store.close()


def test_cost_persistence(tmp_path: Path):
    db = tmp_path / "c.db"
    store = CostStore(db)
    record_cost(store, run_id="r1", model="m", prompt_tokens=1, completion_tokens=1, cost=0.5)
    store.close()
    reopened = CostStore(db)
    try:
        entries = reopened.list_entries()
    finally:
        reopened.close()
    assert len(entries) == 1
    assert entries[0].total_tokens == 2


def test_record_cost_db_path(tmp_path: Path):
    db = tmp_path / "c2.db"
    report = record_cost(db, run_id="r", model="m", total_tokens=7, cost=1.0)
    assert report.total_tokens == 7
    assert report.total_cost == 1.0
