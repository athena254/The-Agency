from __future__ import annotations

import json
import sqlite3

import pytest

from agency.peers.gossip import GossipError, GossipStore, sync_peers


def _event(n: int) -> dict:
    return {
        "event_id": f"e{n}",
        "event_type": "node_created",
        "actor": "tester",
        "target_id": f"t{n}",
        "payload": {"n": n},
        "prior_state": None,
        "timestamp": "2026-01-01T00:00:00+00:00",
    }


def test_divergent_peers_converge(tmp_path):
    a = GossipStore("peer-a", tmp_path / "a.db")
    b = GossipStore("peer-b", tmp_path / "b.db")
    a.append(_event(1))
    a.append(_event(2))
    b.append(_event(3))
    transferred = sync_peers(a, b)
    assert len(transferred) == 3
    assert a.known() == b.known()
    assert len(a.events()) == len(b.events()) == 3
    assert a.verify() and b.verify()
    # Idempotent: second sync transfers nothing, creates no duplicates.
    assert sync_peers(a, b) == []
    assert len(a.events()) == len(b.events()) == 3
    a.close()
    b.close()


def test_tampered_event_rejected(tmp_path):
    a = GossipStore("peer-a", tmp_path / "a.db")
    b = GossipStore("peer-b", tmp_path / "b.db")
    a.append(_event(1))
    # Tamper b's stored copy directly, bypassing the API.
    b.append(_event(2))
    conn = sqlite3.connect(tmp_path / "b.db")
    row = conn.execute("SELECT event FROM gossip_events").fetchone()
    tampered = json.loads(row[0])
    tampered["payload"] = {"n": 999}
    conn.execute(
        "UPDATE gossip_events SET event = ?",
        (json.dumps(tampered, sort_keys=True),),
    )
    conn.commit()
    conn.close()
    with pytest.raises(GossipError):
        sync_peers(a, b)
    # Tampered hash was not applied to a.
    assert all(e["payload"] != {"n": 999} for e in a.events())
    a.close()
    b.close()
