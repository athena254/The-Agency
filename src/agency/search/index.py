"""Read-only index builder over the durable owner-pilot store.

The builder opens the owner SQLite database itself and only ever issues
SELECTs. It never writes, never initializes the schema, and never holds
the store's lifecycle lock, so a search can run while the owner pilot is
live. Every row is filtered by ``owner_id`` so one owner's index can
never contain another owner's threads, messages or tasks.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from agency.admission.models import OwnerPrincipal, PilotError
from agency.search.tokenizer import split_query

DocumentKind = Literal["thread", "message", "task"]


@dataclass(frozen=True)
class IndexEntry:
    """One indexed document before ranking."""

    document_id: str
    kind: DocumentKind
    thread_id: str
    text: str
    updated_at: float
    owner_id: str


@dataclass(frozen=True)
class SearchIndex:
    """A materialized, owner-scoped index snapshot.

    ``built_at`` is the wall clock used for recency decay; queries accept
    their own ``now`` so ranking stays reproducible in tests.
    """

    entries: tuple[IndexEntry, ...]
    built_at: float
    owner_id: str


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _iso_to_epoch(value: object) -> float:
    """Parse the store's ISO-8601 timestamps into epoch seconds.

    The durable store writes ``datetime.fromtimestamp(..., UTC).isoformat()``
    which always carries an offset, but tolerate a naive timestamp too.
    Unparseable values fall back to ``0.0`` so a corrupt row cannot crash a
    search; it simply ranks as maximally stale.
    """
    if type(value) is not str or not value:
        return 0.0
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        from datetime import UTC, datetime

        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.timestamp()
    except ValueError:
        return 0.0


def _text(value: object) -> str:
    return value if type(value) is str else ""


class IndexBuilder:
    """Builds a :class:`SearchIndex` for one owner from the durable store.

    The builder takes the database path directly rather than a live
    ``OwnerPilotStore`` so it has no lifecycle coupling. The caller is
    responsible for passing a principal whose ``owner_id`` owns that file.
    """

    def __init__(self, db_path: str) -> None:
        if type(db_path) is not str or not db_path.strip():
            raise PilotError("invalid_request")
        self._db_path = str(Path(db_path).resolve())

    def build(self, principal: OwnerPrincipal) -> SearchIndex:
        """Read every owner-scoped document into an in-memory index.

        Threads contribute their title; messages contribute their content;
        tasks contribute their request message plus committed response.
        Each document is tagged with the identifiers a caller needs to
        navigate back to the durable record.
        """
        if type(principal) is not OwnerPrincipal:
            raise PilotError("unauthorized")
        principal.__post_init__()
        owner = principal.owner_id
        path = self._db_path
        if not Path(path).exists():
            raise PilotError("not_found")
        conn = _connect(path)
        try:
            entries: list[IndexEntry] = []
            for row in conn.execute(
                "SELECT id,title,updated_at FROM threads WHERE owner=? ORDER BY rowid",
                (owner,),
            ).fetchall():
                entries.append(
                    IndexEntry(
                        document_id=str(row["id"]),
                        kind="thread",
                        thread_id=str(row["id"]),
                        text=_text(row["title"]),
                        updated_at=_iso_to_epoch(row["updated_at"]),
                        owner_id=owner,
                    )
                )
            for row in conn.execute(
                "SELECT m.id,m.thread_id,m.role,m.content,m.created_at "
                "FROM thread_messages m JOIN threads t ON t.id=m.thread_id "
                "WHERE t.owner=? ORDER BY m.rowid",
                (owner,),
            ).fetchall():
                entries.append(
                    IndexEntry(
                        document_id=str(row["id"]),
                        kind="message",
                        thread_id=str(row["thread_id"]),
                        text=_text(row["content"]),
                        updated_at=_iso_to_epoch(row["created_at"]),
                        owner_id=owner,
                    )
                )
            for row in conn.execute(
                "SELECT r.task_id,r.thread_id,r.created_at,r.updated_at,"
                "r.input_json,r.result_json FROM pilot_requests r "
                "JOIN threads t ON t.id=r.thread_id WHERE t.owner=? "
                "AND r.operation='turn' ORDER BY r.sequence",
                (owner,),
            ).fetchall():
                text = _task_text(row["input_json"], row["result_json"])
                if not text:
                    continue
                entries.append(
                    IndexEntry(
                        document_id=str(row["task_id"]),
                        kind="task",
                        thread_id=str(row["thread_id"]),
                        text=text,
                        updated_at=_iso_to_epoch(row["updated_at"]),
                        owner_id=owner,
                    )
                )
        except sqlite3.Error:
            raise PilotError("store") from None
        finally:
            conn.close()
        return SearchIndex(
            entries=tuple(entries), built_at=time.time(), owner_id=owner
        )


def _task_text(input_json: object, result_json: object) -> str:
    """Flatten a task's request message and response into searchable text."""
    parts: list[str] = []
    for raw in (input_json, result_json):
        if type(raw) is not str or not raw:
            continue
        try:
            import json

            value: Any = json.loads(raw)
        except ValueError:
            continue
        if type(value) is dict:
            for key in ("message", "response"):
                text = value.get(key)
                if type(text) is str and text:
                    parts.append(text)
        elif type(value) is str and value:
            parts.append(value)
    return "\n".join(parts)


def build_index(db_path: str, principal: OwnerPrincipal) -> SearchIndex:
    """Convenience wrapper mirroring the builder's single call."""
    return IndexBuilder(db_path).build(principal)


def index_terms(index: SearchIndex) -> tuple[str, ...]:
    """All distinct terms present in an index, sorted.

    Useful for diagnostics and for UI autocomplete; not used for scoring.
    """
    terms: set[str] = set()
    for entry in index.entries:
        terms.update(split_query(entry.text))
    return tuple(sorted(terms))


__all__ = [
    "DocumentKind",
    "IndexBuilder",
    "IndexEntry",
    "SearchIndex",
    "build_index",
    "index_terms",
]
