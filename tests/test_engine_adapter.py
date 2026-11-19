"""Hermetic engine-adapter tests: accepted grant -> one execution -> durable result."""

from __future__ import annotations

from dataclasses import replace

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agency.consensus.engine_adapter import EngineAdapter
from agency.consensus.grants import ExecutionGrant, GrantLedger, GrantRejection, issue_grant
from agency.peers.identities import PeerIdentity, consumer_id_from_ingress
from agency.peers.membership import Membership, derive_genesis_membership

POLICY = "peer-policy/1"
ISSUED_AT_MS = 1000
EXPIRES_AT_MS = ISSUED_AT_MS + 60_000
BFT_TIME_MS = 2000
PROPOSAL_ID = "prop_" + "a" * 32


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


def make_adapter(
    ledger: GrantLedger, actuator: SpyActuator | None = None
) -> tuple[EngineAdapter, SpyActuator]:
    spy = actuator or SpyActuator()
    return EngineAdapter(ledger, actuator=spy), spy


def issue(ids: list[PeerIdentity], ledger: GrantLedger, task_id: str = "task-1") -> ExecutionGrant:
    return ledger.issue(
        ids[0],
        proposal_id=PROPOSAL_ID,
        task_id=task_id,
        issued_at_ms=ISSUED_AT_MS,
        expires_at_ms=EXPIRES_AT_MS,
        affected_consumers=(consumer_id_from_ingress(ingress="telegram", subject="alice"),),
    )


def test_valid_grant_executes_once_and_persists_result() -> None:
    ids, _membership, ledger = setup4()
    adapter, spy = make_adapter(ledger)
    grant = issue(ids, ledger)

    attempt = adapter.execute(grant, bft_time_ms=BFT_TIME_MS)

    assert attempt.executed
    assert attempt.result is not None
    assert attempt.result.grant_digest == grant.digest
    assert attempt.result.proposal_id == PROPOSAL_ID
    assert attempt.result.task_id == grant.task_id
    assert attempt.result.outcome == f"executed:{grant.task_id}"
    assert spy.calls == [grant.task_id]
    assert adapter.result(grant.digest) == attempt.result
    assert dict(adapter.results) == {grant.digest: attempt.result}


def test_duplicate_grant_does_not_execute_twice() -> None:
    ids, _membership, ledger = setup4()
    adapter, spy = make_adapter(ledger)
    grant = issue(ids, ledger)

    first = adapter.execute(grant, bft_time_ms=BFT_TIME_MS)
    second = adapter.execute(grant, bft_time_ms=BFT_TIME_MS)

    assert first.executed
    assert not second.executed
    assert second.verification.reason is GrantRejection.REPLAY
    assert spy.calls == [grant.task_id]
    assert adapter.result(grant.digest) == first.result


def test_replayed_grant_object_is_rejected_before_execution() -> None:
    ids, _membership, ledger = setup4()
    adapter, spy = make_adapter(ledger)
    grant = issue(ids, ledger)
    replayed = replace(grant)

    first = adapter.execute(grant, bft_time_ms=BFT_TIME_MS)
    second = adapter.execute(replayed, bft_time_ms=BFT_TIME_MS)

    assert first.executed
    assert not second.executed
    assert second.verification.reason is GrantRejection.REPLAY
    assert spy.calls == [grant.task_id]


def test_invalid_grant_is_rejected_before_execution() -> None:
    ids, _membership, ledger = setup4()
    adapter, spy = make_adapter(ledger)
    grant = issue(ids, ledger)
    tampered = replace(grant, signature=b"\x00" * 64)

    attempt = adapter.execute(tampered, bft_time_ms=BFT_TIME_MS)

    assert not attempt.executed
    assert attempt.result is None
    assert attempt.verification.reason is GrantRejection.INVALID_SIGNATURE
    assert spy.calls == []
    assert adapter.results == {}


def test_foreign_grant_is_rejected_before_execution() -> None:
    _ids, _membership, ledger = setup4()
    adapter, spy = make_adapter(ledger)
    other_ids, other_membership, _other_ledger = setup4()
    foreign = issue_grant(
        other_ids[0],
        membership=other_membership,
        proposal_id=PROPOSAL_ID,
        task_id="task-1",
        issued_at_ms=ISSUED_AT_MS,
        expires_at_ms=EXPIRES_AT_MS,
        affected_consumers=(consumer_id_from_ingress(ingress="telegram", subject="alice"),),
    )

    attempt = adapter.execute(foreign, bft_time_ms=BFT_TIME_MS)

    assert not attempt.executed
    assert attempt.result is None
    assert attempt.verification.reason is GrantRejection.UNAUTHORIZED_ISSUER
    assert spy.calls == []
    assert adapter.results == {}


def test_stale_grant_is_rejected_before_execution() -> None:
    ids, _membership, ledger = setup4()
    adapter, spy = make_adapter(ledger)
    grant = issue(ids, ledger)

    attempt = adapter.execute(grant, bft_time_ms=EXPIRES_AT_MS)

    assert not attempt.executed
    assert attempt.result is None
    assert attempt.verification.reason is GrantRejection.EXPIRED
    assert spy.calls == []
    assert adapter.results == {}
