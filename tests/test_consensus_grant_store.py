"""Hermetic durable grant-store tests (SQLite file/memory, no clock or network)."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

from agency.consensus.grant_store import DurableGrantStore, GrantStoreError
from agency.consensus.grants import ExecutionGrant, GrantRejection, issue_grant, verify_at_actuator
from agency.peers.canonical import digest_json
from agency.peers.identities import PeerIdentity, consumer_id_from_ingress
from agency.peers.membership import Membership, derive_genesis_membership

POLICY = "peer-policy/1"
ISSUED_AT_MS = 1000
EXPIRES_AT_MS = ISSUED_AT_MS + 60_000
BFT_TIME_MS = 2000
PROPOSAL_ID = "prop_" + "a" * 32
REASON = digest_json({"reason": "peer revoked"})


def setup4() -> tuple[list[PeerIdentity], Membership]:
    ids = [PeerIdentity.from_private_key(Ed25519PrivateKey.generate()) for _ in range(4)]
    members = [
        {
            "peer_id": ident.peer_id,
            "public_key": ident.fingerprint,
            "roles": ["validator"],
            "joined_epoch": 1,
        }
        for ident in ids
    ]
    return ids, derive_genesis_membership(members, policy_version=POLICY)


def consumers() -> tuple[str, ...]:
    return (
        consumer_id_from_ingress(ingress="telegram", subject="alice"),
        consumer_id_from_ingress(ingress="telegram", subject="bob"),
    )


def grant_for(
    issuer: PeerIdentity, membership: Membership, task_id: str = "task-1"
) -> ExecutionGrant:
    return issue_grant(
        issuer,
        membership=membership,
        proposal_id=PROPOSAL_ID,
        task_id=task_id,
        issued_at_ms=ISSUED_AT_MS,
        expires_at_ms=EXPIRES_AT_MS,
        affected_consumers=consumers(),
    )


def issue_via(
    store: DurableGrantStore, issuer: PeerIdentity, task_id: str = "task-1"
) -> ExecutionGrant:
    return store.issue(
        issuer,
        proposal_id=PROPOSAL_ID,
        task_id=task_id,
        issued_at_ms=ISSUED_AT_MS,
        expires_at_ms=EXPIRES_AT_MS,
        affected_consumers=consumers(),
    )


def test_memory_store_reuses_delegated_verification() -> None:
    ids, membership = setup4()
    with DurableGrantStore(membership=membership) as store:
        grant = issue_via(store, ids[0])
        assert verify_at_actuator(grant, ledger=store, bft_time_ms=BFT_TIME_MS).ok
        assert grant.digest in store.issued_digests
        store.record_used(grant)
        assert store.verify(grant, bft_time_ms=BFT_TIME_MS).reason is GrantRejection.REPLAY


def test_file_store_persists_across_reopen(tmp_path: Path) -> None:
    ids, membership = setup4()
    db_path = tmp_path / "grants.db"
    with DurableGrantStore(membership=membership, db_path=db_path) as store:
        used = issue_via(store, ids[0])
        issued_only = issue_via(store, ids[1], task_id="task-2")
        store.record_used(used)

    with DurableGrantStore(membership=membership, db_path=db_path) as reopened:
        assert reopened.used_nonces == frozenset({used.nonce})
        assert reopened.issued_digests == frozenset({used.digest, issued_only.digest})
        assert reopened.revoked_digests == frozenset()
        assert reopened.verify(used, bft_time_ms=BFT_TIME_MS).reason is GrantRejection.REPLAY
        assert reopened.verify(issued_only, bft_time_ms=BFT_TIME_MS).ok


def test_replay_rejected_after_reopen(tmp_path: Path) -> None:
    ids, membership = setup4()
    db_path = tmp_path / "replay.db"
    grant = grant_for(ids[0], membership)
    with DurableGrantStore(membership=membership, db_path=db_path) as store:
        assert store.verify(grant, bft_time_ms=BFT_TIME_MS).ok
        store.record_used(grant)
    with DurableGrantStore(membership=membership, db_path=db_path) as reopened:
        assert reopened.verify(grant, bft_time_ms=BFT_TIME_MS).reason is GrantRejection.REPLAY


def test_revocation_survives_reopen(tmp_path: Path) -> None:
    ids, membership = setup4()
    db_path = tmp_path / "revoke.db"
    revoked = grant_for(ids[0], membership, task_id="revoked")
    still_valid = grant_for(ids[0], membership, task_id="valid")
    with DurableGrantStore(membership=membership, db_path=db_path) as store:
        store.revoke(ids[0], revoked, reason_digest=REASON)
    with DurableGrantStore(membership=membership, db_path=db_path) as reopened:
        assert reopened.revoked_digests == frozenset({revoked.digest})
        assert reopened.verify(revoked, bft_time_ms=BFT_TIME_MS).reason is GrantRejection.REVOKED
        assert reopened.verify(still_valid, bft_time_ms=BFT_TIME_MS).ok


def test_records_are_idempotent(tmp_path: Path) -> None:
    ids, membership = setup4()
    db_path = tmp_path / "idempotent.db"
    grant = grant_for(ids[0], membership)
    with DurableGrantStore(membership=membership, db_path=db_path) as store:
        store.record_used(grant)
        store.record_used(grant)
        store.record_issued(grant)
        store.record_issued(grant)
        store.revoke(ids[0], grant, reason_digest=REASON)
        store.revoke(ids[0], grant, reason_digest=REASON)
    with sqlite3.connect(db_path) as conn:
        counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("used_nonces", "issued_grants", "revoked_grants")
        }
    assert counts == {"used_nonces": 1, "issued_grants": 1, "revoked_grants": 1}


def test_tampered_stored_nonce_fails_closed(tmp_path: Path) -> None:
    ids, membership = setup4()
    db_path = tmp_path / "bad-nonce.db"
    grant = grant_for(ids[0], membership)
    with DurableGrantStore(membership=membership, db_path=db_path) as store:
        store.record_used(grant)
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE used_nonces SET nonce = ?", ("not-a-nonce",))
        conn.commit()
    with pytest.raises(GrantStoreError):
        DurableGrantStore(membership=membership, db_path=db_path)


def test_tampered_revocation_evidence_fails_closed(tmp_path: Path) -> None:
    ids, membership = setup4()
    db_path = tmp_path / "bad-evidence.db"
    grant = grant_for(ids[0], membership)
    with DurableGrantStore(membership=membership, db_path=db_path) as store:
        store.revoke(ids[0], grant, reason_digest=REASON)
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE revoked_grants SET evidence_digest = ?", ("0" * 64,))
        conn.commit()
    with pytest.raises(GrantStoreError):
        DurableGrantStore(membership=membership, db_path=db_path)


def test_tampered_grant_rejected_by_delegated_verification() -> None:
    ids, membership = setup4()
    grant = grant_for(ids[0], membership)
    with DurableGrantStore(membership=membership) as store:
        assert store.verify(
            replace(grant, signature=b"\x00" * 64), bft_time_ms=BFT_TIME_MS
        ).reason is (GrantRejection.INVALID_SIGNATURE)
        assert store.verify(replace(grant, task_id="tampered"), bft_time_ms=BFT_TIME_MS).reason is (
            GrantRejection.INVALID_SIGNATURE
        )
        assert store.verify(replace(grant, attestation=None), bft_time_ms=BFT_TIME_MS).reason is (
            GrantRejection.MISSING_ATTESTATION
        )


def test_expiry_comes_only_from_caller_bft_time() -> None:
    ids, membership = setup4()
    grant = grant_for(ids[0], membership)
    with DurableGrantStore(membership=membership) as store:
        assert store.verify(grant, bft_time_ms=ISSUED_AT_MS).ok
        assert store.verify(grant, bft_time_ms=BFT_TIME_MS).ok
        assert store.verify(grant, bft_time_ms=EXPIRES_AT_MS).reason is GrantRejection.EXPIRED
        assert store.verify(grant, bft_time_ms=EXPIRES_AT_MS + 1).reason is GrantRejection.EXPIRED


def test_revocation_requires_pinned_issuer() -> None:
    ids, membership = setup4()
    grant = grant_for(ids[0], membership)
    with DurableGrantStore(membership=membership) as store:
        with pytest.raises(GrantStoreError):
            store.revoke(PeerIdentity.generate(), grant, reason_digest=REASON)
        assert store.revoked_digests == frozenset()
        assert store.verify(grant, bft_time_ms=BFT_TIME_MS).ok


def test_unknown_grant_revocation_recorded_but_never_authorizes() -> None:
    ids, membership = setup4()
    unknown = grant_for(ids[0], membership, task_id="never-issued")
    known = grant_for(ids[1], membership, task_id="issued")
    with DurableGrantStore(membership=membership) as store:
        store.record_issued(known)
        store.revoke(ids[0], unknown, reason_digest=REASON)
        assert unknown.digest in store.revoked_digests
        assert unknown.digest not in store.issued_digests
        assert store.verify(unknown, bft_time_ms=BFT_TIME_MS).reason is GrantRejection.REVOKED
        assert store.verify(known, bft_time_ms=BFT_TIME_MS).ok


def test_verification_uses_pinned_membership() -> None:
    ids, membership = setup4()
    grant = grant_for(ids[0], membership)
    members = [
        {
            "peer_id": ident.peer_id,
            "public_key": ident.fingerprint,
            "roles": ["validator"],
            "joined_epoch": 1,
        }
        for ident in ids
    ]
    other = derive_genesis_membership(members, policy_version="peer-policy/2")
    assert other.root != membership.root
    with DurableGrantStore(membership=other) as store:
        assert store.verify(grant, bft_time_ms=BFT_TIME_MS).reason is GrantRejection.WRONG_EPOCH


def test_no_private_key_material_persisted(tmp_path: Path) -> None:
    seed = b"\x11" * 32
    signer = PeerIdentity.from_private_key(Ed25519PrivateKey.from_private_bytes(seed))
    ids = [signer, *(PeerIdentity.generate() for _ in range(3))]
    members = [
        {
            "peer_id": ident.peer_id,
            "public_key": ident.fingerprint,
            "roles": ["validator"],
            "joined_epoch": 1,
        }
        for ident in ids
    ]
    membership = derive_genesis_membership(members, policy_version=POLICY)
    db_path = tmp_path / "keys.db"
    grant = grant_for(signer, membership)
    with DurableGrantStore(membership=membership, db_path=db_path) as store:
        store.record_issued(grant)
        store.revoke(signer, grant, reason_digest=REASON)
    raw = db_path.read_bytes()
    assert seed not in raw
    assert seed.hex().encode() not in raw
    seed_material = signer.key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    assert seed_material not in raw
    with sqlite3.connect(db_path) as conn:
        columns = {
            row[1]
            for table in ("used_nonces", "issued_grants", "revoked_grants")
            for row in conn.execute(f"PRAGMA table_info({table})")
        }
    assert columns.isdisjoint({"key", "private_key", "secret", "seed", "signature"})


async def test_async_context_manager_closes_connection() -> None:
    ids, membership = setup4()
    grant = grant_for(ids[0], membership)
    async with DurableGrantStore(membership=membership) as store:
        assert store.used_nonces == frozenset()
    with pytest.raises(GrantStoreError):
        store.record_issued(grant)
