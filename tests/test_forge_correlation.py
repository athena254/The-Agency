"""Tests for task correlation and idempotent evidence."""

from __future__ import annotations

from pathlib import Path

import pytest

from agency.forge.correlation import CorrelationStore, correlate, make_key


def test_correlate_stamps_task_id_and_key() -> None:
    stamped = correlate(
        {"gate": "PASS", "score": 5},
        task_id="task-1",
        transition="review",
        source_sha="abc123",
    )
    assert stamped["gate"] == "PASS"
    assert stamped["task_id"] == "task-1"
    assert stamped["idempotency_key"] == make_key("task-1", "review", "abc123")


def test_correlate_requires_identity() -> None:
    with pytest.raises(ValueError):
        correlate({}, task_id="", transition="review")
    with pytest.raises(ValueError):
        correlate({}, task_id="task-1", transition="")


def test_store_is_append_only_and_idempotent(tmp_path: Path) -> None:
    store = CorrelationStore(tmp_path / "evidence.jsonl")
    first = store.append(task_id="task-1", transition="queued", payload={"task": "x"})
    assert first is not None
    assert store.append(task_id="task-1", transition="queued", payload={"task": "x"}) is None
    second = store.append(task_id="task-1", transition="review", source_sha="abc")
    assert second is not None
    assert len(store.events()) == 2
    assert store.events()[0].key == first.key


def test_store_reloads_keys_from_disk(tmp_path: Path) -> None:
    path = tmp_path / "evidence.jsonl"
    store = CorrelationStore(path)
    store.append(task_id="task-1", transition="queued")
    reopened = CorrelationStore(path)
    assert reopened.append(task_id="task-1", transition="queued") is None
