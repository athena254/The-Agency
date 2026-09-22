"""Backend-agnostic interface for the Unified Lattice Service.

Implements SPEC_LATTICE.md section 4 (API surface) and section 5.4
(``LatticeBackend`` protocol). Agents program against this protocol and
never touch SQLite/Neo4j/Qdrant directly.

Concrete backends (``SQLiteLattice``, ``Neo4jLattice``) implement every
method below. All methods are async; there is no blocking I/O.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from agency.lattice.models import (
    ConsensusProposal,
    EdgeType,
    LatticeEvent,
    NodeType,
    Reputation,
)

Embedder = Callable[[str], Awaitable[list[float]]]
"""Async callable mapping text to an embedding vector."""


@runtime_checkable
class LatticeBackend(Protocol):
    """Full Lattice API. See SPEC_LATTICE.md section 4 for semantics."""

    # -- lifecycle ---------------------------------------------------- #
    async def initialize(self) -> None:
        """Open connections and create the schema (idempotent)."""
        ...

    async def close(self) -> None:
        """Release connections. Safe to call more than once."""
        ...

    # -- 4.1 node operations ------------------------------------------ #
    async def create_node(
        self,
        node_type: NodeType,
        properties: dict[str, Any],
        actor: str = "system",
    ) -> str:
        """Create a node; returns its ID and logs a ``node_created`` event."""
        ...

    async def get_node(self, node_id: str) -> dict[str, Any] | None:
        """Fetch a node by ID, or ``None`` when absent."""
        ...

    async def update_node(
        self,
        node_id: str,
        properties: dict[str, Any],
        actor: str = "system",
    ) -> bool:
        """Merge ``properties`` into a node; logs event with ``prior_state``."""
        ...

    async def delete_node(self, node_id: str, actor: str = "system") -> bool:
        """Delete a node; returns ``True`` when a row was removed."""
        ...

    async def find_nodes(
        self,
        node_type: NodeType | None = None,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List nodes by type + property filters (paginated)."""
        ...

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
        ...

    async def get_edges(
        self,
        node_id: str,
        direction: str = "both",
        edge_type: EdgeType | None = None,
    ) -> list[dict[str, Any]]:
        """Edges incident to ``node_id`` (``direction``: in|out|both)."""
        ...

    async def remove_edge(self, edge_id: str, actor: str = "system") -> bool:
        """Delete a relationship; returns ``True`` when removed."""
        ...

    # -- 4.3 graph traversal ------------------------------------------ #
    async def traverse(
        self,
        start_node: str,
        edge_type: EdgeType | None = None,
        max_depth: int = 3,
        direction: str = "out",
    ) -> list[str]:
        """Node IDs reachable from ``start_node`` within ``max_depth``."""
        ...

    async def find_path(
        self,
        source: str,
        target: str,
        max_depth: int = 5,
    ) -> list[str] | None:
        """Shortest path from ``source`` to ``target`` (``None`` if none)."""
        ...

    async def get_dependencies(self, task_id: str) -> list[str]:
        """All tasks ``task_id`` depends on (``DEPENDS_ON``, outgoing)."""
        ...

    async def get_dependents(self, task_id: str) -> list[str]:
        """All tasks depending on ``task_id`` (``DEPENDS_ON``, incoming)."""
        ...

    # -- 4.4 vector search -------------------------------------------- #
    async def upsert_vector(
        self,
        collection: str,
        vectors: list[tuple[str, list[float], dict[str, Any]]],
    ) -> bool:
        """Insert/replace ``(id, vector, payload)`` rows in ``collection``."""
        ...

    async def search_vectors(
        self,
        collection: str,
        query_vector: list[float],
        limit: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Nearest-neighbour search; returns ``{id, score, payload}`` dicts."""
        ...

    async def search_by_text(
        self,
        collection: str,
        text: str,
        embedder: Embedder,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Embed ``text`` with ``embedder`` then run :meth:`search_vectors`."""
        ...

    async def delete_vectors(self, collection: str, ids: list[str]) -> bool:
        """Remove ``ids`` from ``collection``."""
        ...

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
        ...

    async def cast_vote(
        self,
        voter_id: str,
        proposal_id: str,
        decision: str,
        evidence: list[str] | None = None,
    ) -> bool:
        """Record a vote (``approve``|``deny``|``abstain``); checks quorum."""
        ...

    async def get_proposal_status(self, proposal_id: str) -> ConsensusProposal:
        """Current proposal state (raises ``KeyError`` when unknown)."""
        ...

    async def update_reputation(
        self,
        agent_id: str,
        success: bool,
        peer_rating: float | None = None,
    ) -> Reputation:
        """Fold a task outcome (+ optional peer rating) into reputation."""
        ...

    async def get_reputation(self, agent_id: str) -> Reputation:
        """Reputation for ``agent_id`` (neutral baseline when unknown)."""
        ...

    async def check_quorum(self, proposal_id: str) -> bool:
        """Whether a proposal has reached quorum (resolves it if so)."""
        ...

    # -- 4.6 event log ------------------------------------------------ #
    async def get_events(
        self,
        target_id: str | None = None,
        event_type: str | None = None,
        actor: str | None = None,
        limit: int = 100,
    ) -> list[LatticeEvent]:
        """Query the event log, newest first."""
        ...

    async def replay_events(
        self,
        from_timestamp: datetime,
        to_timestamp: datetime | None = None,
    ) -> list[LatticeEvent]:
        """Events in ``[from_timestamp, to_timestamp]``, oldest first."""
        ...

    async def get_audit_trail(self, node_id: str) -> list[LatticeEvent]:
        """Full history for ``node_id``, oldest first."""
        ...

    # -- 4.7 health & status ------------------------------------------ #
    async def get_status(self) -> dict[str, Any]:
        """Backend, counts, pending proposals, last event, storage size."""
        ...

    async def get_agent_count(self) -> int:
        """Number of ``AGENT`` nodes."""
        ...

    async def get_active_tasks(self) -> list[dict[str, Any]]:
        """Task nodes not in a terminal state."""
        ...


__all__ = ["Embedder", "LatticeBackend"]
