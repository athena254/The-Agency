"""Signed-ballot HTTP endpoint: valid, duplicate, forged, and 3-of-4 quorum."""

from __future__ import annotations

import base64
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agency.api.routers.governance import ballot_router
from agency.peer_envelope import canonical_json
from agency.peers.authority import PeerAuthority, PeerRegistry
from agency.peers.identities import PeerIdentity
from agency.peers.membership import derive_genesis_membership

POLICY = "peer-policy/1"
URL = "/v1/governance/ballot"


def setup4():
    keys = [Ed25519PrivateKey.generate() for _ in range(4)]
    ids = [PeerIdentity.from_private_key(k) for k in keys]
    members = [
        {
            "peer_id": i.peer_id,
            "public_key": i.fingerprint,
            "roles": ["validator"],
            "joined_epoch": 1,
        }
        for i in ids
    ]
    mem = derive_genesis_membership(members, policy_version=POLICY)
    reg = PeerRegistry()
    for i in ids:
        reg.register(i)
    return ids, PeerAuthority(mem, reg)


@pytest.fixture
def client():
    ids, auth = setup4()
    app = FastAPI()
    app.state.peer_authority = auth
    app.include_router(ballot_router)
    with TestClient(app) as c:
        yield c, ids, auth


def post_hex(client, wire: bytes, **kw):
    return client.post(URL, json={"wire_hex": wire.hex()}, **kw)


def test_valid_ballot_passes(client):
    c, ids, auth = client
    pid = "prop_" + "a" * 32
    wire = auth.make_ballot(ids[0], pid, "approve", monotonic_seq=1)
    r = post_hex(c, wire)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["proposal_id"] == pid
    assert body["voter_peer_id"] == ids[0].peer_id
    assert body["decision"] == "approve"
    assert body["tally"] == {"approve": 1, "deny": 0, "abstain": 0}
    assert body["outcome"] == "open"
    assert body["quorum_met"] is False


def test_valid_ballot_base64_passes(client):
    c, ids, auth = client
    pid = "prop_" + "b" * 32
    wire = auth.make_ballot(ids[0], pid, "approve", monotonic_seq=1)
    r = c.post(URL, json={"wire_base64": base64.b64encode(wire).decode()})
    assert r.status_code == 200, r.text
    assert r.json()["tally"]["approve"] == 1


def test_duplicate_seq_rejected(client):
    c, ids, auth = client
    pid = "prop_" + "c" * 32
    wire = auth.make_ballot(ids[0], pid, "approve", monotonic_seq=1)
    assert post_hex(c, wire).status_code == 200
    r = post_hex(c, wire)
    assert r.status_code == 400
    assert auth.tally(pid)["approve"] == 1


def test_forged_ballot_rejected(client):
    c, ids, auth = client
    pid = "prop_" + "d" * 32
    wire = auth.make_ballot(ids[0], pid, "approve", monotonic_seq=1)
    forged = json.loads(wire)
    forged["payload"]["voter_peer_id"] = ids[1].peer_id
    r = post_hex(c, canonical_json(forged))
    assert r.status_code == 400
    bad = json.loads(wire)
    bad["signature"] = {**bad["signature"], "sig": bad["signature"]["sig"][:-4] + "AAAA"}
    assert post_hex(c, canonical_json(bad)).status_code == 400
    assert auth.tally(pid) == {"approve": 0, "deny": 0, "abstain": 0}


def test_quorum_3_of_4_passes(client):
    c, ids, auth = client
    pid = "prop_" + "e" * 32
    last = None
    for i in (0, 1, 2):
        last = post_hex(c, auth.make_ballot(ids[i], pid, "approve", monotonic_seq=1))
        assert last.status_code == 200, last.text
    body = last.json()
    assert body["tally"]["approve"] == 3
    assert body["quorum_required"] == 3
    assert body["quorum_met"] is True
    assert body["outcome"] == "passed"


def test_missing_authority_fails_closed():
    app = FastAPI()
    app.include_router(ballot_router)
    with TestClient(app) as c:
        r = c.post(URL, json={"wire_hex": "7b7d"})
    assert r.status_code == 503


def test_legacy_unsigned_vote_rejected(client):
    c, _, auth = client
    r = c.post(
        "/v1/governance/vote",
        json={"proposal_id": "prop_" + "a" * 32, "decision": "approve"},
    )
    assert r.status_code == 400
    assert r.headers.get("deprecation") == "true"
    assert auth.tally("prop_" + "a" * 32) == {"approve": 0, "deny": 0, "abstain": 0}
