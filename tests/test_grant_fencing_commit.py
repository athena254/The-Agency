"""Hermetic tests for the grant-bound downstream fencing commit (gaps.md #18).

Covers :class:`agency.consensus.fencing_commit.GrantFencingCommit` in
isolation: token commit + idempotent receipt replay, post-intake revocation
refusal, store-error refusal, the stale-epoch refusal when a duplicate digest
commits against a store that already executed it, and durable replay across a
store reopen. Orchestrator-level wiring is covered in
``tests/test_grant_fencing_execution.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agency.consensus.fencing import FencingStateStore, FencingStoreError
from agency.consensus.fencing_commit import FencingCommitOutcome, GrantFencingCommit
from agency.consensus.grants import GrantLedger
from agency.peers.canonical import digest_json
from agency.peers.identities import PeerIdentity
from agency.peers.membership import Membership, derive_genesis_membership

POLICY = "peer-policy/1"
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
TASK = "11111111-2222-3333-4444-555555555555"


def _genesis() -> tuple[list[PeerIdentity], Membership]:
    ids = [PeerIdentity.from_private_key(Ed25519PrivateKey.generate()) for _ in range(4)]
    members = [
        {
            "peer_id": identity.peer_id,
            "public_key": identity.fingerprint,
            "roles": ["validator"],
            "joined_epoch": 1,
        }
        for identity in ids
    ]
    return ids, derive_genesis_membership(members, policy_version=POLICY)


def _fencer(tmp_path: Path | None = None, ledger: GrantLedger | None = None) -> GrantFencingCommit:
    store = FencingStateStore(db_path=tmp_path / "fencing.db" if tmp_path else ":memory:")
    return GrantFencingCommit(store=store, ledger=ledger)


def _issue(ledger: GrantLedger, issuer: PeerIdentity, proposal: str) -> Any:
    return ledger.issue(
        issuer,
        proposal_id=proposal,
        task_id="POST /v1/tasks",
        issued_at_ms=1000,
        expires_at_ms=61000,
        affected_consumers=[],
    )


def test_begin_commits_token_and_finalize_records_receipt() -> None:
    fencer = _fencer()

    fence = fencer.begin(grant_digest=DIGEST_A, resource_id=TASK)

    assert fence.outcome is FencingCommitOutcome.COMMITTED
    assert fence.token == 1
    receipt = fencer.finalize(
        grant_digest=DIGEST_A, resource_id=TASK, token=fence.token, outcome="completed"
    )
    assert fencer.receipts == (receipt,)
    assert receipt.grant_digest == DIGEST_A
    assert receipt.resource_id == TASK
    assert receipt.fencing_token == 1


def test_second_attempt_replays_existing_receipt() -> None:
    fencer = _fencer()
    first = fencer.begin(grant_digest=DIGEST_A, resource_id=TASK)
    assert first.outcome is FencingCommitOutcome.COMMITTED
    recorded = fencer.finalize(
        grant_digest=DIGEST_A, resource_id=TASK, token=first.token, outcome="completed"
    )

    replay = fencer.begin(grant_digest=DIGEST_A, resource_id=TASK)

    assert replay.outcome is FencingCommitOutcome.REPLAYED
    assert replay.receipt == recorded
    assert replay.token is None


def test_second_grant_for_same_resource_advances_token() -> None:
    fencer = _fencer()
    first = fencer.begin(grant_digest=DIGEST_A, resource_id=TASK)
    fencer.finalize(grant_digest=DIGEST_A, resource_id=TASK, token=first.token, outcome="completed")

    second = fencer.begin(grant_digest=DIGEST_B, resource_id=TASK)
    assert second.outcome is FencingCommitOutcome.COMMITTED
    assert second.token == 2  # first execution advanced the resource token to 1
    fencer.finalize(grant_digest=DIGEST_B, resource_id=TASK, token=second.token, outcome="failed")
    assert len(fencer.receipts) == 2


def test_revoked_digest_refused_fail_closed() -> None:
    ids, membership = _genesis()
    ledger = GrantLedger(membership=membership)
    grant = _issue(ledger, ids[0], "prop_" + "a" * 32)
    ledger.revoke(ids[1], grant, reason_digest=digest_json({"reason": "superseded"}))
    fencer = _fencer(ledger=ledger)

    fence = fencer.begin(grant_digest=grant.digest, resource_id=TASK)

    assert fence.outcome is FencingCommitOutcome.REVOKED
    assert fence.receipt is None and fence.token is None
    assert fencer.receipts == ()  # nothing was reserved or executed


def test_store_error_refused_fail_closed() -> None:
    store = FencingStateStore(db_path=":memory:")
    store.close()
    fencer = GrantFencingCommit(store=store)

    fence = fencer.begin(grant_digest=DIGEST_A, resource_id=TASK)

    assert fence.outcome is FencingCommitOutcome.STORE_ERROR
    assert "closed" in (fence.detail or "")
    assert fencer.receipts == ()


def test_malformed_digest_refused_fail_closed() -> None:
    fencer = _fencer()

    fence = fencer.begin(grant_digest="not-a-digest", resource_id=TASK)

    assert fence.outcome is FencingCommitOutcome.STORE_ERROR
    assert fencer.receipts == ()


def test_duplicate_commit_refused_stale_epoch(tmp_path: Path) -> None:
    store = FencingStateStore(db_path=tmp_path / "fencing.db")
    fencer_a = GrantFencingCommit(store=store)
    fencer_b = GrantFencingCommit(store=store)
    first = fencer_a.begin(grant_digest=DIGEST_A, resource_id=TASK)
    racing = fencer_b.begin(grant_digest=DIGEST_A, resource_id=TASK)
    assert first.outcome is FencingCommitOutcome.COMMITTED
    assert racing.outcome is FencingCommitOutcome.COMMITTED

    fencer_a.finalize(
        grant_digest=DIGEST_A, resource_id=TASK, token=first.token, outcome="completed"
    )
    with pytest.raises(FencingStoreError, match="already executed"):
        fencer_b.finalize(
            grant_digest=DIGEST_A, resource_id=TASK, token=racing.token, outcome="completed"
        )


def test_receipt_survives_store_reopen(tmp_path: Path) -> None:
    path = tmp_path / "fencing.db"
    with FencingStateStore(db_path=path) as store:
        fencer = GrantFencingCommit(store=store)
        fence = fencer.begin(grant_digest=DIGEST_A, resource_id=TASK)
        recorded = fencer.finalize(
            grant_digest=DIGEST_A, resource_id=TASK, token=fence.token, outcome="completed"
        )

    with FencingStateStore(db_path=path) as reopened:
        restarted = GrantFencingCommit(store=reopened)
        replay = restarted.begin(grant_digest=DIGEST_A, resource_id=TASK)
        assert replay.outcome is FencingCommitOutcome.REPLAYED
        assert replay.receipt == recorded
        # The resource token advanced with the receipt, so a fresh digest
        # continues from 2, not from the genesis 0.
        fresh = restarted.begin(grant_digest=DIGEST_B, resource_id=TASK)
        assert fresh.outcome is FencingCommitOutcome.COMMITTED
        assert fresh.token == 2
