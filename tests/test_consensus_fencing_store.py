"""Hermetic tests for durable resource-side fencing state and receipts."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agency.consensus.fencing import (
    FencedActuator,
    FencingRejection,
    FencingStateStore,
    FencingStoreError,
    FinalityReceipt,
    fencing_from_state_token,
)
from agency.consensus.grants import ExecutionGrant, GrantLedger
from agency.peers.identities import PeerIdentity, consumer_id_from_ingress
from agency.peers.membership import Membership, derive_genesis_membership

POLICY = "peer-policy/1"
ISSUED_AT_MS = 1000
EXPIRES_AT_MS = ISSUED_AT_MS + 60_000
BFT_TIME_MS = 2000
PROPOSAL_ID = "prop_" + "a" * 32
RESOURCE = "resource-alpha"
OTHER_RESOURCE = "resource-beta"


def setup() -> tuple[list[PeerIdentity], Membership, GrantLedger]:
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
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, grant: ExecutionGrant) -> str:
        self.calls.append(grant.task_id)
        return f"executed:{grant.task_id}"


def issue(ids: list[PeerIdentity], ledger: GrantLedger, task_id: str = "task-1") -> ExecutionGrant:
    return ledger.issue(
        ids[0],
        proposal_id=PROPOSAL_ID,
        task_id=task_id,
        issued_at_ms=ISSUED_AT_MS,
        expires_at_ms=EXPIRES_AT_MS,
        affected_consumers=(consumer_id_from_ingress(ingress="telegram", subject="alice"),),
    )


def seed(path: Path) -> tuple[ExecutionGrant, FinalityReceipt]:
    ids, _membership, ledger = setup()
    grant = issue(ids, ledger)
    with FencingStateStore(db_path=path) as store:
        attempt = FencedActuator(ledger, actuator=SpyActuator(), store=store).execute(
            grant,
            bft_time_ms=BFT_TIME_MS,
            resource_id=RESOURCE,
            fencing=fencing_from_state_token(0),
        )
    receipt = attempt.receipt
    assert receipt is not None
    return grant, receipt


def test_file_store_persists_tokens_receipts_and_log_across_reopen(tmp_path: Path) -> None:
    ids, _membership, ledger = setup()
    db_path = tmp_path / "fencing.db"
    spy = SpyActuator()
    with FencingStateStore(db_path=db_path) as store:
        actuator = FencedActuator(ledger, actuator=spy, store=store)
        grant = issue(ids, ledger)
        first = actuator.execute(
            grant,
            bft_time_ms=BFT_TIME_MS,
            resource_id=RESOURCE,
            fencing=fencing_from_state_token(0),
        )
        receipt = first.receipt
        assert first.executed
        assert receipt is not None
        assert store.token(RESOURCE) == 1
        assert store.receipt(grant.digest) == receipt
        assert store.receipts == (receipt,)

    with FencingStateStore(db_path=db_path) as reopened:
        assert reopened.token(RESOURCE) == 1
        assert reopened.receipt(grant.digest) == receipt
        assert reopened.receipts == (receipt,)
        assert [entry.digest() for entry in reopened.receipts] == [receipt.digest()]
    assert spy.calls == ["task-1"]


def test_replay_after_reopen_returns_stored_receipt_without_execution(tmp_path: Path) -> None:
    ids, membership, ledger = setup()
    db_path = tmp_path / "replay.db"
    grant = issue(ids, ledger)
    first_spy = SpyActuator()
    with FencingStateStore(db_path=db_path) as store:
        first = FencedActuator(ledger, actuator=first_spy, store=store).execute(
            grant,
            bft_time_ms=BFT_TIME_MS,
            resource_id=RESOURCE,
            fencing=fencing_from_state_token(0),
        )

    replay_spy = SpyActuator()
    with FencingStateStore(db_path=db_path) as reopened:
        replay = FencedActuator(
            GrantLedger(membership=membership), actuator=replay_spy, store=reopened
        ).execute(
            grant,
            bft_time_ms=BFT_TIME_MS,
            resource_id=RESOURCE,
            fencing=fencing_from_state_token(0),
        )

    assert first.receipt is not None
    assert first.executed
    assert not replay.executed
    assert replay.accepted
    assert replay.rejection is None
    assert replay.receipt == first.receipt
    assert replay.outcome == first.outcome
    assert first_spy.calls == ["task-1"]
    assert replay_spy.calls == []


def test_stale_token_rejected_after_reopen(tmp_path: Path) -> None:
    ids, membership, ledger = setup()
    db_path = tmp_path / "stale.db"
    first_grant = issue(ids, ledger, "task-1")
    first_spy = SpyActuator()
    with FencingStateStore(db_path=db_path) as store:
        first = FencedActuator(ledger, actuator=first_spy, store=store).execute(
            first_grant,
            bft_time_ms=BFT_TIME_MS,
            resource_id=RESOURCE,
            fencing=fencing_from_state_token(0),
        )
        assert first.executed

    second_ledger = GrantLedger(membership=membership)
    second_grant = issue(ids, second_ledger, "task-2")
    stale_spy = SpyActuator()
    with FencingStateStore(db_path=db_path) as reopened:
        stale = FencedActuator(second_ledger, actuator=stale_spy, store=reopened).execute(
            second_grant,
            bft_time_ms=BFT_TIME_MS,
            resource_id=RESOURCE,
            fencing=fencing_from_state_token(0),
        )
        assert reopened.token(RESOURCE) == 1

    assert not stale.accepted
    assert stale.rejection is FencingRejection.STALE_TOKEN
    assert stale.receipt is None
    assert stale_spy.calls == []


def test_conflicting_outcome_rejected_after_reopen(tmp_path: Path) -> None:
    ids, membership, ledger = setup()
    db_path = tmp_path / "conflict.db"
    grant = issue(ids, ledger)
    with FencingStateStore(db_path=db_path) as store:
        first = FencedActuator(ledger, actuator=SpyActuator(), store=store).execute(
            grant,
            bft_time_ms=BFT_TIME_MS,
            resource_id=RESOURCE,
            fencing=fencing_from_state_token(0),
        )

    conflict_spy = SpyActuator()
    with FencingStateStore(db_path=db_path) as reopened:
        conflict = FencedActuator(
            GrantLedger(membership=membership), actuator=conflict_spy, store=reopened
        ).execute(
            grant,
            bft_time_ms=BFT_TIME_MS,
            resource_id=RESOURCE,
            fencing=fencing_from_state_token(0),
            expected_outcome="executed:someone-else",
        )

    assert not conflict.accepted
    assert conflict.rejection is FencingRejection.CONFLICTING_OUTCOME
    assert conflict.receipt == first.receipt
    assert not conflict.executed
    assert conflict_spy.calls == []


def test_conflicting_resource_rejected_after_reopen(tmp_path: Path) -> None:
    ids, membership, ledger = setup()
    db_path = tmp_path / "resource.db"
    grant = issue(ids, ledger)
    with FencingStateStore(db_path=db_path) as store:
        first = FencedActuator(ledger, actuator=SpyActuator(), store=store).execute(
            grant,
            bft_time_ms=BFT_TIME_MS,
            resource_id=RESOURCE,
            fencing=fencing_from_state_token(0),
        )

    conflict_spy = SpyActuator()
    with FencingStateStore(db_path=db_path) as reopened:
        conflict = FencedActuator(
            GrantLedger(membership=membership), actuator=conflict_spy, store=reopened
        ).execute(
            grant,
            bft_time_ms=BFT_TIME_MS,
            resource_id=OTHER_RESOURCE,
            fencing=fencing_from_state_token(0),
        )

    assert not conflict.accepted
    assert conflict.rejection is FencingRejection.CONFLICTING_RESOURCE
    assert conflict.receipt == first.receipt
    assert conflict_spy.calls == []


@pytest.mark.parametrize(("column", "value"), [("token", 99), ("integrity_digest", "0" * 64)])
def test_tampered_resource_token_fails_closed(tmp_path: Path, column: str, value: object) -> None:
    db_path = tmp_path / "bad-token.db"
    seed(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            f"UPDATE resource_tokens SET {column} = ? WHERE resource_id = ?", (value, RESOURCE)
        )
        conn.commit()
    with pytest.raises(FencingStoreError):
        FencingStateStore(db_path=db_path)


def test_tampered_executed_receipt_fails_closed(tmp_path: Path) -> None:
    db_path = tmp_path / "bad-receipt.db"
    _grant, receipt = seed(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE executed_grants SET outcome = ? WHERE grant_digest = ?",
            ("tampered-outcome", receipt.grant_digest),
        )
        conn.commit()
    with pytest.raises(FencingStoreError):
        FencingStateStore(db_path=db_path)


@pytest.mark.parametrize("column", ["chain_digest", "integrity_digest"])
def test_tampered_receipt_log_fails_closed(tmp_path: Path, column: str) -> None:
    db_path = tmp_path / "bad-log.db"
    seed(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(f"UPDATE receipt_log SET {column} = ?", ("0" * 64,))
        conn.commit()
    with pytest.raises(FencingStoreError):
        FencingStateStore(db_path=db_path)


def test_deleted_receipt_log_entry_fails_closed(tmp_path: Path) -> None:
    db_path = tmp_path / "short-log.db"
    seed(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM receipt_log")
        conn.commit()
    with pytest.raises(FencingStoreError):
        FencingStateStore(db_path=db_path)


def test_in_memory_store_enforces_idempotency_and_fails_closed_when_closed() -> None:
    ids, _membership, ledger = setup()
    spy = SpyActuator()
    with FencingStateStore() as store:
        actuator = FencedActuator(ledger, actuator=spy, store=store)
        grant = issue(ids, ledger)
        first = actuator.execute(
            grant,
            bft_time_ms=BFT_TIME_MS,
            resource_id=RESOURCE,
            fencing=fencing_from_state_token(0),
        )
        replay = actuator.execute(
            grant,
            bft_time_ms=BFT_TIME_MS,
            resource_id=RESOURCE,
            fencing=fencing_from_state_token(0),
        )
        assert first.executed
        assert not replay.executed
        assert replay.receipt == first.receipt
        assert store.receipts == (first.receipt,)
        assert spy.calls == ["task-1"]

    with pytest.raises(FencingStoreError):
        actuator.execute(
            issue(ids, ledger, "task-2"),
            bft_time_ms=BFT_TIME_MS,
            resource_id=RESOURCE,
            fencing=fencing_from_state_token(1),
        )
    assert spy.calls == ["task-1"]


def test_legacy_in_memory_behavior_is_unchanged() -> None:
    ids, membership, ledger = setup()
    backed_ledger = GrantLedger(membership=membership)
    grant = issue(ids, ledger)
    backed_grant = issue(ids, backed_ledger)
    assert backed_grant.digest == grant.digest
    plain_spy, backed_spy = SpyActuator(), SpyActuator()
    plain = FencedActuator(ledger, actuator=plain_spy)
    with FencingStateStore() as store:
        backed = FencedActuator(backed_ledger, actuator=backed_spy, store=store)
        first_plain = plain.execute(
            grant,
            bft_time_ms=BFT_TIME_MS,
            resource_id=RESOURCE,
            fencing=fencing_from_state_token(0),
        )
        first_backed = backed.execute(
            backed_grant,
            bft_time_ms=BFT_TIME_MS,
            resource_id=RESOURCE,
            fencing=fencing_from_state_token(0),
        )
        assert first_backed.receipt == first_plain.receipt
        assert backed.receipts == plain.receipts
        assert backed.token(RESOURCE) == plain.token(RESOURCE) == 1

        replay_plain = plain.execute(
            grant,
            bft_time_ms=BFT_TIME_MS,
            resource_id=RESOURCE,
            fencing=fencing_from_state_token(0),
        )
        replay_backed = backed.execute(
            backed_grant,
            bft_time_ms=BFT_TIME_MS,
            resource_id=RESOURCE,
            fencing=fencing_from_state_token(0),
        )
        assert replay_backed.receipt == replay_plain.receipt
        assert not replay_backed.executed
        assert not replay_plain.executed

        stale_plain = plain.execute(
            issue(ids, ledger, "task-2"),
            bft_time_ms=BFT_TIME_MS,
            resource_id=RESOURCE,
            fencing=fencing_from_state_token(0),
        )
        stale_backed = backed.execute(
            issue(ids, backed_ledger, "task-2"),
            bft_time_ms=BFT_TIME_MS,
            resource_id=RESOURCE,
            fencing=fencing_from_state_token(0),
        )
        assert stale_plain.rejection is FencingRejection.STALE_TOKEN
        assert stale_backed.rejection is FencingRejection.STALE_TOKEN
        assert plain_spy.calls == backed_spy.calls == ["task-1"]
    assert plain.receipts == (first_plain.receipt,)


async def test_async_context_manager_closes_store() -> None:
    store = FencingStateStore()
    async with store:
        assert store.token(RESOURCE) == 0
    with pytest.raises(FencingStoreError):
        store.ensure_token(RESOURCE, 0)
