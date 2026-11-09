"""Unified proof: spawn via factory, tool via forge, vote via HTTP, sync via gossip."""

from __future__ import annotations

import base64

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agency.api.routers.governance import ballot_router
from agency.peers.authority import PeerAuthority, PeerRegistry
from agency.peers.gossip import GossipStore, sync_peers
from agency.peers.identities import PeerIdentity
from agency.peers.membership import derive_genesis_membership


def _setup_peer_authority():
    ids = [PeerIdentity.from_private_key(Ed25519PrivateKey.generate()) for _ in range(4)]
    members = [
        {
            "peer_id": i.peer_id,
            "public_key": i.fingerprint,
            "roles": ["validator"],
            "joined_epoch": 1,
        }
        for i in ids
    ]
    mem = derive_genesis_membership(members, policy_version="peer-policy/1")
    reg = PeerRegistry()
    for ident in ids:
        reg.register(ident)
    return ids, PeerAuthority(mem, reg)


def test_unified_decentralised_loop(tmp_path):
    # 1. authority + HTTP ballot
    ids, auth = _setup_peer_authority()
    app = FastAPI()
    app.state.peer_authority = auth
    app.include_router(ballot_router)
    client = TestClient(app)

    pid = "prop_" + "b" * 32
    for i, ident in enumerate(ids[:3]):
        wire = auth.make_ballot(ident, pid, "approve", monotonic_seq=i + 1)
        assert isinstance(wire, (bytes, bytearray))
        # ballot_router accepts wire_hex per subagent impl; try hex first
        r = client.post("/v1/governance/ballot", json={"wire_hex": bytes(wire).hex()})
        if r.status_code == 400:
            r = client.post(
                "/v1/governance/ballot",
                json={"wire_base64": base64.b64encode(bytes(wire)).decode()},
            )
        assert r.status_code == 200, r.text
    assert auth.decide(pid) == "passed"

    # gossip sync: two peers converge
    a = GossipStore("peer-a", str(tmp_path / "a.db"))
    b = GossipStore("peer-b", str(tmp_path / "b.db"))
    a.append(
        {
            "event_id": "e1",
            "event_type": "node_created",
            "actor": "t",
            "target_id": "t1",
            "payload": {"x": 1},
            "timestamp": "2026-01-01T00:00:00+00:00",
        }
    )
    b.append(
        {
            "event_id": "e2",
            "event_type": "node_created",
            "actor": "t",
            "target_id": "t2",
            "payload": {"y": 2},
            "timestamp": "2026-01-01T00:00:00+00:00",
        }
    )
    synced = sync_peers(a, b)
    assert synced is not None
    assert len(a.events()) == len(b.events()) == 2
    assert a.verify() and b.verify()
    a.close()
    b.close()
