"""Tier6 B0/B1 wire primitives: grant payload reconstruction + genesis seeding.

Ported/adapted from feat/consensus-wave1 ``136c615`` for gaps.md #18: the HTTP
dependency rebuilds an ``ExecutionGrant`` from the ``X-Agency-Grant`` payload
and the API lifespan seeds the peer registry from the pinned genesis file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agency.consensus.genesis import load_genesis
from agency.consensus.grants import (
    ExecutionGrant,
    GrantError,
    GrantLedger,
    GrantRejection,
    grant_from_payload,
    verify_at_actuator,
)
from agency.peers.authority import AuthorityError, PeerRegistry
from agency.peers.identities import PeerIdentity
from agency.peers.membership import Membership, derive_genesis_membership

POLICY = "peer-policy/1"
PROPOSAL_ID = "prop_" + "a" * 32
ISSUED_AT_MS = 1000
EXPIRES_AT_MS = 61_000
BFT_TIME_MS = 2_000


def setup4() -> tuple[list[PeerIdentity], Membership, GrantLedger]:
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
    membership = derive_genesis_membership(members, policy_version=POLICY)
    return ids, membership, GrantLedger(membership=membership)


def wire_payload(grant: ExecutionGrant) -> dict[str, object]:
    attestation = grant.attestation
    assert attestation is not None
    return {
        "proposal_id": grant.proposal_id,
        "task_id": grant.task_id,
        "issuer_peer_id": grant.issuer_peer_id,
        "membership_epoch": grant.membership_epoch,
        "membership_root": grant.membership_root,
        "nonce": grant.nonce,
        "issued_at_ms": grant.issued_at_ms,
        "expires_at_ms": grant.expires_at_ms,
        "affected_consumers": list(grant.affected_consumers),
        "attestation": {
            "attestor_peer_id": attestation.attestor_peer_id,
            "signature": attestation.signature.hex(),
        },
        "signature": grant.signature.hex(),
    }


def test_round_trip_reconstruction_verifies() -> None:
    ids, _membership, ledger = setup4()
    grant = ledger.issue(
        ids[0],
        proposal_id=PROPOSAL_ID,
        task_id="task-1",
        issued_at_ms=ISSUED_AT_MS,
        expires_at_ms=EXPIRES_AT_MS,
        affected_consumers=(),
    )
    rebuilt = grant_from_payload(wire_payload(grant))
    assert rebuilt.digest == grant.digest
    verification = verify_at_actuator(rebuilt, ledger=ledger, bft_time_ms=BFT_TIME_MS)
    assert verification.ok
    assert verification.reason is None


def test_round_trip_of_tampered_payload_rejected() -> None:
    ids, _membership, ledger = setup4()
    grant = ledger.issue(
        ids[0],
        proposal_id=PROPOSAL_ID,
        task_id="task-1",
        issued_at_ms=ISSUED_AT_MS,
        expires_at_ms=EXPIRES_AT_MS,
        affected_consumers=(),
    )
    payload = wire_payload(grant)
    payload["task_id"] = "task-2"  # signed content, not re-signed
    rebuilt = grant_from_payload(payload)
    verification = verify_at_actuator(rebuilt, ledger=ledger, bft_time_ms=BFT_TIME_MS)
    assert not verification.ok
    assert verification.reason is GrantRejection.INVALID_SIGNATURE


def test_payload_field_set_is_pinned() -> None:
    ids, _membership, ledger = setup4()
    grant = ledger.issue(
        ids[0],
        proposal_id=PROPOSAL_ID,
        task_id="task-1",
        issued_at_ms=ISSUED_AT_MS,
        expires_at_ms=EXPIRES_AT_MS,
        affected_consumers=(),
    )
    payload = wire_payload(grant)
    del payload["nonce"]
    with pytest.raises(GrantError, match="field set"):
        grant_from_payload(payload)
    with pytest.raises(GrantError, match="object"):
        grant_from_payload(["not", "an", "object"])


def test_payload_bad_signature_encodings_rejected() -> None:
    ids, _membership, ledger = setup4()
    grant = ledger.issue(
        ids[0],
        proposal_id=PROPOSAL_ID,
        task_id="task-1",
        issued_at_ms=ISSUED_AT_MS,
        expires_at_ms=EXPIRES_AT_MS,
        affected_consumers=(),
    )
    payload = wire_payload(grant)
    payload["signature"] = "zz-not-hex"
    with pytest.raises(GrantError, match="grant signature encoding"):
        grant_from_payload(payload)
    payload["signature"] = grant.signature.hex()
    assert isinstance(payload["attestation"], dict)
    payload["attestation"]["signature"] = "zz-not-hex"
    with pytest.raises(GrantError, match="attestation signature encoding"):
        grant_from_payload(payload)


def test_genesis_round_trip_and_validation(tmp_path: Path) -> None:
    ids, membership, _ledger = setup4()
    members = [
        {
            "peer_id": ident.peer_id,
            "public_key": ident.fingerprint,
            "roles": ["validator"],
            "joined_epoch": 1,
        }
        for ident in ids
    ]
    path = tmp_path / "genesis.json"
    path.write_text(json.dumps({"policy_version": POLICY, "members": members}), encoding="utf-8")
    loaded = load_genesis(path)
    assert loaded.epoch == membership.epoch
    assert loaded.root == membership.root
    assert {m.peer_id for m in loaded.members} == {i.peer_id for i in ids}

    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"policy_version": POLICY, "members": members[:3]}), encoding="utf-8")
    with pytest.raises(ValueError, match="exactly 4"):
        load_genesis(bad)


def test_registry_seeds_public_keys_from_genesis(tmp_path: Path) -> None:
    ids, _membership, _ledger = setup4()
    registry = PeerRegistry()
    for ident in ids:
        registry.register_public_key(ident.peer_id, ident.fingerprint)
    for ident in ids:
        assert registry.get(ident.peer_id) is not None
        assert registry.get(ident.peer_id).fingerprint == ident.fingerprint

    with pytest.raises(AuthorityError, match="mismatch"):
        registry.register_public_key(ids[0].peer_id, ids[1].fingerprint)
