"""Owner-scoped search over the durable pilot store.

Self-contained: index builder over the existing SQLite store (read-only),
pure ranking/scoring, snippet extraction and result limits. No HTTP route
or console UI is exposed here; a later lane wires those.

Typical use::

    from agency.search import SearchService

    service = SearchService(store.db_path)
    index = service.build_index(principal)
    response = service.search(principal, "config.yaml", limit=10)
"""

from __future__ import annotations

from pathlib import Path

from agency.admission.models import OwnerPrincipal, PilotError
from agency.search.index import IndexBuilder, IndexEntry, SearchIndex, build_index
from agency.search.models import DOCUMENT_KINDS, DocumentKind, SearchHit, SearchResponse
from agency.search.ranking import (
    RECENCY_HALF_LIFE,
    SNIPPET_LIMIT,
    match_quality,
    query_terms,
    rank_documents,
    score_document,
    snippet,
)
from agency.search.service import DEFAULT_LIMIT, MAX_LIMIT, search, search_many
from agency.search.tokenizer import (
    MAX_QUERY_CHARS,
    normalize_query,
    split_query,
    tokenize,
)

__all__ = [
    "DEFAULT_LIMIT",
    "DOCUMENT_KINDS",
    "MAX_LIMIT",
    "MAX_QUERY_CHARS",
    "RECENCY_HALF_LIFE",
    "SNIPPET_LIMIT",
    "DocumentKind",
    "IndexBuilder",
    "IndexEntry",
    "SearchHit",
    "SearchIndex",
    "SearchResponse",
    "SearchService",
    "build_index",
    "match_quality",
    "normalize_query",
    "query_terms",
    "rank_documents",
    "score_document",
    "search",
    "search_many",
    "snippet",
    "split_query",
    "tokenize",
]


class SearchService:
    """Facade tying an index builder to the query function.

    The service holds only the database path. Callers build a snapshot per
    request (or cache one per owner) and pass it to :meth:`search`, which
    keeps the ranking layer free of store handles.
    """

    def __init__(self, db_path: str) -> None:
        if type(db_path) is not str or not db_path.strip():
            raise PilotError("invalid_request")
        self._db_path = str(Path(db_path).resolve())

    @property
    def db_path(self) -> str:
        return self._db_path

    def build_index(self, principal: OwnerPrincipal) -> SearchIndex:
        return IndexBuilder(self._db_path).build(principal)

    def search(
        self,
        principal: OwnerPrincipal,
        query: str,
        *,
        limit: int = DEFAULT_LIMIT,
        now: float | None = None,
    ) -> SearchResponse:
        index = self.build_index(principal)
        return search(index, principal, query, limit=limit, now=now)
