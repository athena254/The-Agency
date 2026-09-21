"""Multi-mode retrieval for the Sovereign Mind System.

Three modes are exposed:

* :meth:`RetrievalEngine.semantic_search` - SQLite FTS5 full-text search.
* :meth:`RetrievalEngine.graph_search` - relationship traversal (stub; will be
  backed by the Lattice node / Neo4j).
* :meth:`RetrievalEngine.hybrid_search` - merge of the two, de-duplicated.

The semantic implementation is intentionally local-first: no embedding server
or network dependency is required for agents to retrieve their own memory.
"""

from __future__ import annotations

import re

import structlog

from agency.memory.sms.models import MemoryItem, utc_now
from agency.memory.sms.store import MemoryStore

logger = structlog.get_logger(__name__)

DEFAULT_LIMIT = 10
_TERM_PATTERN = re.compile(r"[A-Za-z0-9_]+")


def build_fts_match(query: str) -> str:
    """Turn free text into a safe FTS5 ``MATCH`` expression.

    Each alphanumeric token becomes a quoted prefix term joined with ``OR``.
    Quoting protects against FTS5 operator injection (``NEAR``, ``*``, ``:``).
    """

    tokens = _TERM_PATTERN.findall(query)
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
        """Full-text search ranked by BM25, scoped to ``agent_id`` if given."""

        match = build_fts_match(query)
        if not match:
            return []
        results = await self._store.search_fts(match, agent_id=agent_id, limit=limit)
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
        return results

    async def graph_search(
        self,
        entity: str,
        relationship: str,
        limit: int = DEFAULT_LIMIT,
    ) -> list[MemoryItem]:
        """Traverse relationships between entities.

        Stub: the Lattice node (Neo4j) is not wired up yet, so this always
        returns an empty list. Once available, it should resolve ``entity`` and
        walk ``relationship`` edges to related memory ids.
        """

        logger.info(
            "retrieval.graph_search.stub",
            entity=entity,
            relationship=relationship,
            limit=limit,
        )
        return []

    async def hybrid_search(
        self,
        query: str,
        agent_id: str | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> list[MemoryItem]:
        """Combine semantic and graph results, preserving relevance order."""

        semantic = await self.semantic_search(query, agent_id=agent_id, limit=limit)
        graph = await self.graph_search(query, relationship="related", limit=limit)

        merged: list[MemoryItem] = []
        seen: set[str] = set()
        for item in (*semantic, *graph):
            if item.id in seen:
                continue
            seen.add(item.id)
            merged.append(item)
        merged = merged[:limit]
        logger.debug(
            "retrieval.hybrid_search",
            query=query,
            agent_id=agent_id,
            semantic=len(semantic),
            graph=len(graph),
            results=len(merged),
        )
        return merged
