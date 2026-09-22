"""Unified Lattice API — the single entry point for all components.

Agents never touch backends directly; they call :class:`Lattice`.

The Lattice delegates graph, vector, governance, and event operations to a
:class:`~agency.lattice.backends.base.LatticeBackend`. Concrete backends
(``SQLiteLattice``, ``Neo4jLattice``) are used when their modules are importable
and configured; otherwise an in-memory backend is used so core operation needs
zero external dependencies. Vector search degrades gracefully: when no vectors
or embedder are available it returns an empty list instead of raising.
"""

from __future__ import annotations

import math
import uuid
from collections import deque
from datetime import datetime, timedelta
from typing import Any

import structlog

from agency.lattice.backends.base import Embedder, LatticeBackend
from agency.lattice.config import get_config
from agency.lattice.models import (
    ConsensusProposal,
    EdgeType,
    LatticeConfig,
    LatticeEvent,
    NodeType,
    Reputation,
    Vote,
    utc_now,
)

log = structlog.get_logger(__name__)

try:  # Prefer the real engines when their briefs have landed.
    from agency.lattice.governance import GovernanceEngine as _RealGovernanceEngine
except ImportError:  # pragma: no cover - fallback stub below
    _RealGovernanceEngine = None  # type: ignore[assignment]

try:
    from agency.lattice.reputation import ReputationEngine as _RealReputationEngine
except ImportError:  # pragma: no cover - fallback stub below
    _RealReputationEngine = None  # type: ignore[assignment]

_TERMINAL_TASK_STATES: frozenset[str] = frozenset(
    {"completed", "failed", "cancelled", "denied", "expired"}
)


def _new_id(prefix: str = "") -> str:
    """Generate a unique hex ID with an optional prefix."""

    token = uuid.uuid4().hex
    return f"{prefix}{token}" if prefix else token


def _coerce_node_type(node_type: NodeType | str) -> NodeType:
    if isinstance(node_type, NodeType):
        return node_type
    return NodeType(str(node_type))


def _coerce_edge_type(edge_type: EdgeType | str) -> EdgeType:
    if isinstance(edge_type, EdgeType):
        return edge_type
    return EdgeType(str(edge_type))


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    denom = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    if denom == 0.0:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / denom


class InMemoryLatticeBackend:
    """Zero-dependency fallback backend implementing the full protocol.

    Used when no concrete backend module (SQLite/Neo4j) is importable.
    Events are recorded for every mutating operation.
    """

    def __init__(self, config: LatticeConfig | None = None) -> None:
        self._config: LatticeConfig = config or LatticeConfig()
        self._nodes: dict[str, dict[str, Any]] = {}
        self._edges: dict[str, dict[str, Any]] = {}
        self._events: list[LatticeEvent] = []
        self._proposals: dict[str, ConsensusProposal] = {}
        self._reputations: dict[str, Reputation] = {}
        self._vectors: dict[str, dict[str, tuple[list[float], dict[str, Any]]]] = {}
        self._initialized: bool = False

    # -- lifecycle ---------------------------------------------------- #
    async def initialize(self) -> None:
        """Mark the backend ready (idempotent, nothing to connect)."""

        self._initialized = True
        log.info("lattice.backend.initialized", backend="memory")

    async def close(self) -> None:
        """Release resources (safe to call more than once)."""

        self._initialized = False
        log.info("lattice.backend.closed", backend="memory")

    # -- internal helpers --------------------------------------------- #
    def _record(
        self,
        event_type: str,
        actor: str,
        target_id: str,
        payload: dict[str, Any] | None = None,
        prior_state: dict[str, Any] | None = None,
    ) -> LatticeEvent:
        event = LatticeEvent(
            event_id=_new_id("evt-"),
            timestamp=utc_now(),
            event_type=event_type,
            actor=actor,
            target_id=target_id,
            payload=dict(payload or {}),
            prior_state=dict(prior_state) if prior_state is not None else None,
        )
        self._events.append(event)
        return event

    # -- 4.1 node operations ------------------------------------------ #
    async def create_node(
        self,
        node_type: NodeType,
        properties: dict[str, Any],
        actor: str = "system",
    ) -> str:
        """Create a node; returns its ID and logs a ``node_created`` event."""

        node_type = _coerce_node_type(node_type)
        node_id = _new_id("n-")
        now = utc_now().isoformat()
        self._nodes[node_id] = {
            "id": node_id,
            "node_type": node_type.value,
            "type": node_type.value,
            "properties": dict(properties),
            "created_at": now,
            "updated_at": now,
        }
        self._record(
            "node_created",
            actor,
            node_id,
            {"node_type": node_type.value, **dict(properties)},
        )
        return node_id

    async def get_node(self, node_id: str) -> dict[str, Any] | None:
        """Fetch a node by ID, or ``None`` when absent."""

        node = self._nodes.get(node_id)
        return dict(node) if node is not None else None

    async def update_node(
        self,
        node_id: str,
        properties: dict[str, Any],
        actor: str = "system",
    ) -> bool:
        """Merge ``properties`` into a node; logs event with ``prior_state``."""

        node = self._nodes.get(node_id)
        if node is None:
            return False
        prior = dict(node["properties"])
        node["properties"] = {**prior, **dict(properties)}
        node["updated_at"] = utc_now().isoformat()
        self._record("node_updated", actor, node_id, dict(properties), prior)
        return True

    async def delete_node(self, node_id: str, actor: str = "system") -> bool:
        """Delete a node; returns ``True`` when a row was removed."""

        node = self._nodes.pop(node_id, None)
        if node is None:
            return False
        for edge_id in [
            eid
            for eid, edge in self._edges.items()
            if edge["source_id"] == node_id or edge["target_id"] == node_id
        ]:
            del self._edges[edge_id]
        self._record("node_deleted", actor, node_id, {"node_type": node["type"]})
        return True

    async def find_nodes(
        self,
        node_type: NodeType | None = None,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List nodes by type + property filters (paginated)."""

        wanted = _coerce_node_type(node_type).value if node_type is not None else None
        results: list[dict[str, Any]] = []
        for node in self._nodes.values():
            if wanted is not None and node["type"] != wanted:
                continue
            if filters and any(
                node["properties"].get(key) != value for key, value in filters.items()
            ):
                continue
            results.append(dict(node))
        return results[offset : offset + limit]

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

        edge_type = _coerce_edge_type(edge_type)
        edge_id = _new_id("e-")
        self._edges[edge_id] = {
            "id": edge_id,
            "edge_id": edge_id,
            "source_id": source_id,
            "target_id": target_id,
            "type": edge_type.value,
            "edge_type": edge_type.value,
            "properties": dict(properties or {}),
            "created_at": utc_now().isoformat(),
        }
        self._record(
            "edge_added",
            actor,
            edge_id,
            {"source_id": source_id, "target_id": target_id,
             "edge_type": edge_type.value},
        )
        return edge_id

    async def get_edges(
        self,
        node_id: str,
        direction: str = "both",
        edge_type: EdgeType | None = None,
    ) -> list[dict[str, Any]]:
        """Edges incident to ``node_id`` (``direction``: in|out|both)."""

        wanted = _coerce_edge_type(edge_type).value if edge_type is not None else None
        results: list[dict[str, Any]] = []
        for edge in self._edges.values():
            if wanted is not None and edge["type"] != wanted:
                continue
            is_source = edge["source_id"] == node_id
            is_target = edge["target_id"] == node_id
            if direction == "out" and not is_source:
                continue
            if direction == "in" and not is_target:
                continue
            if direction not in ("out", "in", "both"):
                continue
            if direction == "both" and not (is_source or is_target):
                continue
            results.append(dict(edge))
        return results

    async def remove_edge(self, edge_id: str, actor: str = "system") -> bool:
        """Delete a relationship; returns ``True`` when removed."""

        if edge_id not in self._edges:
            return False
        del self._edges[edge_id]
        self._record("edge_removed", actor, edge_id, {})
        return True

    # -- 4.3 graph traversal ------------------------------------------ #
    def _neighbors(
        self,
        node_id: str,
        edge_type: EdgeType | None,
        direction: str,
    ) -> list[str]:
        wanted = _coerce_edge_type(edge_type).value if edge_type is not None else None
        found: list[str] = []
        for edge in self._edges.values():
            if wanted is not None and edge["type"] != wanted:
                continue
            if direction in ("out", "both") and edge["source_id"] == node_id:
                found.append(edge["target_id"])
            elif direction in ("in", "both") and edge["target_id"] == node_id:
                found.append(edge["source_id"])
        return found

    async def traverse(
        self,
        start_node: str,
        edge_type: EdgeType | None = None,
        max_depth: int = 3,
        direction: str = "out",
    ) -> list[str]:
        """Node IDs reachable from ``start_node`` within ``max_depth``."""

        seen: list[str] = []
        visited: set[str] = {start_node}
        frontier: list[tuple[str, int]] = [(start_node, 0)]
        while frontier:
            current, depth = frontier.pop(0)
            if depth >= max_depth:
                continue
            for neighbor in self._neighbors(current, edge_type, direction):
                if neighbor not in visited:
                    visited.add(neighbor)
                    seen.append(neighbor)
                    frontier.append((neighbor, depth + 1))
        return seen

    async def find_path(
        self,
        source: str,
        target: str,
        max_depth: int = 5,
    ) -> list[str] | None:
        """Shortest path from ``source`` to ``target`` (``None`` if none)."""

        if source == target:
            return [source]
        queue: deque[list[str]] = deque([[source]])
        visited: set[str] = {source}
        while queue:
            path = queue.popleft()
            if len(path) - 1 >= max_depth:
                continue
            for neighbor in self._neighbors(path[-1], None, "out"):
                if neighbor in visited:
                    continue
                candidate = [*path, neighbor]
                if neighbor == target:
                    return candidate
                visited.add(neighbor)
                queue.append(candidate)
        return None

    async def get_dependencies(self, task_id: str) -> list[str]:
        """All tasks ``task_id`` depends on (``DEPENDS_ON``, outgoing)."""

        return await self.traverse(
            task_id, EdgeType.DEPENDS_ON, max_depth=100, direction="out"
        )

    async def get_dependents(self, task_id: str) -> list[str]:
        """All tasks depending on ``task_id`` (``DEPENDS_ON``, incoming)."""

        return await self.traverse(
            task_id, EdgeType.DEPENDS_ON, max_depth=100, direction="in"
        )

    # -- 4.4 vector search -------------------------------------------- #
    async def upsert_vector(
        self,
        collection: str,
        vectors: list[tuple[str, list[float], dict[str, Any]]],
    ) -> bool:
        """Insert/replace ``(id, vector, payload)`` rows in ``collection``."""

        store = self._vectors.setdefault(collection, {})
        for vector_id, vector, payload in vectors:
            store[vector_id] = (list(vector), dict(payload))
        return True

    async def search_vectors(
        self,
        collection: str,
        query_vector: list[float],
        limit: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Nearest-neighbour search; returns ``{id, score, payload}`` dicts."""

        try:
            store = self._vectors.get(collection, {})
            scored: list[dict[str, Any]] = []
            for vector_id, (vector, payload) in store.items():
                if filters and any(
                    payload.get(key) != value for key, value in filters.items()
                ):
                    continue
                scored.append(
                    {
                        "id": vector_id,
                        "score": _cosine_similarity(query_vector, vector),
                        "payload": dict(payload),
                    }
                )
            scored.sort(key=lambda item: item["score"], reverse=True)
            return scored[:limit]
        except Exception:  # noqa: BLE001 - graceful degradation
            log.warning("lattice.vectors.search_failed", collection=collection)
            return []

    async def search_by_text(
        self,
        collection: str,
        text: str,
        embedder: Embedder,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Embed ``text`` with ``embedder`` then run :meth:`search_vectors`."""

        try:
            query_vector = await embedder(text)
            return await self.search_vectors(collection, query_vector, limit=limit)
        except Exception:  # noqa: BLE001 - graceful degradation
            log.warning("lattice.vectors.text_search_failed", collection=collection)
            return []

    async def delete_vectors(self, collection: str, ids: list[str]) -> bool:
        """Remove ``ids`` from ``collection``."""

        store = self._vectors.get(collection, {})
        removed = False
        for vector_id in ids:
            if store.pop(vector_id, None) is not None:
                removed = True
        return removed

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

        proposal_id = _new_id("prop-")
        proposal = ConsensusProposal(
            proposal_id=proposal_id,
            proposer_id=proposer_id,
            proposal_type=proposal_type,
            quorum_required=quorum,
            votes=[],
            status="open",
            expires_at=utc_now() + timedelta(seconds=ttl_seconds),
        )
        self._proposals[proposal_id] = proposal
        self._record(
            "proposal_submitted",
            proposer_id,
            proposal_id,
            {"proposal_type": proposal_type, **dict(payload)},
        )
        return proposal_id

    async def cast_vote(
        self,
        voter_id: str,
        proposal_id: str,
        decision: str,
        evidence: list[str] | None = None,
    ) -> bool:
        """Record a vote (``approve``|``deny``|``abstain``); checks quorum."""

        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            raise KeyError(f"Unknown proposal: {proposal_id}")
        proposal.votes.append(
            Vote(
                voter_id=voter_id,
                proposal_id=proposal_id,
                decision=decision,
                evidence=list(evidence or []),
            )
        )
        self._record(
            "vote_cast", voter_id, proposal_id, {"decision": decision}
        )
        await self.check_quorum(proposal_id)
        return True

    async def get_proposal_status(self, proposal_id: str) -> ConsensusProposal:
        """Current proposal state (raises ``KeyError`` when unknown)."""

        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            raise KeyError(f"Unknown proposal: {proposal_id}")
        return proposal

    async def list_open_proposals(self) -> list[ConsensusProposal]:
        """Return all proposals with status='open'."""
        return [p for p in self._proposals.values() if p.status == "open"]

    async def update_reputation(
        self,
        agent_id: str,
        success: bool,
        peer_rating: float | None = None,
    ) -> Reputation:
        """Fold a task outcome (+ optional peer rating) into reputation."""

        current = self._reputations.get(agent_id) or Reputation(agent_id=agent_id)
        completed = current.tasks_completed + (1 if success else 0)
        failed = current.tasks_failed + (0 if success else 1)
        score = current.score
        if success:
            score = min(1.0, score + 0.05 * (1.0 - score) + 0.01)
        else:
            score = max(0.0, score - 0.05 * score - 0.01)
        ratings = list(current.peer_ratings)
        if peer_rating is not None:
            ratings.append(float(peer_rating))
            window = max(1, self._config.reputation_window)
            ratings = ratings[-window:]
            score = max(0.0, min(1.0, 0.7 * score + 0.3 * (sum(ratings) / len(ratings))))
        updated = Reputation(
            agent_id=agent_id,
            score=score,
            tasks_completed=completed,
            tasks_failed=failed,
            peer_ratings=ratings,
            last_updated=utc_now(),
        )
        self._reputations[agent_id] = updated
        self._record(
            "reputation_updated",
            agent_id,
            agent_id,
            {"success": success, "score": updated.score},
        )
        return updated

    async def get_reputation(self, agent_id: str) -> Reputation:
        """Reputation for ``agent_id`` (neutral baseline when unknown)."""

        existing = self._reputations.get(agent_id)
        if existing is not None:
            return existing
        return Reputation(agent_id=agent_id)

    async def check_quorum(self, proposal_id: str) -> bool:
        """Whether a proposal has reached quorum (resolves it if so)."""

        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            raise KeyError(f"Unknown proposal: {proposal_id}")
        if proposal.status != "open":
            return proposal.status == "passed"
        if utc_now() >= proposal.expires_at:
            proposal.status = "expired"
            self._record("proposal_expired", "system", proposal_id, {})
            return False
        counted = [v for v in proposal.votes if v.decision in ("approve", "deny")]
        total_weight = sum(v.weight for v in counted)
        if total_weight <= 0:
            return False
        approve_weight = sum(v.weight for v in counted if v.decision == "approve")
        deny_weight = total_weight - approve_weight
        if approve_weight / total_weight >= proposal.quorum_required:
            proposal.status = "passed"
            self._record("proposal_passed", "system", proposal_id, {})
            return True
        if deny_weight / total_weight >= proposal.quorum_required:
            proposal.status = "denied"
            self._record("proposal_denied", "system", proposal_id, {})
        return False

    # -- 4.6 event log ------------------------------------------------ #
    async def get_events(
        self,
        target_id: str | None = None,
        event_type: str | None = None,
        actor: str | None = None,
        limit: int = 100,
    ) -> list[LatticeEvent]:
        """Query the event log, newest first."""

        results = [
            event
            for event in reversed(self._events)
            if (target_id is None or event.target_id == target_id)
            and (event_type is None or event.event_type == event_type)
            and (actor is None or event.actor == actor)
        ]
        return results[:limit]

    async def replay_events(
        self,
        from_timestamp: datetime,
        to_timestamp: datetime | None = None,
    ) -> list[LatticeEvent]:
        """Events in ``[from_timestamp, to_timestamp]``, oldest first."""

        return [
            event
            for event in self._events
            if event.timestamp >= from_timestamp
            and (to_timestamp is None or event.timestamp <= to_timestamp)
        ]

    async def get_audit_trail(self, node_id: str) -> list[LatticeEvent]:
        """Full history for ``node_id``, oldest first."""

        return [event for event in self._events if event.target_id == node_id]

    # -- 4.7 health & status ------------------------------------------ #
    async def get_status(self) -> dict[str, Any]:
        """Backend, counts, pending proposals, last event, storage size."""

        agent_count = await self.get_agent_count()
        active_tasks = await self.get_active_tasks()
        pending = sum(
            1 for proposal in self._proposals.values() if proposal.status == "open"
        )
        last_event = self._events[-1].to_dict() if self._events else None
        return {
            "backend": "memory",
            "initialized": self._initialized,
            "node_count": len(self._nodes),
            "edge_count": len(self._edges),
            "agent_count": agent_count,
            "task_count": len(
                [n for n in self._nodes.values() if n["node_type"] == "task"]
            ),
            "active_task_count": len(active_tasks),
            "pending_proposals": pending,
            "event_count": len(self._events),
            "last_event": last_event,
            "last_event_at": last_event["timestamp"] if last_event else None,
            "storage_size_mb": 0.0,
        }

    async def get_agent_count(self) -> int:
        """Number of ``AGENT`` nodes."""

        return sum(
            1 for node in self._nodes.values() if node["type"] == NodeType.AGENT.value
        )

    async def get_active_tasks(self) -> list[dict[str, Any]]:
        """Task nodes not in a terminal state."""

        return [
            dict(node)
            for node in self._nodes.values()
            if node["type"] == NodeType.TASK.value
            and str(node["properties"].get("status", "open")).lower()
            not in _TERMINAL_TASK_STATES
        ]


class GovernanceEngine:
    """Governance operations bound to a backend.

    Thin wrapper so ``Lattice.governance`` exposes proposal/vote flows
    without callers touching the backend directly.
    """

    def __init__(self, backend: LatticeBackend) -> None:
        self._backend: LatticeBackend = backend

    async def submit_proposal(
        self,
        proposer_id: str,
        proposal_type: str,
        payload: dict[str, Any],
        quorum: float = 0.5,
        ttl_seconds: int = 3600,
    ) -> str:
        """Open a governance proposal; returns its ID."""

        return await self._backend.submit_proposal(
            proposer_id, proposal_type, payload, quorum, ttl_seconds
        )

    async def cast_vote(
        self,
        voter_id: str,
        proposal_id: str,
        decision: str,
        evidence: list[str] | None = None,
    ) -> bool:
        """Record a vote on a proposal."""

        return await self._backend.cast_vote(voter_id, proposal_id, decision, evidence)

    async def get_proposal_status(self, proposal_id: str) -> ConsensusProposal:
        """Current proposal state."""

        return await self._backend.get_proposal_status(proposal_id)

    async def check_quorum(self, proposal_id: str) -> bool:
        """Whether a proposal has reached quorum."""

        return await self._backend.check_quorum(proposal_id)


class ReputationEngine:
    """Reputation operations bound to a backend."""

    def __init__(self, backend: LatticeBackend) -> None:
        self._backend: LatticeBackend = backend

    async def update_reputation(
        self,
        agent_id: str,
        success: bool,
        peer_rating: float | None = None,
    ) -> Reputation:
        """Fold a task outcome into an agent's reputation."""

        return await self._backend.update_reputation(agent_id, success, peer_rating)

    async def get_reputation(self, agent_id: str) -> Reputation:
        """Reputation for ``agent_id``."""

        return await self._backend.get_reputation(agent_id)


class Lattice:
    """Unified Lattice API — the single entry point for all components.

    Agents never touch backends directly; they call this.
    """

    def __init__(self, config: LatticeConfig | None = None) -> None:
        self.config: LatticeConfig = config or get_config()
        self.backend: LatticeBackend = self._create_backend()
        governance_cls = _RealGovernanceEngine or GovernanceEngine
        reputation_cls = _RealReputationEngine or ReputationEngine
        self.governance = governance_cls(self.backend)  # type: ignore[operator]
        self.reputation = reputation_cls(self.backend)  # type: ignore[operator]
        log.info("lattice.api.created", backend=self.config.backend)

    def _create_backend(self) -> LatticeBackend:
        """Instantiate the configured backend, falling back to memory."""

        backend_name = (self.config.backend or "sqlite").strip().lower()
        if backend_name == "sqlite":
            try:
                from agency.lattice.backends.sqlite import SQLiteLattice

                log.info("lattice.api.backend_selected", backend="sqlite")
                return SQLiteLattice(config=self.config)  # type: ignore[no-any-return]
            except ImportError:
                log.info(
                    "lattice.api.backend_fallback",
                    requested="sqlite",
                    fallback="memory",
                )
        elif backend_name == "neo4j":
            try:
                from agency.lattice.backends.neo4j import Neo4jLattice

                log.info("lattice.api.backend_selected", backend="neo4j")
                return Neo4jLattice(config=self.config)  # type: ignore[no-any-return]
            except ImportError:
                log.warning(
                    "lattice.api.backend_fallback",
                    requested="neo4j",
                    fallback="memory",
                )
        else:
            log.warning(
                "lattice.api.unknown_backend",
                requested=backend_name,
                fallback="memory",
            )
        return InMemoryLatticeBackend(config=self.config)

    async def initialize(self) -> None:
        """Initialize backend (create tables, connect)."""

        log.info("lattice.api.initializing", backend=self.config.backend)
        await self.backend.initialize()
        log.info("lattice.api.initialized", backend=self.config.backend)

    async def close(self) -> None:
        """Release backend connections."""

        await self.backend.close()
        log.info("lattice.api.closed")

    # === Node Operations (delegate to backend) ===
    async def create_node(
        self,
        node_type: NodeType | str,
        properties: dict[str, Any],
        actor: str = "system",
    ) -> str:
        """Create a node of ``node_type``; returns its ID."""

        return await self.backend.create_node(
            _coerce_node_type(node_type), properties, actor
        )

    async def get_node(self, node_id: str) -> dict[str, Any] | None:
        """Fetch a node by ID, or ``None`` when absent."""

        return await self.backend.get_node(node_id)

    async def update_node(
        self,
        node_id: str,
        properties: dict[str, Any],
        actor: str = "system",
    ) -> bool:
        """Merge ``properties`` into a node."""

        return await self.backend.update_node(node_id, properties, actor)

    async def delete_node(self, node_id: str, actor: str = "system") -> bool:
        """Delete a node; returns ``True`` when removed."""

        return await self.backend.delete_node(node_id, actor)

    async def find_nodes(
        self,
        node_type: NodeType | str | None = None,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List nodes by type + property filters (paginated)."""

        coerced = _coerce_node_type(node_type) if node_type is not None else None
        return await self.backend.find_nodes(coerced, filters, limit, offset)

    # === Edge Operations ===
    async def add_edge(
        self,
        source_id: str,
        target_id: str,
        edge_type: EdgeType | str,
        properties: dict[str, Any] | None = None,
        actor: str = "system",
    ) -> str:
        """Create a relationship; returns its ID."""

        return await self.backend.add_edge(
            source_id, target_id, _coerce_edge_type(edge_type), properties, actor
        )

    async def get_edges(
        self,
        node_id: str,
        direction: str = "both",
        edge_type: EdgeType | str | None = None,
    ) -> list[dict[str, Any]]:
        """Edges incident to ``node_id``."""

        coerced = _coerce_edge_type(edge_type) if edge_type is not None else None
        return await self.backend.get_edges(node_id, direction, coerced)

    async def remove_edge(self, edge_id: str, actor: str = "system") -> bool:
        """Delete a relationship; returns ``True`` when removed."""

        return await self.backend.remove_edge(edge_id, actor)

    # === Graph Traversal ===
    async def traverse(
        self,
        start_node: str,
        edge_type: EdgeType | str | None = None,
        max_depth: int = 3,
        direction: str = "out",
    ) -> list[str]:
        """Node IDs reachable from ``start_node`` within ``max_depth``."""

        coerced = _coerce_edge_type(edge_type) if edge_type is not None else None
        return await self.backend.traverse(start_node, coerced, max_depth, direction)

    async def find_path(
        self, source: str, target: str, max_depth: int = 5
    ) -> list[str] | None:
        """Shortest path from ``source`` to ``target`` (``None`` if none)."""

        return await self.backend.find_path(source, target, max_depth)

    async def get_dependencies(self, task_id: str) -> list[str]:
        """All tasks ``task_id`` depends on."""

        return await self.backend.get_dependencies(task_id)

    async def get_dependents(self, task_id: str) -> list[str]:
        """All tasks depending on ``task_id``."""

        return await self.backend.get_dependents(task_id)

    # === Vector Search ===
    async def upsert_vector(
        self,
        collection: str,
        vectors: list[tuple[str, list[float], dict[str, Any]]],
    ) -> bool:
        """Insert/replace ``(id, vector, payload)`` rows in ``collection``."""

        return await self.backend.upsert_vector(collection, vectors)

    async def search_vectors(
        self,
        collection: str,
        query_vector: list[float],
        limit: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Nearest-neighbour search; ``[]`` when unavailable (graceful)."""

        try:
            return await self.backend.search_vectors(
                collection, query_vector, limit, filters
            )
        except Exception:  # noqa: BLE001 - graceful degradation
            log.warning("lattice.api.vector_search_degraded", collection=collection)
            return []

    async def search_by_text(
        self,
        collection: str,
        text: str,
        limit: int = 10,
        embedder: Embedder | None = None,
    ) -> list[dict[str, Any]]:
        """Keyword/vector text search; ``[]`` when unavailable (graceful)."""

        try:
            if embedder is not None:
                return await self.backend.search_by_text(
                    collection, text, embedder, limit
                )
            return await self._metadata_text_search(collection, text, limit)
        except Exception:  # noqa: BLE001 - graceful degradation
            log.warning("lattice.api.text_search_degraded", collection=collection)
            return []

    async def _metadata_text_search(
        self, collection: str, text: str, limit: int
    ) -> list[dict[str, Any]]:
        """Fallback substring search over stored vector payloads."""

        store = getattr(self.backend, "_vectors", {}).get(collection, {})
        needle = text.strip().lower()
        if not needle:
            return []
        scored: list[dict[str, Any]] = []
        for vector_id, (_vector, payload) in store.items():
            haystack = " ".join(str(value) for value in payload.values()).lower()
            if needle in haystack:
                scored.append(
                    {"id": vector_id, "score": 1.0, "payload": dict(payload)}
                )
        return scored[:limit]

    async def delete_vectors(self, collection: str, ids: list[str]) -> bool:
        """Remove ``ids`` from ``collection``."""

        return await self.backend.delete_vectors(collection, ids)

    # === Governance (delegated) ===
    async def submit_proposal(
        self,
        proposer_id: str,
        proposal_type: str,
        payload: dict[str, Any],
        quorum: float | None = None,
        ttl_seconds: int = 3600,
        ttl: int | None = None,
    ) -> str:
        """Open a governance proposal; returns its ID."""

        effective_quorum = (
            quorum if quorum is not None else self.config.default_quorum
        )
        effective_ttl = ttl if ttl is not None else ttl_seconds
        return await self.backend.submit_proposal(
            proposer_id, proposal_type, payload, effective_quorum, effective_ttl
        )

    async def cast_vote(
        self,
        voter_id: str,
        proposal_id: str,
        decision: str,
        evidence: list[str] | None = None,
    ) -> dict[str, Any]:
        """Record a vote; returns a summary dict with quorum state."""

        await self.backend.cast_vote(voter_id, proposal_id, decision, evidence)
        proposal = await self.backend.get_proposal_status(proposal_id)
        quorum_reached = await self.backend.check_quorum(proposal_id)
        status = (await self.backend.get_proposal_status(proposal_id)).status
        return {
            "proposal_id": proposal_id,
            "voter_id": voter_id,
            "decision": decision,
            "status": status,
            "vote_count": len(proposal.votes),
            "quorum_reached": quorum_reached,
        }

    async def get_proposal_status(self, proposal_id: str) -> ConsensusProposal:
        """Current proposal state."""

        return await self.backend.get_proposal_status(proposal_id)

    async def update_reputation(
        self,
        agent_id: str,
        success: bool,
        peer_rating: float | None = None,
    ) -> Reputation:
        """Fold a task outcome into an agent's reputation."""

        return await self.backend.update_reputation(agent_id, success, peer_rating)

    async def get_reputation(self, agent_id: str) -> Reputation:
        """Reputation for ``agent_id``."""

        return await self.backend.get_reputation(agent_id)

    async def check_quorum(self, proposal_id: str) -> bool:
        """Whether a proposal has reached quorum."""

        return await self.backend.check_quorum(proposal_id)

    # === Event Log ===
    async def get_events(
        self,
        target_id: str | None = None,
        event_type: str | None = None,
        actor: str | None = None,
        limit: int = 100,
    ) -> list[LatticeEvent]:
        """Query the event log, newest first."""

        return await self.backend.get_events(target_id, event_type, actor, limit)

    async def replay_events(
        self,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
        from_timestamp: datetime | None = None,
        to_timestamp: datetime | None = None,
    ) -> list[LatticeEvent]:
        """Events in ``[from, to]``, oldest first (alias-tolerant)."""

        start = from_ts if from_ts is not None else from_timestamp
        end = to_ts if to_ts is not None else to_timestamp
        if start is None:
            start = datetime(1970, 1, 1, tzinfo=utc_now().tzinfo)
        return await self.backend.replay_events(start, end)

    async def get_audit_trail(self, node_id: str) -> list[LatticeEvent]:
        """Full history for ``node_id``, oldest first."""

        return await self.backend.get_audit_trail(node_id)

    # === Health & Status ===
    async def get_status(self) -> dict[str, Any]:
        """Backend health, counts, and pending governance state."""

        return await self.backend.get_status()

    async def get_agent_count(self) -> int:
        """Number of registered agents."""

        return await self.backend.get_agent_count()

    async def get_active_tasks(self) -> list[dict[str, Any]]:
        """Task nodes not in a terminal state."""

        return await self.backend.get_active_tasks()

    # === Convenience Methods ===
    async def register_agent(
        self,
        agent_id: str,
        agent_type: str,
        capabilities: list[str],
    ) -> str:
        """Register an agent; returns the agent node's ID."""

        return await self.create_node(
            NodeType.AGENT,
            {
                "agent_id": agent_id,
                "agent_type": agent_type,
                "capabilities": list(capabilities),
                "status": "active",
            },
            actor=agent_id,
        )

    async def create_task(
        self,
        agent_id: str,
        task_type: str,
        payload: dict[str, Any],
    ) -> str:
        """Create a task owned by ``agent_id``; returns the task node's ID."""

        return await self.create_node(
            NodeType.TASK,
            {
                "task_type": task_type,
                "payload": dict(payload),
                "created_by": agent_id,
                "status": "open",
            },
            actor=agent_id,
        )

    async def complete_task(
        self,
        task_id: str,
        deliverable: dict[str, Any],
    ) -> bool:
        """Attach a deliverable to ``task_id`` and mark it completed."""

        deliverable_id = await self.create_node(
            NodeType.DELIVERABLE,
            {"task_id": task_id, **dict(deliverable)},
            actor="system",
        )
        await self.add_edge(
            deliverable_id,
            task_id,
            EdgeType.PRODUCED_BY,
            {"task_id": task_id},
            actor="system",
        )
        return await self.update_node(
            task_id,
            {"status": "completed", "deliverable_id": deliverable_id},
            actor="system",
        )

    async def link_evidence(self, evidence_id: str, target_id: str) -> str:
        """Link an evidence node to its target; returns the edge ID."""

        return await self.add_edge(
            evidence_id,
            target_id,
            EdgeType.EVIDENCE_FOR,
            {},
            actor="system",
        )


__all__ = [
    "GovernanceEngine",
    "InMemoryLatticeBackend",
    "Lattice",
]
