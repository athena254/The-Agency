"""Scoped search hit documents and ranked result contracts.

The search service is read-only over durable owner data. Everything it
returns is derived from an index built by the owner-pilot store, so the
wire contracts live here and never re-validate peer authority.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

DocumentKind = Literal["thread", "message", "task"]

#: Sortable document types, used by ranking as a coarse relevance prior.
DOCUMENT_KINDS: frozenset[str] = frozenset({"thread", "message", "task"})


class SearchHit(BaseModel):
    """One ranked match with its identifiers and a bounded text snippet."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    #: Position in the ranked result list; ``0`` is the best match.
    rank: int
    kind: DocumentKind
    thread_id: str
    #: ``message``/``task`` id for those kinds; ``None`` for a thread hit.
    document_id: str | None
    #: ISO-8601 timestamp of the indexed document's last update.
    updated_at: str
    score: float
    snippet: str
    #: Ranked term list; kept for UI grouping, never for scoring only.
    terms: tuple[str, ...] = ()


class SearchResponse(BaseModel):
    """A complete owner-scoped search result."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    hits: tuple[SearchHit, ...]
    total: int
    truncated: bool
    principal: str
    query: str


__all__ = [
    "DOCUMENT_KINDS",
    "DocumentKind",
    "SearchHit",
    "SearchResponse",
]
