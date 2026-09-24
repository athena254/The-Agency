"""SQLite backend for the Unified Lattice Service.

Implements SPEC_LATTICE.md section 5.1 (SQLite schema, zero external
dependencies) and every method of the :class:`LatticeBackend` protocol
defined in :mod:`agency.lattice.backends.base`.

Design notes:
- Async I/O via ``aiosqlite`` (single persistent connection + lock).
- WAL mode + foreign keys enforced on every connection.
- Event sourcing: every write appends an IMMUTABLE row to ``events``.
- Graph traversal: BFS via recursive CTEs (:meth:`traverse`); Python-side
  BFS for :meth:`find_path`.
- Governance: weighted quorum + reputation per spec sections 7.1-7.3.
- Vector search is intentionally degraded (no Qdrant): storage works,
  semantic search returns ``[]``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Any

import aiosqlite

from agency.lattice.models import (
    ConsensusProposal,
    EdgeType,
    LatticeConfig,
    LatticeEvent,
    NodeType,
    Reputation,
    Vote,
    ensure_utc,
    utc_now,
)

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

_SCHEMA_STATEMENTS: tuple[str, ...] = (
    """CREATE TABLE IF NOT EXISTS nodes (
        id TEXT PRIMARY KEY,
        node_type TEXT NOT NULL,
        properties TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS edges (
        id TEXT PRIMARY KEY,
        source_id TEXT NOT NULL,
        target_id TEXT NOT NULL,
        edge_type TEXT NOT NULL,
        properties TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY (source_id) REFERENCES nodes(id),
        FOREIGN KEY (target_id) REFERENCES nodes(id)
    )""",
    """CREATE TABLE IF NOT EXISTS events (
        id TEXT PRIMARY KEY,
        event_type TEXT NOT NULL,
        actor TEXT NOT NULL,
        target_id TEXT NOT NULL,
        payload TEXT NOT NULL,
        prior_state TEXT,
        timestamp TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS proposals (
        id TEXT PRIMARY KEY,
        proposer_id TEXT NOT NULL,
        proposal_type TEXT NOT NULL,
        payload TEXT NOT NULL,
        quorum REAL NOT NULL,
        status TEXT NOT NULL DEFAULT 'open',
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS votes (
        id TEXT PRIMARY KEY,
        proposal_id TEXT NOT NULL,
        voter_id TEXT NOT NULL,
        decision TEXT NOT NULL,
        evidence TEXT,
        weight REAL NOT NULL,
        timestamp TEXT NOT NULL,
        FOREIGN KEY (proposal_id) REFERENCES proposals(id)
    )""",
    """CREATE TABLE IF NOT EXISTS reputations (
        agent_id TEXT PRIMARY KEY,
        score REAL NOT NULL DEFAULT 0.5,
        tasks_completed INTEGER NOT NULL DEFAULT 0,
        tasks_failed INTEGER NOT NULL DEFAULT 0,
        peer_ratings TEXT NOT NULL DEFAULT '[]',
        last_updated TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS vectors (
        id TEXT PRIMARY KEY,
        collection TEXT NOT NULL,
        vector BLOB NOT NULL,
        payload TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS schema_version (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(node_type)",
    "CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id)",
    "CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id)",
    "CREATE INDEX IF NOT EXISTS idx_events_target ON events(target_id)",
    "CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_vectors_collection ON vectors(collection)",
)

_TERMINAL_TASK_STATES = frozenset(
    {"completed", "failed", "cancelled", "canceled", "done", "archived"}
)

_VALID_DIRECTIONS = frozenset({"in", "out", "both"})


def _new_id() -> str:
    """Return a new unique ID (uuid4 hex; ULID-compatible slot)."""

    return uuid.uuid4().hex


def _now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""

    return utc_now().isoformat()


def _as_value(value: NodeType | EdgeType | str) -> str:
    """Normalize an Enum-or-str to its plain string value."""

    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _parse_ts(raw: Any) -> datetime:
    """Parse an ISO 8601 timestamp (or datetime) into an aware UTC datetime."""

    if isinstance(raw, datetime):
        return ensure_utc(raw)
    return ensure_utc(datetime.fromisoformat(str(raw)))


def compute_reputation(rep: Reputation) -> float:
    """Compute a reputation score: 60% task success + 40% peer ratings.

    Implements SPEC_LATTICE.md section 7.2.
    """

    total = rep.tasks_completed + rep.tasks_failed
    if total == 0:
        return 0.5
    success_rate = rep.tasks_completed / total
    recent = rep.peer_ratings[-20:]
    peer_avg = sum(recent) / len(recent) if recent else 0.5
    return 0.6 * success_rate + 0.4 * peer_avg


class SQLiteLattice:
    """Default Lattice backend: full implementation, zero external services."""

    def __init__(self, config: LatticeConfig | str | None = None) -> None:
        if isinstance(config, LatticeConfig):
            self._config = config
            self._db_path = config.sqlite_path or "./data/lattice.db"
        elif isinstance(config, str):
            self._config = LatticeConfig(sqlite_path=config)
            self._db_path = config
        elif config is None:
            self._config = LatticeConfig()
            self._db_path = self._config.sqlite_path
        else:  # pragma: no cover - defensive
            raise TypeError(f"config must be LatticeConfig, str, or None: {config!r}")
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    # -- lifecycle ---------------------------------------------------- #

    async def initialize(self) -> None:
        """Open the connection, set pragmas, and run migrations (idempotent)."""

        try:
            async with self._lock:
                await self._open_locked()
                await self._migrate_locked()
        except Exception as exc:
            logger.exception("SQLiteLattice.initialize failed for %s", self._db_path)
            raise RuntimeError(f"Failed to initialize SQLiteLattice: {exc}") from exc

    async def close(self) -> None:
        """Release the connection. Safe to call more than once."""

        try:
            async with self._lock:
                if self._db is not None:
                    await self._db.close()
                    self._db = None
        except Exception as exc:
            logger.exception("SQLiteLattice.close failed for %s", self._db_path)
            raise RuntimeError(f"Failed to close SQLiteLattice: {exc}") from exc

    async def migrate(self) -> None:
        """Create tables/indexes when missing; record the schema version."""

        try:
            async with self._lock:
                await self._open_locked()
                await self._migrate_locked()
        except Exception as exc:
            logger.exception("SQLiteLattice.migrate failed for %s", self._db_path)
            raise RuntimeError(f"Failed to migrate SQLiteLattice: {exc}") from exc

    async def _open_locked(self) -> None:
        """Open the DB connection (caller must hold ``self._lock``)."""

        if self._db is not None:
            return
        if self._db_path != ":memory:":
            parent = os.path.dirname(os.path.abspath(self._db_path))
            if parent:
                os.makedirs(parent, exist_ok=True)
        db = await aiosqlite.connect(self._db_path)
        db.row_factory = sqlite3.Row
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")
        await db.commit()
        self._db = db

    async def _migrate_locked(self) -> None:
        """Run schema statements (caller must hold ``self._lock``)."""

        assert self._db is not None
        for statement in _SCHEMA_STATEMENTS:
            await self._db.execute(statement)
        await self._db.execute(
            "INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (?, ?)",
            (SCHEMA_VERSION, _now_iso()),
        )
        await self._db.commit()

    def _require_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("SQLiteLattice is not initialized; call initialize() first.")
        return self._db

    # -- internal helpers ---------------------------------------------- #

    async def _append_event(
        self,
        event_type: str,
        actor: str,
        target_id: str,
        payload: dict[str, Any] | None = None,
        prior_state: dict[str, Any] | None = None,
    ) -> str:
        """Append one immutable event row; returns the event ID."""

        db = self._require_db()
        event_id = _new_id()
        await db.execute(
            """INSERT INTO events (id, event_type, actor, target_id, payload,
                                   prior_state, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                event_id,
                event_type,
                actor,
                target_id,
                json.dumps(payload or {}),
                json.dumps(prior_state) if prior_state is not None else None,
                _now_iso(),
            ),
        )
        return event_id

    @staticmethod
    def _node_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "node_type": row["node_type"],
            "properties": json.loads(row["properties"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _edge_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        raw_props = row["properties"]
        return {
            "id": row["id"],
            "source_id": row["source_id"],
            "target_id": row["target_id"],
            "edge_type": row["edge_type"],
            "properties": json.loads(raw_props) if raw_props else {},
            "created_at": row["created_at"],
        }

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> LatticeEvent:
        return LatticeEvent(
            event_id=row["id"],
            timestamp=_parse_ts(row["timestamp"]),
            event_type=row["event_type"],
            actor=row["actor"],
            target_id=row["target_id"],
            payload=json.loads(row["payload"] or "{}"),
            prior_state=json.loads(row["prior_state"]) if row["prior_state"] else None,
        )

    # -- 4.1 node operations ------------------------------------------ #

    async def create_node(
        self,
        node_type: NodeType,
        properties: dict[str, Any],
        actor: str = "system",
    ) -> str:
        """Create a node; returns its ID and logs a ``node_created`` event."""

        try:
            async with self._lock:
                db = self._require_db()
                node_id = _new_id()
                now = _now_iso()
                await db.execute(
                    "INSERT INTO nodes (id, node_type, properties, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (node_id, _as_value(node_type), json.dumps(dict(properties)), now, now),
                )
                await self._append_event(
                    "node_created",
                    actor,
                    node_id,
                    {"node_type": _as_value(node_type), "properties": dict(properties)},
                )
                await db.commit()
                return node_id
        except Exception as exc:
            logger.exception("create_node failed for type %s", node_type)
            raise RuntimeError(f"create_node failed: {exc}") from exc

    async def get_node(self, node_id: str) -> dict[str, Any] | None:
        """Fetch a node by ID, or ``None`` when absent."""

        try:
            db = self._require_db()
            cursor = await db.execute("SELECT * FROM nodes WHERE id = ?", (node_id,))
            row = await cursor.fetchone()
            return self._node_to_dict(row) if row is not None else None
        except Exception as exc:
            logger.exception("get_node failed for %s", node_id)
            raise RuntimeError(f"get_node failed for {node_id}: {exc}") from exc

    async def update_node(
        self,
        node_id: str,
        properties: dict[str, Any],
        actor: str = "system",
    ) -> bool:
        """Merge ``properties`` into a node; logs event with ``prior_state``."""

        try:
            async with self._lock:
                db = self._require_db()
                cursor = await db.execute("SELECT * FROM nodes WHERE id = ?", (node_id,))
                row = await cursor.fetchone()
                if row is None:
                    return False
                prior_state: dict[str, Any] = json.loads(row["properties"])
                merged = {**prior_state, **dict(properties)}
                await db.execute(
                    "UPDATE nodes SET properties = ?, updated_at = ? WHERE id = ?",
                    (json.dumps(merged), _now_iso(), node_id),
                )
                await self._append_event(
                    "node_updated",
                    actor,
                    node_id,
                    {"properties": dict(properties)},
                    prior_state,
                )
                await db.commit()
                return True
        except Exception as exc:
            logger.exception("update_node failed for %s", node_id)
            raise RuntimeError(f"update_node failed for {node_id}: {exc}") from exc

    async def delete_node(self, node_id: str, actor: str = "system") -> bool:
        """Delete a node and its incident edges; returns ``True`` if removed."""

        try:
            async with self._lock:
                db = self._require_db()
                cursor = await db.execute("SELECT * FROM nodes WHERE id = ?", (node_id,))
                row = await cursor.fetchone()
                if row is None:
                    return False
                prior_state = self._node_to_dict(row)
                await db.execute(
                    "DELETE FROM edges WHERE source_id = ? OR target_id = ?", (node_id, node_id)
                )
                await db.execute("DELETE FROM nodes WHERE id = ?", (node_id,))
                await self._append_event("node_deleted", actor, node_id, {}, prior_state)
                await db.commit()
                return True
        except Exception as exc:
            logger.exception("delete_node failed for %s", node_id)
            raise RuntimeError(f"delete_node failed for {node_id}: {exc}") from exc

    async def find_nodes(
        self,
        node_type: NodeType | None = None,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List nodes by type + property filters (paginated)."""

        try:
            db = self._require_db()
            clauses: list[str] = []
            params: list[Any] = []
            if node_type is not None:
                clauses.append("node_type = ?")
                params.append(_as_value(node_type))
            for key, value in (filters or {}).items():
                if value is None:
                    clauses.append(f"json_extract(properties, '$.{key}') IS NULL")
                else:
                    clauses.append(f"json_extract(properties, '$.{key}') = ?")
                    params.append(value)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            cursor = await db.execute(
                f"SELECT * FROM nodes {where} ORDER BY created_at ASC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            )
            rows = await cursor.fetchall()
            return [self._node_to_dict(r) for r in rows]
        except Exception as exc:
            logger.exception("find_nodes failed for type %s", node_type)
            raise RuntimeError(f"find_nodes failed: {exc}") from exc

    # -- 4.2 edge operations ------------------------------------------ #

    async def add_edge(
        self,
        source_id: str,
        target_id: str,
        edge_type: EdgeType,
        properties: dict[str, Any] | None = None,
        actor: str = "system",
    ) -> str:
        """Create a relationship; returns its ID."""

        try:
            async with self._lock:
                db = self._require_db()
                edge_id = _new_id()
                await db.execute(
                    """INSERT INTO edges (id, source_id, target_id, edge_type,
                                          properties, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        edge_id,
                        source_id,
                        target_id,
                        _as_value(edge_type),
                        json.dumps(dict(properties or {})),
                        _now_iso(),
                    ),
                )
                await self._append_event(
                    "edge_added",
                    actor,
                    edge_id,
                    {
                        "source_id": source_id,
                        "target_id": target_id,
                        "edge_type": _as_value(edge_type),
                        "properties": dict(properties or {}),
                    },
                )
                await db.commit()
                return edge_id
        except Exception as exc:
            logger.exception("add_edge failed %s -> %s", source_id, target_id)
            raise RuntimeError(f"add_edge failed ({source_id} -> {target_id}): {exc}") from exc

    async def get_edges(
        self,
        node_id: str,
        direction: str = "both",
        edge_type: EdgeType | None = None,
    ) -> list[dict[str, Any]]:
        """Edges incident to ``node_id`` (``direction``: in|out|both)."""

        if direction not in _VALID_DIRECTIONS:
            raise ValueError(f"direction must be one of {sorted(_VALID_DIRECTIONS)}.")
        try:
            db = self._require_db()
            clauses: list[str] = []
            params: list[Any] = []
            if direction == "out":
                clauses.append("source_id = ?")
                params.append(node_id)
            elif direction == "in":
                clauses.append("target_id = ?")
                params.append(node_id)
            else:
                clauses.append("(source_id = ? OR target_id = ?)")
                params.extend([node_id, node_id])
            if edge_type is not None:
                clauses.append("edge_type = ?")
                params.append(_as_value(edge_type))
            cursor = await db.execute(
                f"SELECT * FROM edges WHERE {' AND '.join(clauses)} ORDER BY created_at ASC",
                tuple(params),
            )
            rows = await cursor.fetchall()
            return [self._edge_to_dict(r) for r in rows]
        except Exception as exc:
            logger.exception("get_edges failed for %s", node_id)
            raise RuntimeError(f"get_edges failed for {node_id}: {exc}") from exc

    async def remove_edge(self, edge_id: str, actor: str = "system") -> bool:
        """Delete a relationship; returns ``True`` when removed."""

        try:
            async with self._lock:
                db = self._require_db()
                cursor = await db.execute("SELECT * FROM edges WHERE id = ?", (edge_id,))
                row = await cursor.fetchone()
                if row is None:
                    return False
                prior_state = self._edge_to_dict(row)
                await db.execute("DELETE FROM edges WHERE id = ?", (edge_id,))
                await self._append_event("edge_removed", actor, edge_id, {}, prior_state)
                await db.commit()
                return True
        except Exception as exc:
            logger.exception("remove_edge failed for %s", edge_id)
            raise RuntimeError(f"remove_edge failed for {edge_id}: {exc}") from exc

    # -- 4.3 graph traversal ------------------------------------------ #

    async def traverse(
        self,
        start_node: str,
        edge_type: EdgeType | None = None,
        max_depth: int = 3,
        direction: str = "out",
    ) -> list[str]:
        """Node IDs reachable from ``start_node`` within ``max_depth`` (BFS)."""

        if direction not in _VALID_DIRECTIONS:
            raise ValueError(f"direction must be one of {sorted(_VALID_DIRECTIONS)}.")
        if max_depth < 1:
            return []
        try:
            db = self._require_db()
            etype = _as_value(edge_type) if edge_type is not None else None
            out_branch = (
                "SELECT e.target_id AS id, r.depth + 1 AS depth"
                " FROM reachable r JOIN edges e ON e.source_id = r.id"
                " WHERE r.depth < ? AND (? IS NULL OR e.edge_type = ?)"
            )
            in_branch = (
                "SELECT e.source_id AS id, r.depth + 1 AS depth"
                " FROM reachable r JOIN edges e ON e.target_id = r.id"
                " WHERE r.depth < ? AND (? IS NULL OR e.edge_type = ?)"
            )
            if direction == "out":
                body = out_branch
                params: tuple[Any, ...] = (max_depth, etype, etype)
            elif direction == "in":
                body = in_branch
                params = (max_depth, etype, etype)
            else:
                body = f"{out_branch} UNION {in_branch}"
                params = (max_depth, etype, etype, max_depth, etype, etype)
            query = (
                "WITH RECURSIVE reachable(id, depth) AS ("
                " SELECT ? AS id, 0 AS depth"
                f" UNION {body}"
                ") SELECT DISTINCT id FROM reachable WHERE id != ?"
            )
            cursor = await db.execute(query, (start_node, *params, start_node))
            rows = await cursor.fetchall()
            return [r["id"] for r in rows]
        except Exception as exc:
            logger.exception("traverse failed from %s", start_node)
            raise RuntimeError(f"traverse failed from {start_node}: {exc}") from exc

    async def find_path(
        self,
        source: str,
        target: str,
        max_depth: int = 5,
    ) -> list[str] | None:
        """Shortest directed path from ``source`` to ``target`` (BFS)."""

        if source == target:
            return [source]
        try:
            db = self._require_db()
            visited: set[str] = {source}
            queue: list[list[str]] = [[source]]
            depth = 0
            while queue and depth < max_depth:
                depth += 1
                next_level: list[list[str]] = []
                for path in queue:
                    cursor = await db.execute(
                        "SELECT target_id FROM edges WHERE source_id = ?", (path[-1],)
                    )
                    for row in await cursor.fetchall():
                        neighbour = row["target_id"]
                        if neighbour in visited:
                            continue
                        visited.add(neighbour)
                        candidate = [*path, neighbour]
                        if neighbour == target:
                            return candidate
                        next_level.append(candidate)
                queue = next_level
            return None
        except Exception as exc:
            logger.exception("find_path failed %s -> %s", source, target)
            raise RuntimeError(f"find_path failed ({source} -> {target}): {exc}") from exc

    async def get_dependencies(self, task_id: str) -> list[str]:
        """All tasks ``task_id`` depends on (``DEPENDS_ON``, outgoing)."""

        try:
            return await self.traverse(task_id, EdgeType.DEPENDS_ON, max_depth=100, direction="out")
        except Exception as exc:
            logger.exception("get_dependencies failed for %s", task_id)
            raise RuntimeError(f"get_dependencies failed for {task_id}: {exc}") from exc

    async def get_dependents(self, task_id: str) -> list[str]:
        """All tasks depending on ``task_id`` (``DEPENDS_ON``, incoming)."""

        try:
            return await self.traverse(task_id, EdgeType.DEPENDS_ON, max_depth=100, direction="in")
        except Exception as exc:
            logger.exception("get_dependents failed for %s", task_id)
            raise RuntimeError(f"get_dependents failed for {task_id}: {exc}") from exc

    # -- 4.4 vector search (degraded, no Qdrant) ----------------------- #

    async def upsert_vector(
        self,
        collection: str,
        vectors: list[tuple[str, list[float], dict[str, Any]]],
    ) -> bool:
        """Insert/replace ``(id, vector, payload)`` rows in ``collection``."""

        try:
            async with self._lock:
                db = self._require_db()
                for vector_id, vector, payload in vectors:
                    await db.execute(
                        """INSERT INTO vectors (id, collection, vector, payload)
                           VALUES (?, ?, ?, ?)
                           ON CONFLICT(id) DO UPDATE SET
                             collection = excluded.collection,
                             vector = excluded.vector,
                             payload = excluded.payload""",
                        (
                            vector_id,
                            collection,
                            json.dumps(list(vector)).encode("utf-8"),
                            json.dumps(dict(payload)),
                        ),
                    )
                await db.commit()
                return True
        except Exception as exc:
            logger.exception("upsert_vector failed for collection %s", collection)
            raise RuntimeError(f"upsert_vector failed for {collection}: {exc}") from exc

    async def search_vectors(
        self,
        collection: str,
        query_vector: list[float],
        limit: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Degraded without Qdrant: always returns ``[]``."""

        logger.debug(
            "search_vectors degraded (no Qdrant): collection=%s limit=%d", collection, limit
        )
        return []

    async def search_by_text(
        self,
        collection: str,
        text: str,
        embedder: Any,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Degraded without Qdrant: always returns ``[]``."""

        logger.debug(
            "search_by_text degraded (no Qdrant): collection=%s limit=%d", collection, limit
        )
        return []

    async def delete_vectors(self, collection: str, ids: list[str]) -> bool:
        """Remove ``ids`` from ``collection``."""

        try:
            async with self._lock:
                db = self._require_db()
                if not ids:
                    return True
                placeholders = ",".join("?" for _ in ids)
                await db.execute(
                    f"DELETE FROM vectors WHERE collection = ? AND id IN ({placeholders})",
                    (collection, *ids),
                )
                await db.commit()
                return True
        except Exception as exc:
            logger.exception("delete_vectors failed for collection %s", collection)
            raise RuntimeError(f"delete_vectors failed for {collection}: {exc}") from exc

    # -- 4.5 governance ----------------------------------------------- #

    async def submit_proposal(
        self,
        proposer_id: str,
        proposal_type: str,
        payload: dict[str, Any],
        quorum: float = 0.5,
        ttl_seconds: int = 3600,
    ) -> str:
        """Open a governance proposal; returns its ID."""

        try:
            async with self._lock:
                db = self._require_db()
                proposal_id = _new_id()
                now = utc_now()
                expires_at = now.fromtimestamp(now.timestamp() + ttl_seconds, tz=UTC).isoformat()
                await db.execute(
                    """INSERT INTO proposals (id, proposer_id, proposal_type, payload,
                                              quorum, status, created_at, expires_at)
                       VALUES (?, ?, ?, ?, ?, 'open', ?, ?)""",
                    (
                        proposal_id,
                        proposer_id,
                        proposal_type,
                        json.dumps(dict(payload)),
                        float(quorum),
                        now.isoformat(),
                        expires_at,
                    ),
                )
                await self._append_event(
                    "proposal_submitted",
                    proposer_id,
                    proposal_id,
                    {"proposal_type": proposal_type, **dict(payload)},
                )
                await db.commit()
                return proposal_id
        except Exception as exc:
            logger.exception("submit_proposal failed for %s", proposer_id)
            raise RuntimeError(f"submit_proposal failed: {exc}") from exc

    async def vote_weight(self, voter_id: str) -> float:
        """Voting power: Butler/User always 1.0, agents use reputation score."""

        if voter_id in ("butler", "user"):
            return 1.0
        rep = await self.get_reputation(voter_id)
        return rep.score

    async def cast_vote(
        self,
        voter_id: str,
        proposal_id: str,
        decision: str,
        evidence: list[str] | None = None,
    ) -> bool:
        """Record a vote (``approve``|``deny``|``abstain``); checks quorum."""

        if decision not in ("approve", "deny", "abstain"):
            raise ValueError(f"decision {decision!r} must be approve|deny|abstain.")
        try:
            async with self._lock:
                db = self._require_db()
                cursor = await db.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,))
                proposal = await cursor.fetchone()
                if proposal is None:
                    raise KeyError(f"Unknown proposal: {proposal_id}")
                if proposal["status"] != "open":
                    return False
                if _parse_ts(proposal["expires_at"]) <= utc_now():
                    await db.execute(
                        "UPDATE proposals SET status = 'expired' WHERE id = ?",
                        (proposal_id,),
                    )
                    await self._append_event(
                        "proposal_resolved", "system", proposal_id, {"status": "expired"}
                    )
                    await db.commit()
                    return False
                # Idempotent re-vote: replace any prior vote from this voter.
                await db.execute(
                    "DELETE FROM votes WHERE proposal_id = ? AND voter_id = ?",
                    (proposal_id, voter_id),
                )
                # NOTE: nested lock acquisition avoided on purpose; weight is
                # resolved inline so this block stays a single transaction.
                weight = 1.0
                if voter_id not in ("butler", "user"):
                    rep_cursor = await db.execute(
                        "SELECT * FROM reputations WHERE agent_id = ?", (voter_id,)
                    )
                    rep_row = await rep_cursor.fetchone()
                    if rep_row is not None:
                        weight = float(rep_row["score"])
                    else:
                        weight = 0.5
                await db.execute(
                    """INSERT INTO votes (id, proposal_id, voter_id, decision,
                                          evidence, weight, timestamp)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        _new_id(),
                        proposal_id,
                        voter_id,
                        decision,
                        json.dumps(list(evidence or [])),
                        weight,
                        _now_iso(),
                    ),
                )
                await self._append_event(
                    "vote_cast",
                    voter_id,
                    proposal_id,
                    {"decision": decision, "weight": weight},
                )
                await db.commit()
            await self.check_quorum(proposal_id)
            return True
        except (KeyError, ValueError):
            raise
        except Exception as exc:
            logger.exception("cast_vote failed for proposal %s", proposal_id)
            raise RuntimeError(f"cast_vote failed for {proposal_id}: {exc}") from exc

    async def get_proposal_status(self, proposal_id: str) -> ConsensusProposal:
        """Current proposal state (raises ``KeyError`` when unknown)."""

        try:
            db = self._require_db()
            cursor = await db.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,))
            row = await cursor.fetchone()
            if row is None:
                raise KeyError(f"Unknown proposal: {proposal_id}")
            vote_cursor = await db.execute(
                "SELECT * FROM votes WHERE proposal_id = ? ORDER BY timestamp ASC",
                (proposal_id,),
            )
            votes = [
                Vote(
                    voter_id=r["voter_id"],
                    proposal_id=r["proposal_id"],
                    decision=r["decision"],
                    evidence=json.loads(r["evidence"] or "[]"),
                    weight=float(r["weight"]),
                    timestamp=_parse_ts(r["timestamp"]),
                )
                for r in await vote_cursor.fetchall()
            ]
            return ConsensusProposal(
                proposal_id=row["id"],
                proposer_id=row["proposer_id"],
                proposal_type=row["proposal_type"],
                quorum_required=float(row["quorum"]),
                votes=votes,
                status=row["status"],
                expires_at=_parse_ts(row["expires_at"]),
            )
        except KeyError:
            raise
        except Exception as exc:
            logger.exception("get_proposal_status failed for %s", proposal_id)
            raise RuntimeError(f"get_proposal_status failed for {proposal_id}: {exc}") from exc

    async def list_open_proposals(self) -> list[ConsensusProposal]:
        """Return open proposals and their votes using the existing decoder."""
        db = self._require_db()
        cursor = await db.execute("SELECT id FROM proposals WHERE status = 'open' ORDER BY id")
        return [await self.get_proposal_status(row["id"]) for row in await cursor.fetchall()]

    async def check_quorum(self, proposal_id: str) -> bool:
        """Whether a proposal reached quorum (resolves it when it does)."""

        try:
            async with self._lock:
                db = self._require_db()
                cursor = await db.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,))
                row = await cursor.fetchone()
                if row is None:
                    raise KeyError(f"Unknown proposal: {proposal_id}")
                if row["status"] != "open":
                    return bool(row["status"] == "passed")
                if _parse_ts(row["expires_at"]) <= utc_now():
                    await db.execute(
                        "UPDATE proposals SET status = 'expired' WHERE id = ?",
                        (proposal_id,),
                    )
                    await self._append_event(
                        "proposal_resolved", "system", proposal_id, {"status": "expired"}
                    )
                    await db.commit()
                    return False
                quorum = float(row["quorum"])
                vote_cursor = await db.execute(
                    "SELECT decision, weight FROM votes WHERE proposal_id = ?",
                    (proposal_id,),
                )
                approve = 0.0
                deny = 0.0
                for vote_row in await vote_cursor.fetchall():
                    if vote_row["decision"] == "approve":
                        approve += float(vote_row["weight"])
                    elif vote_row["decision"] == "deny":
                        deny += float(vote_row["weight"])
                counted = approve + deny
                if counted <= 0:
                    return False
                if approve / counted >= quorum:
                    await db.execute(
                        "UPDATE proposals SET status = 'passed' WHERE id = ?",
                        (proposal_id,),
                    )
                    await self._append_event(
                        "proposal_resolved",
                        "system",
                        proposal_id,
                        {"status": "passed", "approve": approve, "deny": deny},
                    )
                    await db.commit()
                    return True
                if deny / counted >= quorum:
                    await db.execute(
                        "UPDATE proposals SET status = 'denied' WHERE id = ?",
                        (proposal_id,),
                    )
                    await self._append_event(
                        "proposal_resolved",
                        "system",
                        proposal_id,
                        {"status": "denied", "approve": approve, "deny": deny},
                    )
                    await db.commit()
                    return False
                return False
        except KeyError:
            raise
        except Exception as exc:
            logger.exception("check_quorum failed for %s", proposal_id)
            raise RuntimeError(f"check_quorum failed for {proposal_id}: {exc}") from exc

    async def update_reputation(
        self,
        agent_id: str,
        success: bool,
        peer_rating: float | None = None,
    ) -> Reputation:
        """Fold a task outcome (+ optional peer rating) into reputation."""

        try:
            async with self._lock:
                db = self._require_db()
                cursor = await db.execute(
                    "SELECT * FROM reputations WHERE agent_id = ?", (agent_id,)
                )
                row = await cursor.fetchone()
                if row is None:
                    rep = Reputation(agent_id=agent_id)
                else:
                    rep = Reputation(
                        agent_id=row["agent_id"],
                        score=float(row["score"]),
                        tasks_completed=int(row["tasks_completed"]),
                        tasks_failed=int(row["tasks_failed"]),
                        peer_ratings=json.loads(row["peer_ratings"] or "[]"),
                        last_updated=_parse_ts(row["last_updated"]),
                    )
                if success:
                    rep.tasks_completed += 1
                else:
                    rep.tasks_failed += 1
                if peer_rating is not None:
                    rep.peer_ratings.append(float(peer_rating))
                    rep.peer_ratings = rep.peer_ratings[-100:]
                rep.score = compute_reputation(rep)
                rep.last_updated = utc_now()
                await db.execute(
                    """INSERT INTO reputations (agent_id, score, tasks_completed,
                                                tasks_failed, peer_ratings, last_updated)
                       VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(agent_id) DO UPDATE SET
                         score = excluded.score,
                         tasks_completed = excluded.tasks_completed,
                         tasks_failed = excluded.tasks_failed,
                         peer_ratings = excluded.peer_ratings,
                         last_updated = excluded.last_updated""",
                    (
                        agent_id,
                        rep.score,
                        rep.tasks_completed,
                        rep.tasks_failed,
                        json.dumps(rep.peer_ratings),
                        rep.last_updated.isoformat(),
                    ),
                )
                await self._append_event(
                    "reputation_updated",
                    "system",
                    agent_id,
                    {"success": success, "score": rep.score},
                )
                await db.commit()
                return rep
        except Exception as exc:
            logger.exception("update_reputation failed for %s", agent_id)
            raise RuntimeError(f"update_reputation failed for {agent_id}: {exc}") from exc

    async def get_reputation(self, agent_id: str) -> Reputation:
        """Reputation for ``agent_id`` (neutral baseline when unknown)."""

        try:
            db = self._require_db()
            cursor = await db.execute("SELECT * FROM reputations WHERE agent_id = ?", (agent_id,))
            row = await cursor.fetchone()
            if row is None:
                return Reputation(agent_id=agent_id)
            return Reputation(
                agent_id=row["agent_id"],
                score=float(row["score"]),
                tasks_completed=int(row["tasks_completed"]),
                tasks_failed=int(row["tasks_failed"]),
                peer_ratings=json.loads(row["peer_ratings"] or "[]"),
                last_updated=_parse_ts(row["last_updated"]),
            )
        except Exception as exc:
            logger.exception("get_reputation failed for %s", agent_id)
            raise RuntimeError(f"get_reputation failed for {agent_id}: {exc}") from exc

    # -- 4.6 event log ------------------------------------------------ #

    async def get_events(
        self,
        target_id: str | None = None,
        event_type: str | None = None,
        actor: str | None = None,
        limit: int = 100,
    ) -> list[LatticeEvent]:
        """Query the event log, newest first."""

        try:
            db = self._require_db()
            clauses: list[str] = []
            params: list[Any] = []
            if target_id is not None:
                clauses.append("target_id = ?")
                params.append(target_id)
            if event_type is not None:
                clauses.append("event_type = ?")
                params.append(event_type)
            if actor is not None:
                clauses.append("actor = ?")
                params.append(actor)
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            cursor = await db.execute(
                f"SELECT * FROM events {where} ORDER BY timestamp DESC LIMIT ?",
                (*params, limit),
            )
            return [self._row_to_event(r) for r in await cursor.fetchall()]
        except Exception as exc:
            logger.exception("get_events failed")
            raise RuntimeError(f"get_events failed: {exc}") from exc

    async def replay_events(
        self,
        from_timestamp: datetime,
        to_timestamp: datetime | None = None,
    ) -> list[LatticeEvent]:
        """Events in ``[from_timestamp, to_timestamp]``, oldest first."""

        try:
            db = self._require_db()
            start = ensure_utc(from_timestamp).isoformat()
            if to_timestamp is None:
                cursor = await db.execute(
                    "SELECT * FROM events WHERE timestamp >= ? ORDER BY timestamp ASC",
                    (start,),
                )
            else:
                end = ensure_utc(to_timestamp).isoformat()
                cursor = await db.execute(
                    "SELECT * FROM events WHERE timestamp >= ? AND timestamp <= ?"
                    " ORDER BY timestamp ASC",
                    (start, end),
                )
            return [self._row_to_event(r) for r in await cursor.fetchall()]
        except Exception as exc:
            logger.exception("replay_events failed")
            raise RuntimeError(f"replay_events failed: {exc}") from exc

    async def get_audit_trail(self, node_id: str) -> list[LatticeEvent]:
        """Full history for ``node_id`` (incl. its incident edges), oldest first."""

        try:
            db = self._require_db()
            cursor = await db.execute(
                "SELECT id FROM edges WHERE source_id = ? OR target_id = ?",
                (node_id, node_id),
            )
            edge_ids = [r["id"] for r in await cursor.fetchall()]
            targets = [node_id, *edge_ids]
            placeholders = ",".join("?" for _ in targets)
            cursor = await db.execute(
                f"SELECT * FROM events WHERE target_id IN ({placeholders}) ORDER BY timestamp ASC",
                tuple(targets),
            )
            return [self._row_to_event(r) for r in await cursor.fetchall()]
        except Exception as exc:
            logger.exception("get_audit_trail failed for %s", node_id)
            raise RuntimeError(f"get_audit_trail failed for {node_id}: {exc}") from exc

    # -- 4.7 health & status ------------------------------------------ #

    async def _count(self, query: str, params: tuple[Any, ...] = ()) -> int:
        db = self._require_db()
        cursor = await db.execute(query, params)
        row = await cursor.fetchone()
        return int(row[0]) if row is not None else 0

    async def get_status(self) -> dict[str, Any]:
        """Backend, counts, pending proposals, last event, storage size."""

        try:
            node_count = await self._count("SELECT COUNT(*) FROM nodes")
            edge_count = await self._count("SELECT COUNT(*) FROM edges")
            event_count = await self._count("SELECT COUNT(*) FROM events")
            agent_count = await self._count(
                "SELECT COUNT(*) FROM nodes WHERE node_type = ?", ("agent",)
            )
            task_count = await self._count(
                "SELECT COUNT(*) FROM nodes WHERE node_type = ?", ("task",)
            )
            pending = await self._count("SELECT COUNT(*) FROM proposals WHERE status = 'open'")
            db = self._require_db()
            cursor = await db.execute("SELECT MAX(timestamp) FROM events")
            last_row = await cursor.fetchone()
            last_event_at = last_row[0] if last_row is not None else None
            if self._db_path == ":memory:" or not os.path.exists(self._db_path):
                storage_mb = 0.0
            else:
                storage_mb = os.path.getsize(self._db_path) / (1024 * 1024)
            return {
                "backend": "sqlite",
                "node_count": node_count,
                "edge_count": edge_count,
                "event_count": event_count,
                "agent_count": agent_count,
                "task_count": task_count,
                "pending_proposals": pending,
                "last_event_at": last_event_at,
                "storage_size_mb": storage_mb,
            }
        except Exception as exc:
            logger.exception("get_status failed")
            raise RuntimeError(f"get_status failed: {exc}") from exc

    async def get_agent_count(self) -> int:
        """Number of ``AGENT`` nodes."""

        try:
            return await self._count("SELECT COUNT(*) FROM nodes WHERE node_type = ?", ("agent",))
        except Exception as exc:
            logger.exception("get_agent_count failed")
            raise RuntimeError(f"get_agent_count failed: {exc}") from exc

    async def get_active_tasks(self) -> list[dict[str, Any]]:
        """Task nodes not in a terminal state."""

        try:
            tasks = await self.find_nodes(NodeType.TASK, limit=10_000)
            active: list[dict[str, Any]] = []
            for task in tasks:
                status = str(task["properties"].get("status", "")).lower()
                if status not in _TERMINAL_TASK_STATES:
                    active.append(task)
            return active
        except Exception as exc:
            logger.exception("get_active_tasks failed")
            raise RuntimeError(f"get_active_tasks failed: {exc}") from exc


__all__ = ["SCHEMA_VERSION", "SQLiteLattice", "compute_reputation"]
