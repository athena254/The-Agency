"""Hermetic chain-head anchoring tests."""

from __future__ import annotations

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agency.peers.chain_head import anchor_chain_head, build_chain_head_payload
from agency.peers.identities import PeerIdentity
from agency.peers.log import PeerLog
from agency.peers.membership import derive_genesis_membership


def setup(tmp_path):
    ids = [PeerIdentity.from_private_key(Ed25519PrivateKey.generate()) for _ in range(4)]
    members = [
        {"peer_id": i.peer_id, "public_key": i.fingerprint, "roles": ["validator"], "joined_epoch": 1}
        for i in ids
    ]
    mem = derive_genesis_membership(members, policy_version="peer-policy/1")
    log = PeerLog(
        tmp_path / "peer.log",
        peer_id=ids[0].peer_id,
        genesis={"epoch": mem.epoch, "membership_root": mem.root, "policy_version": mem.policy_version},
        expected_genesis_root=mem.root,
    )
    log.open()
    return ids[0].peer_id, mem, log


def test_chain_head_payload(tmp_path):
    peer_id, _mem, log = setup(tmp_path)
    payload = build_chain_head_payload(log, peer_id=peer_id)
    assert payload is not None and payload.get("peer_id") == peer_id


def test_anchor_chain_head(tmp_path):
    peer_id, mem, log = setup(tmp_path)
    seq = anchor_chain_head(log, peer_id=peer_id, membership=mem, ts=1000)
    assert seq is not None
    assert any(r.kind == "recovery" for r in log.records())
