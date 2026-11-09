"""Pure ranking and snippet extraction for owner-scoped search.

No I/O, no clock reads, no store handles. Every function here is
deterministic so the ranking order can be asserted directly in tests.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence

from agency.search.tokenizer import split_query

#: Weight of an exact substring hit (case-insensitive) in the score.
EXACT_WEIGHT = 3.0
#: Weight per matched token occurrence.
TOKEN_WEIGHT = 1.0
#: Weight for a document that matches *every* query term.
COVERAGE_WEIGHT = 2.5
#: Coarse prior: titles are worth more than bodies at equal match quality.
THREAD_PRIOR = 1.3
MESSAGE_PRIOR = 1.0
TASK_PRIOR = 1.15

#: Recency half-life in seconds. A document this many seconds older than the
#: newest matching document is worth half as much from recency alone.
RECENCY_HALF_LIFE = 7 * 24 * 3600.0

#: Maximum characters in a returned snippet.
SNIPPET_LIMIT = 240
#: Characters of context around a match inside a snippet.
SNIPPET_CONTEXT = 48

_DOCUMENT_PRIORS = {
    "thread": THREAD_PRIOR,
    "message": MESSAGE_PRIOR,
    "task": TASK_PRIOR,
}

#: Terms that carry no ranking signal on their own.
_STOP_TERMS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "this",
        "that",
        "from",
        "into",
        "your",
        "you",
        "are",
        "was",
        "were",
        "has",
        "have",
        "not",
        "but",
        "all",
        "can",
        "will",
        "would",
        "should",
        "about",
    }
)


_WORD = re.compile(r"[0-9a-z]+")


def _tokens(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def match_quality(text: str, terms: Sequence[str]) -> float:
    """Score how well ``text`` matches ``terms``.

    Exact substring hits dominate; whole-word token hits add linearly;
    covering every term earns a bonus. Stop terms are ignored so
    ``the config`` ranks by ``config`` alone. Returns ``0.0`` when nothing
    matches.
    """
    if not terms or not text:
        return 0.0
    active = tuple(term for term in terms if term and term not in _STOP_TERMS)
    if not active:
        return 0.0
    lowered = text.lower()
    words = _tokens(lowered)
    quality = 0.0
    covered = 0
    for term in active:
        occurrences = lowered.count(term)
        if occurrences:
            quality += EXACT_WEIGHT * occurrences
            covered += 1
            continue
        # Token fallback: the term matches as a whole word somewhere.
        hits = words.count(term)
        if hits:
            quality += TOKEN_WEIGHT * hits
            covered += 1
    if covered == len(active):
        quality += COVERAGE_WEIGHT
    return quality


def recency_decay(updated_at: float, newest: float) -> float:
    """Return a ``[0, 1]`` recency factor for one document.

    A document at or newer than the newest match scores ``1.0``; each
    ``RECENCY_HALF_LIFE`` of age halves the factor. Non-finite timestamps
    score ``0.0`` so corrupt rows rank last instead of crashing a search.
    """
    if not math.isfinite(updated_at) or not math.isfinite(newest):
        return 0.0
    age = max(0.0, newest - updated_at)
    if age <= 0.0:
        return 1.0
    return float(0.5 ** (age / RECENCY_HALF_LIFE))


def score_document(
    *,
    kind: str,
    text: str,
    terms: Sequence[str],
    updated_at: float,
    newest: float,
) -> float:
    """Combine match quality, document prior and recency into one score.

    A document with no match scores ``0.0`` regardless of recency. Recency
    is multiplicative so a stale perfect match still loses to a fresh
    strong match, and the prior scales the whole result.
    """
    quality = match_quality(text, terms)
    if quality <= 0.0:
        return 0.0
    prior = _DOCUMENT_PRIORS.get(kind, 1.0)
    return quality * prior * recency_decay(updated_at, newest)


def rank_documents(
    documents: Iterable[tuple[str, str, str, float, str]],
    terms: Sequence[str],
    *,
    limit: int,
) -> tuple[list[tuple[str, str, float]], int]:
    """Rank ``(document_id, kind, text, updated_at, thread_id)`` rows.

    Returns ``(ranked, total)`` where ``ranked`` is truncated to ``limit``
    and each entry carries ``(document_id, kind, score)``. Ordering is by
    score descending, then by most recent first, then by document id for a
    stable tie-break. ``total`` counts every matching document, including
    those past ``limit``, so callers can report truncation.
    """
    rows = list(documents)
    if limit <= 0 or not terms:
        return [], 0
    timestamps = [row[3] for row in rows if math.isfinite(row[3])]
    newest = max(timestamps, default=0.0)
    scored: list[tuple[float, float, str, str, str]] = []
    for document_id, kind, text, updated_at, thread_id in rows:
        score = score_document(
            kind=kind,
            text=text,
            terms=terms,
            updated_at=updated_at,
            newest=newest,
        )
        if score <= 0.0:
            continue
        scored.append((score, updated_at, document_id, kind, thread_id))
    scored.sort(key=lambda item: (-item[0], -item[1], item[2]))
    ranked = [(item[2], item[3], item[0]) for item in scored[:limit]]
    return ranked, len(scored)


def snippet(text: str, terms: Sequence[str], *, limit: int = SNIPPET_LIMIT) -> str:
    """Extract a bounded snippet centered on the first meaningful match.

    Falls back to the document's opening text when no term matches (a
    recency-ordered index still shows a preview). The result never exceeds
    ``limit`` characters and marks elision with ``…`` on both ends when the
    document is longer.
    """
    if not text:
        return ""
    if len(text) <= limit:
        return text
    lowered = text.lower()
    position = -1
    for term in terms:
        if not term:
            continue
        found = lowered.find(term)
        if found >= 0 and (position < 0 or found < position):
            position = found
    if position < 0:
        return "…" + text[: max(0, limit - 1)].rstrip()
    start = max(0, position - SNIPPET_CONTEXT)
    end = min(len(text), position + limit - SNIPPET_CONTEXT)
    body = text[start:end].strip()
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return prefix + body + suffix


def query_terms(query: str) -> tuple[str, ...]:
    """Validated query terms for a raw user query string."""
    return split_query(query)


def effective_terms(terms: Sequence[str]) -> tuple[str, ...]:
    """Drop stop terms, preserving order and de-duplicating."""
    seen: set[str] = set()
    result: list[str] = []
    for term in terms:
        if term in seen or term in _STOP_TERMS:
            continue
        seen.add(term)
        result.append(term)
    return tuple(result)


__all__ = [
    "COVERAGE_WEIGHT",
    "EXACT_WEIGHT",
    "RECENCY_HALF_LIFE",
    "SNIPPET_CONTEXT",
    "SNIPPET_LIMIT",
    "TOKEN_WEIGHT",
    "effective_terms",
    "match_quality",
    "query_terms",
    "rank_documents",
    "recency_decay",
    "score_document",
    "snippet",
]
