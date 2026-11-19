"""Protocol interfaces for SMS memory backends.

Backends are structural: anything exposing these async methods satisfies the
protocol regardless of its concrete class. This keeps the default SQLite store
and any future optional backend (e.g. an external Honcho adapter) interchangeable
behind the same trusted seams.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from agency.memory.sms.models import MemoryItem, MemoryQuery, MemoryTier


@runtime_checkable
class MemoryBackend(Protocol):
    """Read/write persistence for memory items."""

    @property
    def db_path(self) -> str: ...

    async def initialize(self) -> None: ...

    async def close(self) -> None: ...

    async def store(self, item: MemoryItem) -> MemoryItem: ...

    async def get(self, item_id: str) -> MemoryItem | None: ...

    async def search(self, query: MemoryQuery) -> list[MemoryItem]: ...

    async def search_text(
        self, text: str, *, agent_id: str | None = None, limit: int = 10
    ) -> list[MemoryItem]: ...

    async def search_fts(
        self, match: str, *, agent_id: str | None = None, limit: int = 10
    ) -> list[MemoryItem]: ...

    async def list_by_agent(self, agent_id: str, limit: int = 100) -> list[MemoryItem]: ...

    async def list_by_tier(self, tier: MemoryTier, limit: int = 1000) -> list[MemoryItem]: ...

    async def list_all(self, limit: int = 10000) -> list[MemoryItem]: ...

    async def touch(self, item_id: str, accessed_at: datetime | None = None) -> bool: ...

    async def touch_many(self, item_ids: list[str], accessed_at: datetime | None = None) -> int: ...

    async def count(self) -> int: ...

    async def update_tier(self, item_id: str, tier: MemoryTier) -> MemoryItem | None: ...

    async def delete(self, item_id: str) -> bool: ...


@runtime_checkable
class RetrievalBackend(Protocol):
    """Multi-mode retrieval over memory items."""

    async def semantic_search(
        self, query: str, agent_id: str | None = None, limit: int = 10
    ) -> list[MemoryItem]: ...

    async def graph_search(
        self,
        entity: str,
        relationship: str = "related",
        limit: int = 10,
        agent_id: str | None = None,
    ) -> list[MemoryItem]: ...

    async def hybrid_search(
        self, query: str, agent_id: str | None = None, limit: int = 10
    ) -> list[MemoryItem]: ...


__all__ = ["MemoryBackend", "RetrievalBackend"]
