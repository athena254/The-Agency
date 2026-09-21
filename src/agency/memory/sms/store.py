"""SQLite-backed memory store for the Sovereign Mind System.

The store owns persistence only. Lifecycle policy (which tier an item *should*
live in) lives in :mod:`agency.memory.sms.lifecycle`; the store just knows how
to move bytes.

An FTS5 virtual table mirrors ``memory_items`` so that the retrieval node can
run full-text queries without scanning the base table.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from typing import Any, Self

import aiosqlite
import structlog

from agency.memory.sms.models import (
    MemoryItem,
    MemoryQuery,
    MemoryTier,
    ensure_utc,
    utc_now,
)

logger = structlog.get_logger(__name__)

DEFAULT_AGING_THRESHOLD = timedelta(days=7)
"""Items untouched for this long are demoted by :meth:`MemoryStore.auto_age`."""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_items (
    id          TEXT PRIMARY KEY,
    agent_id    TEXT NOT NULL,
    content     TEXT NOT NULL,
    tier        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    accessed_at TEXT NOT NULL,
    importance  REAL NOT NULL DEFAULT 0.5,
    tags        TEXT NOT NULL DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_memory_items_agent    ON memory_items(agent_id);
CREATE INDEX IF NOT EXISTS idx_memory_items_tier     ON memory_items(tier);
CREATE INDEX IF NOT EXISTS idx_memory_items_accessed ON memory_items(accessed_at);

CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
    item_id UNINDEXED,
    content,
    tags,
    tokenize = 'porter unicode61'
);
"""

_ITEM_COLUMNS = "id, agent_id, content, tier, created_at, accessed_at, importance, tags"

_DEMOTE_CASE = """
CASE tier
    WHEN 'hot'    THEN 'warm'
    WHEN 'warm'   THEN 'normal'
    WHEN 'normal' THEN 'cool'
    WHEN 'cool'   THEN 'cold'
    ELSE 'cold'
END
"""


def _parse_datetime(raw: str) -> datetime:
    """Parse an ISO-8601 string produced by :meth:`datetime.isoformat`."""

    return ensure_utc(datetime.fromisoformat(raw))


def _row_to_item(row: aiosqlite.Row) -> MemoryItem:
    """Materialize a :class:`MemoryItem` from a SQLite row."""

    tags_raw = row["tags"]
    return MemoryItem(
        id=row["id"],
        agent_id=row["agent_id"],
        content=row["content"],
        tier=MemoryTier(row["tier"]),
        created_at=_parse_datetime(row["created_at"]),
        accessed_at=_parse_datetime(row["accessed_at"]),
        importance=float(row["importance"]),
        tags=json.loads(tags_raw) if tags_raw else [],
    )


class MemoryStore:
    """Async SQLite store for :class:`MemoryItem` records.

    Lifecycle::

        store = MemoryStore("~/.theagency/sms/memory.db")
        await store.initialize()
        try:
            ...
        finally:
            await store.close()

    or use the store as an async context manager. All public methods lazily
    initialize the schema, so explicit ``initialize`` is optional.
    """

    def __init__(
        self,
        db_path: str = ":memory:",
        *,
        aging_threshold: timedelta = DEFAULT_AGING_THRESHOLD,
    ) -> None:
        self._db_path = db_path
        self._aging_threshold = aging_threshold
        self._conn: aiosqlite.Connection | None = None
        self._initialized = False
        self._init_lock = asyncio.Lock()

    async def __aenter__(self) -> Self:
        await self.initialize()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    @property
    def connection(self) -> aiosqlite.Connection:
        """The live connection. Raises if :meth:`initialize` has not run."""

        if self._conn is None:
            raise RuntimeError("MemoryStore is not initialized")
        return self._conn

    @property
    def db_path(self) -> str:
        return self._db_path

    async def initialize(self) -> None:
        """Open the connection and create the schema (idempotent)."""

        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            self._conn = await aiosqlite.connect(self._db_path)
            self._conn.row_factory = aiosqlite.Row
            await self._conn.execute("PRAGMA journal_mode=WAL")
            await self._conn.execute("PRAGMA busy_timeout=5000")
            await self._conn.executescript(_SCHEMA)
            await self._conn.commit()
            self._initialized = True
            logger.info("memory_store.initialized", db_path=self._db_path)

    async def close(self) -> None:
        """Close the connection. Safe to call more than once."""

        if self._conn is not None:
            await self._conn.close()
            self._conn = None
            self._initialized = False
            logger.info("memory_store.closed", db_path=self._db_path)

    async def store(self, item: MemoryItem) -> MemoryItem:
        """Insert or replace ``item`` and keep the FTS index in sync."""

        await self.initialize()
        conn = self.connection
        tags_json = json.dumps(item.tags)
        await conn.execute(
            f"""
            INSERT INTO memory_items ({_ITEM_COLUMNS})
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                agent_id    = excluded.agent_id,
                content     = excluded.content,
                tier        = excluded.tier,
                created_at  = excluded.created_at,
                accessed_at = excluded.accessed_at,
                importance  = excluded.importance,
                tags        = excluded.tags
            """,
            (
                item.id,
                item.agent_id,
                item.content,
                item.tier.value,
                item.created_at.isoformat(),
                item.accessed_at.isoformat(),
                item.importance,
                tags_json,
            ),
        )
        await conn.execute("DELETE FROM memory_fts WHERE item_id = ?", (item.id,))
        await conn.execute(
            "INSERT INTO memory_fts (item_id, content, tags) VALUES (?, ?, ?)",
            (item.id, item.content, " ".join(item.tags)),
        )
        await conn.commit()
        logger.debug(
            "memory_store.stored",
            item_id=item.id,
            agent_id=item.agent_id,
            tier=item.tier.value,
        )
        return item

    async def get(self, item_id: str) -> MemoryItem | None:
        """Fetch a single item by id, or ``None`` when absent."""

        await self.initialize()
        async with self.connection.execute(
            "SELECT * FROM memory_items WHERE id = ?", (item_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return _row_to_item(row) if row is not None else None

    async def search(self, query: MemoryQuery) -> list[MemoryItem]:
        """Return items matching ``query`` ordered by importance then recency."""

        await self.initialize()
        clauses: list[str] = []
        params: list[Any] = []

        if query.agent_id is not None:
            clauses.append("agent_id = ?")
            params.append(query.agent_id)
        if query.tier is not None:
            clauses.append("tier = ?")
            params.append(query.tier.value)
        if query.tags:
            placeholders = ", ".join("?" for _ in query.tags)
            clauses.append(
                "EXISTS ("
                "  SELECT 1 FROM json_each(memory_items.tags) AS tag "
                f" WHERE tag.value IN ({placeholders})"
                ")"
            )
            params.extend(query.tags)

        where = " AND ".join(clauses) if clauses else "1 = 1"
        params.append(query.limit)
        sql = (
            f"SELECT * FROM memory_items WHERE {where} "
            "ORDER BY importance DESC, accessed_at DESC LIMIT ?"
        )
        async with self.connection.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
        return [_row_to_item(row) for row in rows]

    async def update_tier(self, item_id: str, tier: MemoryTier) -> MemoryItem | None:
        """Assign ``tier`` to an item, returning the updated item or ``None``."""

        await self.initialize()
        cursor = await self.connection.execute(
            "UPDATE memory_items SET tier = ? WHERE id = ?", (tier.value, item_id)
        )
        await self.connection.commit()
        if cursor.rowcount == 0:
            logger.warning("memory_store.update_tier.missing", item_id=item_id)
            return None
        logger.debug("memory_store.tier_updated", item_id=item_id, tier=tier.value)
        return await self.get(item_id)

    async def delete(self, item_id: str) -> bool:
        """Delete an item from both the base table and the FTS index."""

        await self.initialize()
        conn = self.connection
        cursor = await conn.execute("DELETE FROM memory_items WHERE id = ?", (item_id,))
        await conn.execute("DELETE FROM memory_fts WHERE item_id = ?", (item_id,))
        await conn.commit()
        deleted = cursor.rowcount > 0
        logger.debug("memory_store.deleted", item_id=item_id, deleted=deleted)
        return deleted

    async def list_by_agent(self, agent_id: str, limit: int = 100) -> list[MemoryItem]:
        """All items owned by ``agent_id``, most recently accessed first."""

        await self.initialize()
        async with self.connection.execute(
            "SELECT * FROM memory_items WHERE agent_id = ? ORDER BY accessed_at DESC LIMIT ?",
            (agent_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()
        return [_row_to_item(row) for row in rows]

    async def list_by_tier(self, tier: MemoryTier, limit: int = 1000) -> list[MemoryItem]:
        """All items currently in ``tier``, most recently accessed first."""

        await self.initialize()
        async with self.connection.execute(
            "SELECT * FROM memory_items WHERE tier = ? ORDER BY accessed_at DESC LIMIT ?",
            (tier.value, limit),
        ) as cursor:
            rows = await cursor.fetchall()
        return [_row_to_item(row) for row in rows]

    async def list_all(self, limit: int = 10000) -> list[MemoryItem]:
        """Every item in the store. Used by the lifecycle scanner."""

        await self.initialize()
        async with self.connection.execute(
            "SELECT * FROM memory_items ORDER BY accessed_at ASC LIMIT ?", (limit,)
        ) as cursor:
            rows = await cursor.fetchall()
        return [_row_to_item(row) for row in rows]

    async def touch(self, item_id: str, accessed_at: datetime | None = None) -> bool:
        """Mark an item as accessed now (or at ``accessed_at``)."""

        return await self.touch_many([item_id], accessed_at) > 0

    async def touch_many(self, item_ids: list[str], accessed_at: datetime | None = None) -> int:
        """Mark several items as accessed in a single statement."""

        if not item_ids:
            return 0
        await self.initialize()
        when = ensure_utc(accessed_at or utc_now()).isoformat()
        placeholders = ", ".join("?" for _ in item_ids)
        cursor = await self.connection.execute(
            f"UPDATE memory_items SET accessed_at = ? WHERE id IN ({placeholders})",
            (when, *item_ids),
        )
        await self.connection.commit()
        return cursor.rowcount

    async def count(self) -> int:
        """Total number of stored items."""

        await self.initialize()
        async with self.connection.execute("SELECT COUNT(*) AS n FROM memory_items") as cursor:
            row = await cursor.fetchone()
        return int(row["n"]) if row is not None else 0

    async def search_fts(
        self, match: str, *, agent_id: str | None = None, limit: int = 10
    ) -> list[MemoryItem]:
        """Run a raw FTS5 ``MATCH`` expression against the mirror index.

        ``match`` is built by :class:`~agency.memory.sms.retrieval.RetrievalEngine`.
        Results are ranked best-first using ``bm25``.
        """

        await self.initialize()
        sql = (
            "SELECT m.* FROM memory_fts "
            "JOIN memory_items AS m ON m.id = memory_fts.item_id "
            "WHERE memory_fts MATCH ?"
        )
        params: list[Any] = [match]
        if agent_id is not None:
            sql += " AND m.agent_id = ?"
            params.append(agent_id)
        sql += " ORDER BY bm25(memory_fts) ASC, m.importance DESC LIMIT ?"
        params.append(limit)
        async with self.connection.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
        return [_row_to_item(row) for row in rows]

    async def auto_age(self) -> int:
        """Demote every item untouched for :data:`DEFAULT_AGING_THRESHOLD`.

        Each stale item moves down exactly one tier and its ``accessed_at`` is
        refreshed so the aging clock restarts; without the refresh an item
        would cascade to COLD on subsequent runs. Returns the number of rows
        demoted.
        """

        await self.initialize()
        now = utc_now()
        cutoff = now - self._aging_threshold
        cursor = await self.connection.execute(
            f"""
            UPDATE memory_items
            SET tier = {_DEMOTE_CASE}, accessed_at = ?
            WHERE accessed_at < ? AND tier != 'cold'
            """,
            (now.isoformat(), cutoff.isoformat()),
        )
        await self.connection.commit()
        moved = cursor.rowcount
        logger.info("memory_store.auto_age", moved=moved, threshold=self._aging_threshold)
        return moved
