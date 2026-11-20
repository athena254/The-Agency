"""Hermetic membership/revocation handler tests."""

from __future__ import annotations

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agency.consensus.grants import GrantLedger, revoke_grant
from agency.peers.authority import PeerAuthority
from agency.peers.identities import PeerIdentity
from agency.peers.log import PeerLog
from agency.peers.membership import derive_genesis_membership
from agency.peers.membership_handler import apply_membership_update, record_revocation


def setup(tmp_path):
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
    log = PeerLog(
        tmp_path / "peer.log",
        peer_id=ids[0].peer_id,
        genesis={
            "epoch": mem.epoch,
            "membership_root": mem.root,
            "policy_version": mem.policy_version,
        },
        expected_genesis_root=mem.root,
    )
    log.open()
    return ids, mem, log


def test_apply_membership_update(tmp_path):
    _ids, mem, log = setup(tmp_path)
    auth = PeerAuthority(mem)
    assert (
        apply_membership_update(
            auth, log, new_membership=mem, reason_digest="0" * 64, epoch=mem.epoch, ts=1000
        )
        is True
    )
    assert any(r.kind == "reconciliation" for r in log.records())


def test_record_revocation(tmp_path):
    ids, mem, log = setup(tmp_path)
    ledger = GrantLedger(membership=mem)
    grant = ledger.issue(
        ids[0],
        proposal_id="prop_" + "a" * 32,
        task_id="t",
        issued_at_ms=1000,
        expires_at_ms=61000,
        affected_consumers=[],
    )
    rev = revoke_grant(ids[0], grant, reason_digest="0" * 64)
    ok = record_revocation(
        log,
        grant_digest=grant.digest,
        revocation=rev,
        pinned_issuers=ledger.pinned_issuers,
        epoch=mem.epoch,
        membership_root=mem.root,
        ts=1000,
    )
    assert ok is True
    assert any(r.kind == "outcome" for r in log.records())
