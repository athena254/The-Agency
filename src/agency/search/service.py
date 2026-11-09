"""Owner-scoped query function over a materialized search index.

The public entry point is :func:`search`. It takes a built
:class:`~agency.search.index.SearchIndex`, a principal, and a raw query,
then returns ranked hits with identifiers and snippets. Every query is
principal-scoped: a principal can only search an index whose
``owner_id`` equals its own ``owner_id``, enforced before any matching.
"""

from __future__ import annotations

import math
import time
from datetime import UTC, datetime
from typing import cast

from agency.admission.models import OwnerPrincipal, PilotError
from agency.search.index import DocumentKind, IndexEntry, SearchIndex
from agency.search.models import SearchHit, SearchResponse
from agency.search.ranking import (
    SNIPPET_LIMIT,
    effective_terms,
    query_terms,
    rank_documents,
    snippet,
)
from agency.search.tokenizer import MAX_QUERY_CHARS, normalize_query

#: Default and maximum result limits. The store's own list cap is 100, so
#: search matches that ceiling.
DEFAULT_LIMIT = 20
MAX_LIMIT = 100


def _validate_limit(limit: object) -> int:
    if type(limit) is not int or isinstance(limit, bool):
        raise PilotError("invalid_request")
    if not 1 <= limit <= MAX_LIMIT:
        raise PilotError("invalid_request")
    return limit


def _validate_principal(index: SearchIndex, principal: OwnerPrincipal) -> str:
    if type(principal) is not OwnerPrincipal:
        raise PilotError("unauthorized")
    principal.__post_init__()
    if index.owner_id != principal.owner_id:
        # Same sanitized failure whether the index is missing or foreign.
        raise PilotError("not_found")
    return principal.owner_id


def search(
    index: SearchIndex,
    principal: OwnerPrincipal,
    query: str,
    *,
    limit: int = DEFAULT_LIMIT,
    now: float | None = None,
) -> SearchResponse:
    """Return ranked, owner-scoped hits for ``query``.

    The index must already be built for ``principal``. Results are ordered
    by match quality, then recency, then document id, and truncated to
    ``limit``. An empty or stop-only query returns no hits.
    """
    if type(index) is not SearchIndex:
        raise PilotError("invalid_request")
    _validate_limit(limit)
    owner = _validate_principal(index, principal)
    normalized = normalize_query(query)
    terms = effective_terms(query_terms(normalized))
    if not terms:
        return SearchResponse(hits=(), total=0, truncated=False, principal=owner, query=normalized)
    moment = time.time() if now is None else float(now)
    if not math.isfinite(moment):
        raise PilotError("invalid_request")
    rows = [
        (entry.document_id, entry.kind, entry.text, entry.updated_at, entry.thread_id)
        for entry in index.entries
        if entry.owner_id == owner
    ]
    ranked, total = rank_documents(rows, terms, limit=limit)
    hits: list[SearchHit] = []
    for position, (document_id, kind, score) in enumerate(ranked):
        entry = _find_entry(index, document_id, cast(DocumentKind, kind), owner)
        if entry is None:
            continue
        hits.append(
            SearchHit(
                rank=position,
                kind=cast(DocumentKind, kind),
                thread_id=entry.thread_id,
                document_id=entry.document_id,
                updated_at=_format_timestamp(entry.updated_at),
                score=round(score, 6),
                snippet=snippet(entry.text, terms, limit=SNIPPET_LIMIT),
                terms=tuple(terms),
            )
        )
    return SearchResponse(
        hits=tuple(hits),
        total=total,
        truncated=total > len(hits),
        principal=owner,
        query=normalized,
    )


def _find_entry(
    index: SearchIndex, document_id: str, kind: DocumentKind, owner: str
) -> IndexEntry | None:
    for entry in index.entries:
        if entry.owner_id != owner:
            continue
        if entry.document_id == document_id and entry.kind == kind:
            return entry
    return None


def _format_timestamp(epoch: float) -> str:
    if not math.isfinite(epoch):
        return ""
    return datetime.fromtimestamp(epoch, UTC).isoformat()


def search_many(
    index: SearchIndex,
    principal: OwnerPrincipal,
    queries: list[str],
    *,
    limit: int = DEFAULT_LIMIT,
) -> list[SearchResponse]:
    """Run several queries against one index snapshot.

    Each response is independently principal-scoped; the caller still owns
    authorization. Useful for batch diagnostics.
    """
    if type(queries) is not list or any(type(q) is not str for q in queries):
        raise PilotError("invalid_request")
    return [search(index, principal, query, limit=limit) for query in queries]


__all__ = [
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "MAX_QUERY_CHARS",
    "search",
    "search_many",
]
