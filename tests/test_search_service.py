"""Owner-scoped search service tests.

The search package is read-only over the durable owner-pilot store. These
tests cover ranking order, snippet bounds, result limits, principal
isolation and the index builder's owner scoping.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agency.admission.models import OwnerPrincipal, PilotError
from agency.search.index import IndexBuilder, IndexEntry, SearchIndex
from agency.search.ranking import SNIPPET_LIMIT, rank_documents, recency_decay, snippet
from agency.search.service import DEFAULT_LIMIT, MAX_LIMIT, search, search_many

OWNER = OwnerPrincipal("owner", "local", "owner")
OTHER = OwnerPrincipal("intruder", "local", "intruder")

NEWEST = 1_000_000.0


def _entry(
    document_id: str,
    kind: str,
    text: str,
    updated_at: float,
    *,
    thread_id: str | None = None,
    owner_id: str = "owner",
) -> IndexEntry:
    return IndexEntry(
        document_id=document_id,
        kind=kind,  # type: ignore[arg-type]
        thread_id=thread_id or document_id,
        text=text,
        updated_at=updated_at,
        owner_id=owner_id,
    )


def _index(entries: list[IndexEntry], *, owner_id: str = "owner") -> SearchIndex:
    return SearchIndex(entries=tuple(entries), built_at=NEWEST, owner_id=owner_id)


def test_ranking_prefers_match_quality_then_recency() -> None:
    entries = [
        _entry("repeat", "message", "alpha alpha alpha", NEWEST - 100),
        _entry("once", "message", "alpha", NEWEST),
        _entry("stale", "message", "alpha", NEWEST - 10_000),
    ]
    response = search(_index(entries), OWNER, "alpha", now=NEWEST)
    assert [hit.document_id for hit in response.hits] == ["repeat", "once", "stale"]


def test_ranking_uses_document_kind_prior_at_equal_quality() -> None:
    entries = [
        _entry("msg", "message", "alpha", NEWEST),
        _entry("task", "task", "alpha", NEWEST),
        _entry("thread", "thread", "alpha", NEWEST),
    ]
    response = search(_index(entries), OWNER, "alpha", now=NEWEST)
    assert [hit.document_id for hit in response.hits] == ["thread", "task", "msg"]


def test_rank_documents_reports_total_beyond_the_limit() -> None:
    rows = [
        (f"id{index}", "message", "alpha", NEWEST - index, f"id{index}")
        for index in range(5)
    ]
    ranked, total = rank_documents(rows, ["alpha"], limit=2)
    assert total == 5
    assert len(ranked) == 2


def test_limit_truncates_hits_and_marks_truncated() -> None:
    entries = [
        _entry(f"id{index}", "message", "alpha", NEWEST - index) for index in range(5)
    ]
    response = search(_index(entries), OWNER, "alpha", now=NEWEST, limit=2)
    assert len(response.hits) == 2
    assert response.total == 5
    assert response.truncated is True
    assert [hit.rank for hit in response.hits] == [0, 1]


def test_limit_is_bounded_and_validated() -> None:
    entries = [_entry("id", "message", "alpha", NEWEST)]
    index = _index(entries)
    with pytest.raises(PilotError):
        search(index, OWNER, "alpha", now=NEWEST, limit=0)
    with pytest.raises(PilotError):
        search(index, OWNER, "alpha", now=NEWEST, limit=MAX_LIMIT + 1)
    with pytest.raises(PilotError):
        search(index, OWNER, "alpha", now=NEWEST, limit=True)  # type: ignore[arg-type]
    assert search(index, OWNER, "alpha", now=NEWEST, limit=MAX_LIMIT).hits


def test_default_limit_is_applied_when_omitted() -> None:
    entries = [
        _entry(f"id{index:03d}", "message", "alpha", NEWEST - index) for index in range(DEFAULT_LIMIT + 5)
    ]
    response = search(_index(entries), OWNER, "alpha", now=NEWEST)
    assert len(response.hits) == DEFAULT_LIMIT
    assert response.total == DEFAULT_LIMIT + 5


def test_snippet_is_bounded_and_marks_elision() -> None:
    text = "hello world " * 60
    result = snippet(text, ["world"])
    assert len(result) <= SNIPPET_LIMIT
    assert result.endswith("…")
    assert "world" in result


def test_snippet_returns_short_text_unchanged() -> None:
    assert snippet("short text", ["short"]) == "short text"


def test_empty_and_stop_only_queries_return_no_hits() -> None:
    entries = [_entry("id", "message", "alpha", NEWEST)]
    index = _index(entries)
    for query in ("", "   ", "the and"):
        response = search(index, OWNER, query, now=NEWEST)
        assert response.hits == ()
        assert response.total == 0


def test_principal_cannot_search_a_foreign_index() -> None:
    index = _index([_entry("id", "message", "alpha", NEWEST)], owner_id="owner")
    with pytest.raises(PilotError) as caught:
        search(index, OTHER, "alpha", now=NEWEST)
    assert caught.value.code == "not_found"


def test_non_owner_principal_type_is_rejected() -> None:
    index = _index([_entry("id", "message", "alpha", NEWEST)])
    with pytest.raises(PilotError):
        search(index, "not-a-principal", "alpha", now=NEWEST)  # type: ignore[arg-type]


def test_non_finite_now_is_rejected() -> None:
    index = _index([_entry("id", "message", "alpha", NEWEST)])
    with pytest.raises(PilotError):
        search(index, OWNER, "alpha", now=float("nan"))


def test_search_many_validates_its_inputs() -> None:
    index = _index([_entry("id", "message", "alpha", NEWEST)])
    with pytest.raises(PilotError):
        search_many(index, OWNER, "alpha")  # type: ignore[arg-type]
    with pytest.raises(PilotError):
        search_many(index, OWNER, [1, 2])  # type: ignore[list-item]
    assert len(search_many(index, OWNER, ["alpha", "beta"])) == 2


def test_recency_decay_bounds() -> None:
    assert recency_decay(NEWEST, NEWEST) == 1.0
    assert recency_decay(float("nan"), NEWEST) == 0.0
    assert recency_decay(NEWEST, float("inf")) == 0.0
    half_life = NEWEST - (7 * 24 * 3600.0)
    assert 0.49 < recency_decay(half_life, NEWEST) < 0.51


_SCHEMA = """
CREATE TABLE threads (id TEXT PRIMARY KEY, title TEXT, updated_at TEXT, owner TEXT);
CREATE TABLE thread_messages (
    id TEXT PRIMARY KEY, thread_id TEXT, role TEXT, content TEXT, created_at TEXT
);
CREATE TABLE pilot_requests (
    sequence INTEGER PRIMARY KEY, task_id TEXT, thread_id TEXT,
    created_at TEXT, updated_at TEXT, input_json TEXT, result_json TEXT,
    operation TEXT
);
"""


def _store_db(path: Path) -> Path:
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    conn.execute(
        "INSERT INTO threads VALUES (?,?,?,?)",
        ("t1", "alpha title", "2026-01-01T00:00:00+00:00", "owner"),
    )
    conn.execute(
        "INSERT INTO threads VALUES (?,?,?,?)",
        ("t2", "other owner thread", "2026-01-01T00:00:00+00:00", "someone_else"),
    )
    conn.execute(
        "INSERT INTO thread_messages VALUES (?,?,?,?,?)",
        ("m1", "t1", "user", "alpha body", "2026-01-01T00:00:00+00:00"),
    )
    conn.execute(
        "INSERT INTO pilot_requests VALUES (?,?,?,?,?,?,?,?)",
        (
            1,
            "task1",
            "t1",
            "2026-01-01T00:00:00+00:00",
            "2026-01-01T00:00:01+00:00",
            '{"message": "alpha task"}',
            '{"response": "alpha result"}',
            "turn",
        ),
    )
    conn.commit()
    conn.close()
    return path


def test_index_builder_is_owner_scoped(tmp_path: Path) -> None:
    index = IndexBuilder(str(_store_db(tmp_path / "store.db"))).build(OWNER)
    assert index.owner_id == "owner"
    assert {entry.kind for entry in index.entries} == {"thread", "message", "task"}
    assert all(entry.owner_id == "owner" for entry in index.entries)
    assert all("other owner" not in entry.text for entry in index.entries)


def test_index_builder_missing_database_is_not_found(tmp_path: Path) -> None:
    with pytest.raises(PilotError) as caught:
        IndexBuilder(str(tmp_path / "missing.db")).build(OWNER)
    assert caught.value.code == "not_found"


def test_index_builder_rejects_a_foreign_principal(tmp_path: Path) -> None:
    with pytest.raises(PilotError):
        IndexBuilder(str(_store_db(tmp_path / "store.db"))).build("intruder")  # type: ignore[arg-type]
