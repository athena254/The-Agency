"""Decentralised Lattice replication via per-peer gossip (KISS).

Each peer owns one SQLite ``GossipStore``: an append-only mirror of the
``lattice.audit_chain`` hash chain, partitioned by originating ``peer_id``.
Anti-entropy sync is in-process only (no network): two peers compare their
``(peer_id, event_hash, seq)`` sets, transfer missing events, and apply
them idempotently. An event is applied only if its hash recomputes and it
links to its predecessor (genesis or an already-held event), so a tampered
event is rejected and blocks its successors in that peer's stream.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Self

from agency.lattice.audit_chain import GENESIS_HASH, compute_event_hash, verify_chain

__all__ = ["GossipError", "GossipStore", "sync_peers"]


class GossipError(ValueError):
    """A gossip event failed hash-chain verification."""


class GossipStore:
    """Append-only per-peer event log backed by one SQLite database."""

    def __init__(self, peer_id: str, db_path: str | Path | None = None) -> None:
        if not peer_id:
            raise GossipError("peer_id must not be empty")
        self.peer_id = peer_id
        self._path = Path(db_path) if db_path and str(db_path) != ":memory:" else None
        self._conn = sqlite3.connect(str(db_path) if db_path else ":memory:")
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS gossip_events ("
            " peer_id TEXT NOT NULL, seq INTEGER NOT NULL, event_hash TEXT NOT NULL,"
            " prev_hash TEXT NOT NULL, event TEXT NOT NULL,"
            " PRIMARY KEY (peer_id, seq), UNIQUE (peer_id, event_hash))"
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- reads ---------------------------------------------------------- #
    def events(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT event FROM gossip_events ORDER BY peer_id, seq"
        ).fetchall()
        return [json.loads(r["event"]) for r in rows]

    def known(self) -> set[tuple[str, str, int]]:
        rows = self._conn.execute("SELECT peer_id, event_hash, seq FROM gossip_events").fetchall()
        return {(r["peer_id"], r["event_hash"], int(r["seq"])) for r in rows}

    def verify(self) -> bool:
        by_peer: dict[str, list[dict[str, Any]]] = {}
        for ev in self.events():
            by_peer.setdefault(str(ev["peer_id"]), []).append(ev)
        return all(verify_chain(sorted(v, key=lambda e: e["seq"])) for v in by_peer.values())

    # -- writes --------------------------------------------------------- #
    def append(self, event: dict[str, Any]) -> dict[str, Any]:
        """Chain ``event`` onto this peer's stream and store it (idempotent seq)."""
        row = self._conn.execute(
            "SELECT event_hash, seq FROM gossip_events WHERE peer_id = ? ORDER BY seq DESC LIMIT 1",
            (self.peer_id,),
        ).fetchone()
        prev = str(row["event_hash"]) if row else GENESIS_HASH
        seq = int(row["seq"]) + 1 if row else 1
        draft = {**event, "peer_id": self.peer_id}
        draft.pop("prev_hash", None)
        draft.pop("event_hash", None)
        draft.pop("seq", None)
        chained = {**draft, "prev_hash": prev, "event_hash": compute_event_hash(prev, draft)}
        self._insert({**chained, "seq": seq})
        return {**chained, "seq": seq}

    def apply(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Verify then idempotently insert ``events``; returns applied ones."""
        applied: list[dict[str, Any]] = []
        for ev in sorted(events, key=lambda e: (str(e["peer_id"]), int(e["seq"]))):
            if self._apply_one(ev):
                applied.append(ev)
        return applied

    def _apply_one(self, ev: dict[str, Any]) -> bool:
        peer, seq = str(ev["peer_id"]), int(ev["seq"])
        prev, eh = str(ev["prev_hash"]), str(ev["event_hash"])
        if compute_event_hash(prev, ev) != eh:
            raise GossipError(f"tampered event rejected: hash mismatch {peer}:{seq}")
        existing = self._conn.execute(
            "SELECT event_hash FROM gossip_events WHERE peer_id = ? AND seq = ?",
            (peer, seq),
        ).fetchone()
        if existing is not None:
            if str(existing["event_hash"]) != eh:
                raise GossipError(f"conflicting event at {peer}:{seq}")
            return False  # already held: idempotent, no duplicate
        if prev != GENESIS_HASH:
            pred = self._conn.execute(
                "SELECT event_hash FROM gossip_events WHERE peer_id = ? AND seq = ?",
                (peer, seq - 1),
            ).fetchone()
            if pred is None or str(pred["event_hash"]) != prev:
                raise GossipError(f"orphan event rejected: missing predecessor {peer}:{seq}")
        self._insert(ev)
        return True

    def _insert(self, ev: dict[str, Any]) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO gossip_events"
            " (peer_id, seq, event_hash, prev_hash, event) VALUES (?, ?, ?, ?, ?)",
            (
                str(ev["peer_id"]),
                int(ev["seq"]),
                str(ev["event_hash"]),
                str(ev["prev_hash"]),
                json.dumps(ev, sort_keys=True),
            ),
        )
        self._conn.commit()


def sync_peers(a: GossipStore, b: GossipStore) -> list[dict[str, Any]]:
    """Anti-entropy sync in both directions; returns all transferred events.

    Compares ``(peer_id, event_hash, seq)`` sets, transfers missing events,
    and applies them idempotently. A tampered event raises ``GossipError``
    on the receiving side and is not stored.
    """
    a_events, b_events = a.events(), b.events()
    a_known, b_known = a.known(), b.known()

    def missing(
        src: list[dict[str, Any]], dst_known: set[tuple[str, str, int]]
    ) -> list[dict[str, Any]]:
        return [
            e
            for e in src
            if (str(e["peer_id"]), str(e["event_hash"]), int(e["seq"])) not in dst_known
        ]

    transferred = b.apply(missing(a_events, b_known)) + a.apply(missing(b_events, a_known))
    return transferred
