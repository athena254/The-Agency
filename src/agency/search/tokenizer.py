"""Deterministic tokenization shared by indexing and query parsing.

Tokens are lowercased alphanumeric runs. Splitting on punctuation keeps
``config.yaml`` and ``config-yaml`` from colliding, and the tokenizer is
pure so both the index builder and the query path see identical terms.
"""

from __future__ import annotations

import re

_TOKEN = re.compile(r"[0-9A-Za-z]+")
_SPLIT = re.compile(r"[^0-9A-Za-z]+")

#: A query this long is a document dump, not a search. Refuse it.
MAX_QUERY_CHARS = 256

#: Longest single token we index or match. Guards pathological input.
MAX_TOKEN_CHARS = 64

#: Shortest term that still counts as a token. Single-character terms are
#: dropped because they make token matching a no-op substring search.
MIN_TOKEN_CHARS = 2


def tokenize(text: object) -> tuple[str, ...]:
    """Return the stable, lowercased token tuple for ``text``.

    Non-string input yields ``()`` rather than raising: the indexer reads
    untyped SQLite columns and a NULL must degrade to an empty term set.
    """
    if type(text) is not str:
        return ()
    terms: list[str] = []
    for raw in _TOKEN.findall(text.lower()):
        if MIN_TOKEN_CHARS <= len(raw) <= MAX_TOKEN_CHARS:
            terms.append(raw)
    return tuple(terms)


def split_query(query: object) -> tuple[str, ...]:
    """Tokenize a user query, dropping empties and duplicates in order.

    Duplicates are removed so a repeated term cannot inflate the match
    score; the first occurrence defines its position in the result.
    """
    terms = tokenize(query)
    seen: set[str] = set()
    unique: list[str] = []
    for term in terms:
        if term not in seen:
            seen.add(term)
            unique.append(term)
    return tuple(unique)


def normalize_query(query: object) -> str:
    """Collapse a raw query to a bounded, single-line search string."""
    if type(query) is not str:
        return ""
    return " ".join(query.split())[:MAX_QUERY_CHARS]


def substring_variants(term: str) -> tuple[str, ...]:
    """Return the literal substring probes for a token.

    For a normal token this is just the token itself. For very long tokens
    we also probe shorter suffixes so a truncated index still matches a
    prefix of the document term, which keeps substring matching useful
    without unbounded probes.
    """
    if not term:
        return ()
    if len(term) <= MAX_TOKEN_CHARS:
        return (term,)
    probes = [term[:MAX_TOKEN_CHARS]]
    if len(term) > MAX_TOKEN_CHARS * 2:
        probes.append(term[-MAX_TOKEN_CHARS:])
    return tuple(probes)


__all__ = [
    "MAX_QUERY_CHARS",
    "MAX_TOKEN_CHARS",
    "MIN_TOKEN_CHARS",
    "normalize_query",
    "split_query",
    "substring_variants",
    "tokenize",
]
