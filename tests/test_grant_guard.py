"""Point-of-use actuator grant guard tests (hermetic; no clock, network or effect)."""

from __future__ import annotations

from dataclasses import replace
from typing import cast

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agency.consensus.grant_guard import enforce_grant
from agency.consensus.grants import (
    ExecutionGrant,
    GrantLedger,
    GrantRejection,
    issue_grant,
)
from agency.peers.canonical import digest_json
from agency.peers.identities import PeerIdentity, consumer_id_from_ingress
from agency.peers.membership import Membership, derive_genesis_membership

POLICY = "peer-policy/1"
ISSUED_AT_MS = 1000
EXPIRES_AT_MS = ISSUED_AT_MS + 60_000
BFT_TIME_MS = 2000
PROPOSAL_ID = "prop_" + "a" * 32


def _membership(peers: list[PeerIdentity], *, policy_version: str = POLICY) -> Membership:
    return derive_genesis_membership(
        [
            {
                "peer_id": peer.peer_id,
                "public_key": peer.fingerprint,
                "roles": ["validator"],
                "joined_epoch": 1,
            }
            for peer in peers
        ],
        policy_version=policy_version,
    )


def _setup() -> tuple[list[PeerIdentity], Membership, GrantLedger]:
    peers = [PeerIdentity.from_private_key(Ed25519PrivateKey.generate()) for _ in range(4)]
    membership = _membership(peers)
    return peers, membership, GrantLedger(membership=membership)


def _consumers() -> tuple[str, ...]:
    return (
        consumer_id_from_ingress(ingress="telegram", subject="alice"),
        consumer_id_from_ingress(ingress="telegram", subject="bob"),
    )


def _grant(
    ledger: GrantLedger,
    issuer: PeerIdentity,
    *,
    task_id: str = "task-1",
    issued_at_ms: int = ISSUED_AT_MS,
    expires_at_ms: int = EXPIRES_AT_MS,
) -> ExecutionGrant:
    return ledger.issue(
        issuer,
        proposal_id=PROPOSAL_ID,
        task_id=task_id,
        issued_at_ms=issued_at_ms,
        expires_at_ms=expires_at_ms,
        affected_consumers=_consumers(),
    )


class _NotAGrant:
    digest = "0" * 64


def test_valid_grant_accepted_once() -> None:
    peers, _membership_set, ledger = _setup()
    grant = _grant(ledger, peers[0])

    first = enforce_grant(grant, ledger=ledger, bft_time_ms=BFT_TIME_MS)
    second = enforce_grant(grant, ledger=ledger, bft_time_ms=BFT_TIME_MS)

    assert first.ok
    assert first.accepted
    assert first.reason is None
    assert first.grant_digest == grant.digest
    assert not second.ok
    assert second.reason is GrantRejection.REPLAY


def test_replayed_grant_rejected() -> None:
    peers, membership, ledger = _setup()
    original = _grant(ledger, peers[0])
    assert enforce_grant(original, ledger=ledger, bft_time_ms=BFT_TIME_MS).ok

    duplicate = issue_grant(
        peers[0],
        membership=membership,
        proposal_id=PROPOSAL_ID,
        task_id="task-1",
        issued_at_ms=ISSUED_AT_MS,
        expires_at_ms=EXPIRES_AT_MS,
        affected_consumers=_consumers(),
        nonce=original.nonce,
    )
    assert duplicate.digest == original.digest
    replay = enforce_grant(duplicate, ledger=ledger, bft_time_ms=BFT_TIME_MS)
    assert not replay.ok
    assert replay.reason is GrantRejection.REPLAY


def test_stale_expired_and_revoked_grants_rejected() -> None:
    peers, _membership_set, ledger = _setup()
    grant = _grant(ledger, peers[0])

    expired = enforce_grant(grant, ledger=ledger, bft_time_ms=EXPIRES_AT_MS)
    assert not expired.ok
    assert expired.reason is GrantRejection.EXPIRED
    assert expired.grant_digest == grant.digest

    fresh = _grant(ledger, peers[0], task_id="task-2", issued_at_ms=2000, expires_at_ms=62000)
    assert enforce_grant(fresh, ledger=ledger, bft_time_ms=2500).ok

    old = _grant(ledger, peers[1], task_id="task-3")
    ledger.revoke(peers[0], old, reason_digest=digest_json({"reason": "stale"}))
    revoked = enforce_grant(old, ledger=ledger, bft_time_ms=BFT_TIME_MS)
    assert not revoked.ok
    assert revoked.reason is GrantRejection.REVOKED


def test_foreign_authority_rejected() -> None:
    peers, _membership_set, ledger = _setup()
    outsider = PeerIdentity.generate()
    foreign_membership = _membership([outsider, *peers[1:]])
    foreign = issue_grant(
        outsider,
        membership=foreign_membership,
        proposal_id=PROPOSAL_ID,
        task_id="task-1",
        issued_at_ms=ISSUED_AT_MS,
        expires_at_ms=EXPIRES_AT_MS,
        affected_consumers=_consumers(),
    )

    unauthorized = enforce_grant(foreign, ledger=ledger, bft_time_ms=BFT_TIME_MS)
    assert not unauthorized.ok
    assert unauthorized.reason is GrantRejection.UNAUTHORIZED_ISSUER

    other_epoch_ledger = GrantLedger(membership=_membership(peers, policy_version="peer-policy/2"))
    local = _grant(ledger, peers[0])
    stale_epoch = enforce_grant(local, ledger=other_epoch_ledger, bft_time_ms=BFT_TIME_MS)
    assert not stale_epoch.ok
    assert stale_epoch.reason is GrantRejection.WRONG_EPOCH

    legit = _grant(ledger, peers[0])
    assert enforce_grant(legit, ledger=ledger, bft_time_ms=BFT_TIME_MS).ok


def test_malformed_and_tampered_grants_rejected() -> None:
    peers, _membership_set, ledger = _setup()
    grant = _grant(ledger, peers[0])

    bad_signature = replace(grant, signature=b"\x00" * 64)
    bad_task = replace(grant, task_id="task-tampered")
    malformed = enforce_grant(
        cast(ExecutionGrant, _NotAGrant()), ledger=ledger, bft_time_ms=BFT_TIME_MS
    )

    bad_signature_result = enforce_grant(bad_signature, ledger=ledger, bft_time_ms=BFT_TIME_MS)
    assert not bad_signature_result.ok
    assert bad_signature_result.reason is GrantRejection.INVALID_SIGNATURE
    assert (
        enforce_grant(bad_task, ledger=ledger, bft_time_ms=BFT_TIME_MS).reason
        is GrantRejection.INVALID_SIGNATURE
    )
    assert not malformed.ok
    assert malformed.reason is GrantRejection.MALFORMED

    assert enforce_grant(grant, ledger=ledger, bft_time_ms=BFT_TIME_MS).ok
