"""Hermetic Slice C execution-grant tests (no clock, network, or actuator)."""

from __future__ import annotations

from dataclasses import replace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agency.consensus.grants import (
    GrantError,
    GrantLedger,
    GrantRejection,
    issue_grant,
    revoke_grant,
    verify_at_actuator,
    verify_grant,
    verify_revocation,
)
from agency.peers.canonical import digest_json
from agency.peers.identities import PeerIdentity, consumer_id_from_ingress
from agency.peers.membership import Membership, derive_genesis_membership

POLICY = "peer-policy/1"
ISSUED_AT_MS = 1000
EXPIRES_AT_MS = ISSUED_AT_MS + 60_000
BFT_TIME_MS = 2000
PROPOSAL_ID = "prop_" + "a" * 32
REASON = digest_json({"reason": "peer revoked"})


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


def consumers() -> tuple[str, ...]:
    return (
        consumer_id_from_ingress(ingress="telegram", subject="alice"),
        consumer_id_from_ingress(ingress="telegram", subject="bob"),
    )


def test_grant_happy_path_through_actuator_seam() -> None:
    ids, _membership, ledger = setup4()
    grant = ledger.issue(
        ids[0],
        proposal_id=PROPOSAL_ID,
        task_id="task-1",
        issued_at_ms=ISSUED_AT_MS,
        expires_at_ms=EXPIRES_AT_MS,
        affected_consumers=consumers(),
    )

    result = verify_at_actuator(grant, ledger=ledger, bft_time_ms=BFT_TIME_MS)

    assert result.ok
    assert result.accepted
    assert result.reason is None
    assert result.grant_digest == grant.digest
    assert grant.affected_consumers == tuple(sorted(consumers()))
    assert len(grant.nonce) == 32


def test_fail_closed_on_wrong_epoch() -> None:
    ids, membership, ledger = setup4()
    grant = ledger.issue(
        ids[0],
        proposal_id=PROPOSAL_ID,
        task_id="task-1",
        issued_at_ms=ISSUED_AT_MS,
        expires_at_ms=EXPIRES_AT_MS,
        affected_consumers=consumers(),
    )

    assert ledger.verify(grant, bft_time_ms=BFT_TIME_MS).ok

    wrong_epoch = verify_grant(
        grant,
        expected_epoch=membership.epoch + 1,
        expected_root=membership.root,
        bft_time_ms=BFT_TIME_MS,
        pinned_issuers=ledger.pinned_issuers,
    )
    wrong_root = verify_grant(
        grant,
        expected_epoch=membership.epoch,
        expected_root="0" * 64,
        bft_time_ms=BFT_TIME_MS,
        pinned_issuers=ledger.pinned_issuers,
    )
    assert wrong_epoch.reason is GrantRejection.WRONG_EPOCH
    assert wrong_root.reason is GrantRejection.WRONG_EPOCH


def test_fail_closed_on_expiry() -> None:
    ids, _membership, ledger = setup4()
    grant = ledger.issue(
        ids[0],
        proposal_id=PROPOSAL_ID,
        task_id="task-1",
        issued_at_ms=ISSUED_AT_MS,
        expires_at_ms=EXPIRES_AT_MS,
        affected_consumers=consumers(),
    )

    at_expiry = ledger.verify(grant, bft_time_ms=EXPIRES_AT_MS)
    after_expiry = ledger.verify(grant, bft_time_ms=EXPIRES_AT_MS + 1)

    assert at_expiry.reason is GrantRejection.EXPIRED
    assert after_expiry.reason is GrantRejection.EXPIRED


def test_fail_closed_on_replayed_nonce() -> None:
    ids, _membership, ledger = setup4()
    grant = ledger.issue(
        ids[0],
        proposal_id=PROPOSAL_ID,
        task_id="task-1",
        issued_at_ms=ISSUED_AT_MS,
        expires_at_ms=EXPIRES_AT_MS,
        affected_consumers=consumers(),
    )
    assert ledger.verify(grant, bft_time_ms=BFT_TIME_MS).ok

    ledger.record_used(grant)

    assert ledger.verify(grant, bft_time_ms=BFT_TIME_MS).reason is GrantRejection.REPLAY


def test_fail_closed_on_missing_affected_set_attestation() -> None:
    ids, _membership, ledger = setup4()
    grant = ledger.issue(
        ids[0],
        proposal_id=PROPOSAL_ID,
        task_id="task-1",
        issued_at_ms=ISSUED_AT_MS,
        expires_at_ms=EXPIRES_AT_MS,
        affected_consumers=consumers(),
    )

    absent = replace(grant, attestation=None)
    assert absent.digest != grant.digest
    assert ledger.verify(absent, bft_time_ms=BFT_TIME_MS).reason is (
        GrantRejection.MISSING_ATTESTATION
    )

    assert grant.attestation is not None
    wrong_attestor = replace(
        grant,
        attestation=replace(grant.attestation, attestor_peer_id=ids[1].peer_id),
    )
    assert ledger.verify(wrong_attestor, bft_time_ms=BFT_TIME_MS).reason is (
        GrantRejection.MISSING_ATTESTATION
    )


def test_fail_closed_on_tampered_signature_and_payload() -> None:
    ids, _membership, ledger = setup4()
    grant = ledger.issue(
        ids[0],
        proposal_id=PROPOSAL_ID,
        task_id="task-1",
        issued_at_ms=ISSUED_AT_MS,
        expires_at_ms=EXPIRES_AT_MS,
        affected_consumers=consumers(),
    )

    bad_signature = replace(grant, signature=b"\x00" * 64)
    bad_task = replace(grant, task_id="task-tampered")
    assert ledger.verify(bad_signature, bft_time_ms=BFT_TIME_MS).reason is (
        GrantRejection.INVALID_SIGNATURE
    )
    assert ledger.verify(bad_task, bft_time_ms=BFT_TIME_MS).reason is (
        GrantRejection.INVALID_SIGNATURE
    )


def test_revocation_and_tampered_revocation() -> None:
    ids, _membership, ledger = setup4()
    grant = ledger.issue(
        ids[0],
        proposal_id=PROPOSAL_ID,
        task_id="task-1",
        issued_at_ms=ISSUED_AT_MS,
        expires_at_ms=EXPIRES_AT_MS,
        affected_consumers=consumers(),
    )

    revocation = revoke_grant(ids[0], grant, reason_digest=REASON)
    assert verify_revocation(revocation, pinned_issuers=ledger.pinned_issuers)
    assert ledger.verify(grant, bft_time_ms=BFT_TIME_MS).ok

    ledger.revoke(ids[0], grant, reason_digest=REASON)
    assert ledger.verify(grant, bft_time_ms=BFT_TIME_MS).reason is GrantRejection.REVOKED

    tampered = replace(revocation, signature=b"\x00" * 64)
    assert not verify_revocation(tampered, pinned_issuers=ledger.pinned_issuers)


def test_unauthorized_issuer_and_overlong_lifetime() -> None:
    ids, membership, ledger = setup4()
    outsider = PeerIdentity.generate()
    with pytest.raises(GrantError):
        ledger.issue(
            outsider,
            proposal_id=PROPOSAL_ID,
            task_id="task-1",
            issued_at_ms=ISSUED_AT_MS,
            expires_at_ms=EXPIRES_AT_MS,
            affected_consumers=consumers(),
        )
    with pytest.raises(GrantError):
        issue_grant(
            ids[0],
            membership=membership,
            proposal_id=PROPOSAL_ID,
            task_id="task-1",
            issued_at_ms=ISSUED_AT_MS,
            expires_at_ms=ISSUED_AT_MS + 3_600_001,
            affected_consumers=consumers(),
        )

    grant = ledger.issue(
        ids[0],
        proposal_id=PROPOSAL_ID,
        task_id="task-1",
        issued_at_ms=ISSUED_AT_MS,
        expires_at_ms=EXPIRES_AT_MS,
        affected_consumers=consumers(),
    )
    assert ledger.verify(grant, bft_time_ms=BFT_TIME_MS).ok


def test_no_wall_clock_and_order_independent_affected_set() -> None:
    ids, _membership, ledger = setup4()
    audience = consumers()

    forward = ledger.issue(
        ids[0],
        proposal_id=PROPOSAL_ID,
        task_id="task-1",
        issued_at_ms=ISSUED_AT_MS,
        expires_at_ms=EXPIRES_AT_MS,
        affected_consumers=audience,
    )
    reverse = ledger.issue(
        ids[0],
        proposal_id=PROPOSAL_ID,
        task_id="task-1",
        issued_at_ms=ISSUED_AT_MS,
        expires_at_ms=EXPIRES_AT_MS,
        affected_consumers=tuple(reversed(audience)),
    )

    assert forward.nonce == reverse.nonce
    assert forward.digest == reverse.digest
    assert forward.signature == reverse.signature
    assert ledger.verify(forward, bft_time_ms=BFT_TIME_MS) == ledger.verify(
        forward, bft_time_ms=BFT_TIME_MS
    )
