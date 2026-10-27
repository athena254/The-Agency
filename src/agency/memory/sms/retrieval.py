"""Multi-mode retrieval for the Sovereign Mind System.

Three modes are exposed:

* :meth:`RetrievalEngine.semantic_search` - SQLite FTS5 full-text search with a
  local fallback when FTS yields nothing.
* :meth:`RetrievalEngine.graph_search` - lightweight tag/entity co-occurrence
  traversal (no external graph DB required).
* :meth:`RetrievalEngine.hybrid_search` - merge of the two, de-duplicated and
  re-ranked.
"""

from __future__ import annotations

import re

import structlog

from agency.memory.sms.models import MemoryItem, utc_now
from agency.memory.sms.store import MemoryStore

logger = structlog.get_logger(__name__)

DEFAULT_LIMIT = 10
_TERM_PATTERN = re.compile(r"[A-Za-z0-9_]+")
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "to",
        "was",
        "were",
        "will",
        "with",
    }
)


def _split_camel(value: str) -> list[str]:
    return re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|\d+", value)


def tokenize(text: str) -> list[str]:
    """Split text into meaningful lowercase tokens."""
    raw = _TERM_PATTERN.findall(text)
    out: list[str] = []
    for token in raw:
        parts = _split_camel(token) or [token]
        for part in parts:
            low = part.lower()
            if low and low not in _STOPWORDS and len(low) >= 2:
                out.append(low)
    # de-duplicate preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for t in out:
        if t not in seen:
            seen.add(t)
            unique.append(t)
    return unique


def build_fts_match(query: str) -> str:
    """Turn free text into a safe FTS5 ``MATCH`` expression."""
    tokens = tokenize(query)
    if not tokens:
        raw = _TERM_PATTERN.findall(query)
        tokens = [t.lower() for t in raw if t]
    if not tokens:
        return ""
    return " OR ".join(f'"{token}"*' for token in tokens)


class RetrievalEngine:
    """Search across agent memory using FTS, graph, or both."""

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    async def semantic_search(
        self,
        query: str,
        agent_id: str | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> list[MemoryItem]:
        """Full-text search ranked by BM25, with a local fallback."""
        match = build_fts_match(query)
        results: list[MemoryItem] = []
        if match:
            results = await self._store.search_fts(match, agent_id=agent_id, limit=limit)

        # Fallback: substring scan when FTS returns nothing.
        if not results:
            results = await self._substring_fallback(query, agent_id=agent_id, limit=limit)

        # Phrase boost: items containing the full query phrase first.
        q_lower = query.strip().lower()
        if q_lower and len(results) > 1:
            phrase_hits = [r for r in results if q_lower in r.content.lower()]
            others = [r for r in results if q_lower not in r.content.lower()]
            results = phrase_hits + others

        if results:
            now = utc_now()
            await self._store.touch_many([item.id for item in results], now)
            results = [item.model_copy(update={"accessed_at": now}) for item in results]
        logger.debug(
            "retrieval.semantic_search",
            query=query,
            agent_id=agent_id,
            results=len(results),
        )
        return results[:limit]

    async def _substring_fallback(
        self, query: str, *, agent_id: str | None, limit: int
    ) -> list[MemoryItem]:
        tokens = tokenize(query)
        if not tokens and not query.strip():
            return []
        if agent_id is not None:
            candidates = await self._store.list_by_agent(agent_id, limit=1000)
        else:
            candidates = await self._store.list_all(limit=1000)
        scored: list[tuple[int, MemoryItem]] = []
        for item in candidates:
            content_low = item.content.lower()
            tag_text = " ".join(item.tags).lower()
            haystack = content_low + " " + tag_text
            score = sum(1 for t in tokens if t in haystack)
            if score > 0:
                scored.append((score, item))
        scored.sort(key=lambda pair: (-pair[0], -pair[1].importance))
        return [item for _, item in scored[:limit]]

    async def graph_search(
        self,
        entity: str,
        relationship: str = "related",
        limit: int = DEFAULT_LIMIT,
        agent_id: str | None = None,
    ) -> list[MemoryItem]:
        """Lightweight tag/entity co-occurrence traversal."""
        if not entity.strip():
            return []
        if agent_id is not None:
            candidates = await self._store.list_by_agent(agent_id, limit=1000)
        else:
            candidates = await self._store.list_all(limit=1000)

        entity_low = entity.strip().lower()
        seed_ids: set[str] = set()
        seed_tags: set[str] = set()
        for item in candidates:
            if entity_low in item.content.lower() or any(
                entity_low in t.lower() for t in item.tags
            ):
                seed_ids.add(item.id)
                seed_tags.update(t.lower() for t in item.tags)

        related: list[MemoryItem] = []
        seen: set[str] = set(seed_ids)
        for item in candidates:
            if item.id in seen:
                continue
            item_tags = {t.lower() for t in item.tags}
            if seed_tags and (item_tags & seed_tags) or entity_low in item.content.lower():
                related.append(item)
                seen.add(item.id)
        # rank by importance then recency
        related.sort(key=lambda m: (-m.importance, m.accessed_at), reverse=False)
        related.sort(key=lambda m: m.importance, reverse=True)
        return related[:limit]

    async def hybrid_search(
        self,
        query: str,
        agent_id: str | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> list[MemoryItem]:
        """Combine semantic and graph results, de-duplicated and re-ranked."""
        semantic = await self.semantic_search(query, agent_id=agent_id, limit=limit)
        graph = await self.graph_search(
            query, relationship="related", limit=limit, agent_id=agent_id
        )

        merged: list[MemoryItem] = []
        seen: set[str] = set()
        for item in (*semantic, *graph):
            if item.id in seen:
                continue
            seen.add(item.id)
            merged.append(item)

        # Re-rank: semantic order first (already relevance), then by importance.
        sem_ids = {i.id for i in semantic}
        merged.sort(key=lambda m: (0 if m.id in sem_ids else 1, -m.importance))
        merged = merged[:limit]

        if merged:
            now = utc_now()
            # touch already done in semantic; touch graph-only
            graph_only = [i.id for i in merged if i.id not in sem_ids]
            if graph_only:
                await self._store.touch_many(graph_only, now)
        logger.debug(
            "hybrid_search",
            query=query,
            agent_id=agent_id,
            semantic=len(semantic),
            graph=len(graph),
            results=len(merged),
        )
        return merged
