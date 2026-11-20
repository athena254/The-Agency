"""Hermetic actuator-fencing tests: verify, fence, execute once, replay in process."""

from __future__ import annotations

from dataclasses import replace

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agency.consensus.fencing import (
    FencedActuator,
    FencingRejection,
    fencing_from_payload,
    fencing_from_state_token,
)
from agency.consensus.grants import ExecutionGrant, GrantLedger, GrantRejection
from agency.peers.canonical import canonical_json, digest_json
from agency.peers.identities import PeerIdentity, consumer_id_from_ingress
from agency.peers.membership import Membership, derive_genesis_membership

POLICY = "peer-policy/1"
ISSUED_AT_MS = 1000
EXPIRES_AT_MS = ISSUED_AT_MS + 60_000
BFT_TIME_MS = 2000
PROPOSAL_ID = "prop_" + "a" * 32
RESOURCE = "resource-alpha"
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


class SpyActuator:
    """Records each execution instead of touching any real resource."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, grant: ExecutionGrant) -> str:
        self.calls.append(grant.task_id)
        return f"executed:{grant.task_id}"


def make_actuator(ledger: GrantLedger) -> tuple[FencedActuator, SpyActuator]:
    spy = SpyActuator()
    return FencedActuator(ledger, actuator=spy), spy


def issue(ids: list[PeerIdentity], ledger: GrantLedger, task_id: str = "task-1") -> ExecutionGrant:
    return ledger.issue(
        ids[0],
        proposal_id=PROPOSAL_ID,
        task_id=task_id,
        issued_at_ms=ISSUED_AT_MS,
        expires_at_ms=EXPIRES_AT_MS,
        affected_consumers=(consumer_id_from_ingress(ingress="telegram", subject="alice"),),
    )


def test_successful_execution_advances_resource_token() -> None:
    ids, _membership, ledger = setup4()
    actuator, spy = make_actuator(ledger)
    grant = issue(ids, ledger)

    attempt = actuator.execute(
        grant,
        bft_time_ms=BFT_TIME_MS,
        resource_id=RESOURCE,
        fencing=fencing_from_state_token(0),
    )

    assert attempt.accepted
    assert attempt.executed
    assert attempt.rejection is None
    assert attempt.verification is not None
    assert attempt.verification.accepted
    assert attempt.outcome == "executed:task-1"
    assert actuator.token(RESOURCE) == 1
    assert spy.calls == ["task-1"]


def test_replay_returns_stored_outcome_without_second_actuator_call() -> None:
    ids, _membership, ledger = setup4()
    actuator, spy = make_actuator(ledger)
    grant = issue(ids, ledger)
    fencing = fencing_from_state_token(0)

    first = actuator.execute(grant, bft_time_ms=BFT_TIME_MS, resource_id=RESOURCE, fencing=fencing)
    replay = actuator.execute(grant, bft_time_ms=BFT_TIME_MS, resource_id=RESOURCE, fencing=fencing)

    assert first.executed
    assert not replay.executed
    assert replay.accepted
    assert replay.outcome == first.outcome
    assert replay.receipt == first.receipt
    assert spy.calls == ["task-1"]


def test_stale_token_rejected() -> None:
    ids, _membership, ledger = setup4()
    actuator, spy = make_actuator(ledger)
    first_grant = issue(ids, ledger, task_id="task-1")
    second_grant = issue(ids, ledger, task_id="task-2")

    first = actuator.execute(
        first_grant,
        bft_time_ms=BFT_TIME_MS,
        resource_id=RESOURCE,
        fencing=fencing_from_state_token(0),
    )
    stale = actuator.execute(
        second_grant,
        bft_time_ms=BFT_TIME_MS,
        resource_id=RESOURCE,
        fencing=fencing_from_state_token(0),
    )

    assert first.executed
    assert not stale.accepted
    assert stale.rejection is FencingRejection.STALE_TOKEN
    assert stale.receipt is None
    assert actuator.token(RESOURCE) == 1
    assert spy.calls == ["task-1"]


def test_conflicting_outcome_rejected() -> None:
    ids, _membership, ledger = setup4()
    actuator, spy = make_actuator(ledger)
    grant = issue(ids, ledger)
    fencing = fencing_from_state_token(0)

    first = actuator.execute(grant, bft_time_ms=BFT_TIME_MS, resource_id=RESOURCE, fencing=fencing)
    conflict = actuator.execute(
        grant,
        bft_time_ms=BFT_TIME_MS,
        resource_id=RESOURCE,
        fencing=fencing,
        expected_outcome="executed:something-else",
    )

    assert first.executed
    assert not conflict.accepted
    assert conflict.rejection is FencingRejection.CONFLICTING_OUTCOME
    assert conflict.outcome == first.outcome
    assert actuator.receipt(grant.digest) == first.receipt
    assert spy.calls == ["task-1"]


def test_expired_grant_rejected() -> None:
    ids, _membership, ledger = setup4()
    actuator, spy = make_actuator(ledger)
    grant = issue(ids, ledger)

    attempt = actuator.execute(
        grant,
        bft_time_ms=EXPIRES_AT_MS,
        resource_id=RESOURCE,
        fencing=fencing_from_state_token(0),
    )

    assert not attempt.accepted
    assert not attempt.executed
    assert attempt.verification is not None
    assert attempt.verification.reason is GrantRejection.EXPIRED
    assert actuator.receipts == ()
    assert spy.calls == []


def test_revoked_grant_rejected() -> None:
    ids, _membership, ledger = setup4()
    actuator, spy = make_actuator(ledger)
    grant = issue(ids, ledger)
    ledger.revoke(ids[0], grant, reason_digest=REASON)

    attempt = actuator.execute(
        grant,
        bft_time_ms=BFT_TIME_MS,
        resource_id=RESOURCE,
        fencing=fencing_from_state_token(0),
    )

    assert not attempt.accepted
    assert not attempt.executed
    assert attempt.verification is not None
    assert attempt.verification.reason is GrantRejection.REVOKED
    assert actuator.receipts == ()
    assert spy.calls == []


def test_missing_attestation_rejected() -> None:
    ids, _membership, ledger = setup4()
    actuator, spy = make_actuator(ledger)
    grant = issue(ids, ledger)
    missing = replace(grant, attestation=None)

    attempt = actuator.execute(
        missing,
        bft_time_ms=BFT_TIME_MS,
        resource_id=RESOURCE,
        fencing=fencing_from_state_token(0),
    )

    assert not attempt.accepted
    assert not attempt.executed
    assert attempt.verification is not None
    assert attempt.verification.reason is GrantRejection.MISSING_ATTESTATION
    assert actuator.receipts == ()
    assert spy.calls == []


def test_receipt_binds_grant_resource_token_and_outcome() -> None:
    ids, _membership, ledger = setup4()
    actuator, _spy = make_actuator(ledger)
    grant = issue(ids, ledger)

    attempt = actuator.execute(
        grant,
        bft_time_ms=BFT_TIME_MS,
        resource_id=RESOURCE,
        fencing=fencing_from_state_token(4),
    )
    receipt = attempt.receipt
    assert receipt is not None
    assert receipt.grant_digest == grant.digest
    assert receipt.resource_id == RESOURCE
    assert receipt.fencing_token == 5
    assert receipt.outcome == "executed:task-1"
    assert receipt.canonical() == canonical_json(receipt.body())
    assert receipt.digest() == digest_json(receipt.body())
    assert actuator.receipts == (receipt,)

    changed = replace(receipt, outcome="different")
    assert changed.digest() != receipt.digest()


def test_deterministic_replay_and_payload_derived_fencing() -> None:
    ids, membership, ledger = setup4()
    grant = issue(ids, ledger)
    first, first_spy = make_actuator(ledger)
    second, second_spy = make_actuator(GrantLedger(membership=membership))
    payload = {"expected_current": 0, "next_token": 1}

    from_payload = first.execute(
        grant,
        bft_time_ms=BFT_TIME_MS,
        resource_id=RESOURCE,
        fencing=fencing_from_payload(payload),
    )
    from_state = second.execute(
        grant,
        bft_time_ms=BFT_TIME_MS,
        resource_id=RESOURCE,
        fencing=fencing_from_state_token(0),
    )

    assert from_payload.receipt == from_state.receipt
    assert first.receipts == second.receipts
    assert [receipt.digest() for receipt in first.receipts] == [
        receipt.digest() for receipt in second.receipts
    ]
    assert first_spy.calls == second_spy.calls == ["task-1"]

    replay = first.execute(
        grant,
        bft_time_ms=BFT_TIME_MS,
        resource_id=RESOURCE,
        fencing=fencing_from_payload(payload),
    )
    assert replay.receipt == from_payload.receipt
    assert replay.receipt is not None
    assert replay.receipt.digest() == digest_json(from_payload.receipt.body())
    assert first_spy.calls == ["task-1"]
