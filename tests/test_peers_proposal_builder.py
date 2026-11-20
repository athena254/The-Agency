"""Hermetic signed proposal/ballot builder tests."""

from __future__ import annotations

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agency.peer_envelope import decode_envelope
from agency.peers.authority import PeerAuthority
from agency.peers.identities import PeerIdentity
from agency.peers.membership import derive_genesis_membership
from agency.peers.proposal_builder import build_ballot_wire, build_proposal_envelope


def setup():
    ids = [PeerIdentity.from_private_key(Ed25519PrivateKey.generate()) for _ in range(4)]
    members = [
        {"peer_id": i.peer_id, "public_key": i.fingerprint, "roles": ["validator"], "joined_epoch": 1}
        for i in ids
    ]
    mem = derive_genesis_membership(members, policy_version="peer-policy/1")
    return ids, mem


def test_build_proposal_envelope():
    ids, mem = setup()
    snapshot = {
        "graph_root_digest": "0" * 64,
        "epoch": mem.epoch,
        "membership_root": mem.root,
        "members": [
            {"peer_id": ids[0].peer_id, "consumer_id": None, "kind": "peer", "role": "validator", "reason": "proposer"}
        ],
        "derivation": "genesis",
    }
    wire = build_proposal_envelope(ids[0], mem, action_type="echo", resource="res-1", params={"a": 1}, affected_snapshot=snapshot)
    env = decode_envelope(wire)
    assert env["kind"] == "proposal"
    assert env["payload"]["action"]["action_type"] == "echo"
    assert env["payload"]["proposal_id"].startswith("prop_")


def test_build_ballot_wire():
    ids, mem = setup()
    auth = PeerAuthority(mem)
    wire = build_ballot_wire(auth, ids[0], proposal_id="prop_" + "a" * 32, decision="approve", monotonic_seq=1)
    assert isinstance(wire, bytes) and len(wire) > 0
