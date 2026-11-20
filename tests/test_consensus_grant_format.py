"""§12-format task grant issuance, verification, and legacy compatibility tests."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agency.consensus.engine_adapter import EngineAdapter
from agency.consensus.grant_guard import enforce_grant
from agency.consensus.grant_store import DurableGrantStore
from agency.consensus.grants import (
    GRANT_SIGNER_THRESHOLD,
    GRANT_VALIDATOR_COUNT,
    ExecutionGrant,
    GrantAction,
    GrantError,
    GrantLedger,
    GrantRejection,
    GrantSigner,
    VoterCertificate,
    VoterInputRef,
    verify_at_actuator,
    verify_grant,
)
from agency.peer_envelope import PREFIX
from agency.peers.canonical import digest_json
from agency.peers.identities import PeerIdentity, consumer_id_from_ingress
from agency.peers.membership import Membership, derive_genesis_membership

POLICY = "peer-policy/1"
ISSUED_AT_MS = 1000
EXPIRES_AT_MS = ISSUED_AT_MS + 60_000
BFT_TIME_MS = 2000
PROPOSAL_ID = "prop_" + "a" * 32
IDEMPOTENCY_KEY = "grant_capability:agent/alpha:op-1"
_GRANT_SIG_PREFIX = PREFIX + b"task-grant-v1|"
_FORMAT_SIG_PREFIX = PREFIX + b"task-grant-format-v1|"


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


def consumers() -> tuple[str, ...]:
    return (
        consumer_id_from_ingress(ingress="telegram", subject="alice"),
        consumer_id_from_ingress(ingress="telegram", subject="bob"),
    )


def action() -> GrantAction:
    return GrantAction("grant_capability", "agent/alpha", digest_json({"params": "bounded"}), "v1")


def certificate(
    *, audience: tuple[str, ...] | None = None, seed: str = "decision"
) -> VoterCertificate:
    members = tuple(sorted(audience if audience is not None else consumers()))
    return VoterCertificate(
        digest_json({"decision_certificate": seed}),
        tuple(VoterInputRef(member, digest_json({"input": member})) for member in members),
    )


def format_grant(
    ledger: GrantLedger,
    ids: list[PeerIdentity],
    *,
    signers: list[PeerIdentity] | None = None,
    voter_certificate: VoterCertificate | None = None,
) -> ExecutionGrant:
    return ledger.issue_format(
        ids[0],
        proposal_id=PROPOSAL_ID,
        task_id="task-1",
        issued_at_ms=ISSUED_AT_MS,
        expires_at_ms=EXPIRES_AT_MS,
        affected_consumers=consumers(),
        action=action(),
        idempotency_key=IDEMPOTENCY_KEY,
        expected_current=3,
        voter_certificate=voter_certificate or certificate(),
        signers=signers if signers is not None else ids[:3],
    )


def resign(
    grant: ExecutionGrant,
    ids: list[PeerIdentity],
    issuer: PeerIdentity,
    *,
    extra: dict[str, PeerIdentity] | None = None,
) -> ExecutionGrant:
    by_id = {ident.peer_id: ident for ident in ids}
    if extra is not None:
        by_id.update(extra)
    format_bytes = _FORMAT_SIG_PREFIX + bytes.fromhex(grant.digest)
    signers = tuple(
        replace(entry, signature=by_id[entry.peer_id].key.sign(format_bytes))
        for entry in grant.grant_signers or ()
    )
    signed = replace(grant, grant_signers=signers)
    grant_bytes = _GRANT_SIG_PREFIX + bytes.fromhex(signed.digest)
    return replace(signed, signature=issuer.key.sign(grant_bytes))


def test_format_grant_happy_path_through_verification_seams() -> None:
    ids, membership, ledger = setup()
    grant = format_grant(ledger, ids)

    assert grant.is_format
    assert len(grant.grant_signers or ()) == GRANT_SIGNER_THRESHOLD
    assert GRANT_VALIDATOR_COUNT == 4
    assert ledger.verify(grant, bft_time_ms=BFT_TIME_MS).ok
    assert verify_at_actuator(grant, ledger=ledger, bft_time_ms=BFT_TIME_MS).ok
    assert enforce_grant(grant, ledger=ledger, bft_time_ms=BFT_TIME_MS).ok
    assert ledger.verify(grant, bft_time_ms=BFT_TIME_MS).reason is GrantRejection.REPLAY

    direct = verify_grant(
        grant,
        expected_epoch=membership.epoch,
        expected_root=membership.root,
        bft_time_ms=BFT_TIME_MS,
        pinned_issuers=ledger.pinned_issuers,
        pinned_validators=ledger.pinned_validators,
    )
    assert direct.ok
    assert direct.grant_digest == grant.digest


def test_every_new_field_is_bound_into_the_signed_digest() -> None:
    ids, _membership, ledger = setup()
    grant = format_grant(ledger, ids)
    original_signers = grant.grant_signers or ()
    outsider = PeerIdentity.generate()
    variants = [
        replace(grant, action=replace(action(), resource="agent/beta")),
        replace(grant, idempotency_key="grant_capability:agent/alpha:op-2"),
        replace(grant, expected_current=4, next_token=5),
        replace(
            grant,
            grant_signers=(
                original_signers[0],
                original_signers[1],
                GrantSigner(outsider.peer_id, original_signers[2].signature),
            ),
        ),
        replace(grant, voter_certificate=certificate(seed="different")),
    ]

    for variant in variants:
        assert variant.digest != grant.digest
        assert not ledger.verify(variant, bft_time_ms=BFT_TIME_MS).ok


def test_malformed_and_partial_format_fields_reject() -> None:
    ids, membership, ledger = setup()
    grant = format_grant(ledger, ids)

    assert ledger.verify(replace(grant, action=None), bft_time_ms=BFT_TIME_MS).reason is (
        GrantRejection.MALFORMED
    )
    assert (
        ledger.verify(
            replace(grant, action={"action_type": "grant_capability"}), bft_time_ms=BFT_TIME_MS
        ).reason
        is GrantRejection.MALFORMED
    )
    assert ledger.verify(replace(grant, idempotency_key=""), bft_time_ms=BFT_TIME_MS).reason is (
        GrantRejection.MALFORMED
    )
    duplicate_signers = replace(
        grant,
        grant_signers=(grant.grant_signers[0], grant.grant_signers[0], grant.grant_signers[1]),
    )
    assert ledger.verify(duplicate_signers, bft_time_ms=BFT_TIME_MS).reason is (
        GrantRejection.MALFORMED
    )
    few_signers = replace(grant, grant_signers=(grant.grant_signers[0], grant.grant_signers[1]))
    assert ledger.verify(few_signers, bft_time_ms=BFT_TIME_MS).reason is GrantRejection.MALFORMED
    outsider = PeerIdentity.generate()
    unpinned = resign(
        replace(
            grant,
            grant_signers=(
                grant.grant_signers[0],
                grant.grant_signers[1],
                GrantSigner(outsider.peer_id, b"\x00" * 64),
            ),
        ),
        ids,
        ids[0],
        extra={outsider.peer_id: outsider},
    )
    assert ledger.verify(unpinned, bft_time_ms=BFT_TIME_MS).reason is (
        GrantRejection.INSUFFICIENT_SIGNERS
    )
    short_validator_set = verify_grant(
        grant,
        expected_epoch=membership.epoch,
        expected_root=membership.root,
        bft_time_ms=BFT_TIME_MS,
        pinned_issuers=ledger.pinned_issuers,
        pinned_validators=dict(list(ledger.pinned_validators.items())[:2]),
    )
    assert short_validator_set.reason is GrantRejection.MALFORMED


def test_wrong_threshold_signatures_reject() -> None:
    ids, _membership, ledger = setup()
    grant = format_grant(ledger, ids)

    zeroed = replace(
        grant,
        grant_signers=tuple(
            replace(signer, signature=b"\x00" * 64) for signer in grant.grant_signers or ()
        ),
    )
    assert ledger.verify(zeroed, bft_time_ms=BFT_TIME_MS).reason is (
        GrantRejection.INVALID_SIGNATURE
    )
    one_bad = replace(
        grant,
        grant_signers=(replace(grant.grant_signers[0], signature=b"\x01" * 64),)
        + grant.grant_signers[1:],
    )
    assert ledger.verify(one_bad, bft_time_ms=BFT_TIME_MS).reason is (
        GrantRejection.INVALID_SIGNATURE
    )
    bad_issuer = replace(grant, signature=b"\x00" * 64)
    assert ledger.verify(bad_issuer, bft_time_ms=BFT_TIME_MS).reason is (
        GrantRejection.INVALID_SIGNATURE
    )


def test_missing_coverage_rejects_even_when_resigned() -> None:
    ids, _membership, ledger = setup()
    truncated = certificate(audience=consumers()[:1])
    partial = resign(replace(format_grant(ledger, ids), voter_certificate=truncated), ids, ids[0])
    assert ledger.verify(partial, bft_time_ms=BFT_TIME_MS).reason is GrantRejection.MISSING_COVERAGE

    extra = VoterCertificate(
        digest_json({"decision_certificate": "extra"}),
        certificate().input_refs
        + (
            VoterInputRef(
                consumer_id_from_ingress(ingress="telegram", subject="carol"),
                digest_json({"input": "carol"}),
            ),
        ),
    )
    overbroad = resign(replace(format_grant(ledger, ids), voter_certificate=extra), ids, ids[0])
    assert ledger.verify(overbroad, bft_time_ms=BFT_TIME_MS).reason is (
        GrantRejection.MISSING_COVERAGE
    )


def test_fencing_mismatch_rejects() -> None:
    ids, _membership, ledger = setup()
    grant = format_grant(ledger, ids)

    for variant in (
        replace(grant, expected_current=4),
        replace(grant, next_token=9),
        replace(grant, expected_current=0, next_token=0),
    ):
        assert ledger.verify(variant, bft_time_ms=BFT_TIME_MS).reason is GrantRejection.MALFORMED


def test_tampered_digests_reject() -> None:
    ids, _membership, ledger = setup()
    grant = format_grant(ledger, ids)
    assert grant.voter_certificate is not None

    bad_params = replace(grant, action=replace(action(), params_digest="0" * 64))
    assert ledger.verify(bad_params, bft_time_ms=BFT_TIME_MS).reason is (
        GrantRejection.INVALID_SIGNATURE
    )
    bad_certificate = replace(
        grant,
        voter_certificate=replace(grant.voter_certificate, decision_certificate_digest="0" * 64),
    )
    assert ledger.verify(bad_certificate, bft_time_ms=BFT_TIME_MS).reason is (
        GrantRejection.INVALID_SIGNATURE
    )
    bad_affected = replace(
        grant,
        affected_consumers=(
            consumers()[0],
            consumer_id_from_ingress(ingress="telegram", subject="carol"),
        ),
    )
    assert ledger.verify(bad_affected, bft_time_ms=BFT_TIME_MS).reason is (
        GrantRejection.INVALID_SIGNATURE
    )


def test_deterministic_issuance_and_caller_supplied_time() -> None:
    ids, membership, ledger = setup()
    first = format_grant(ledger, ids)
    second = format_grant(ledger, ids)
    assert first.nonce == second.nonce
    assert first.digest == second.digest
    assert first.signature == second.signature
    assert first.grant_signers == second.grant_signers

    assert ledger.verify(first, bft_time_ms=BFT_TIME_MS).ok
    assert ledger.verify(first, bft_time_ms=EXPIRES_AT_MS).reason is GrantRejection.EXPIRED
    assert ledger.verify(first, bft_time_ms=EXPIRES_AT_MS + 1).reason is GrantRejection.EXPIRED
    assert verify_grant(
        first,
        expected_epoch=membership.epoch,
        expected_root=membership.root,
        bft_time_ms=BFT_TIME_MS,
        pinned_issuers=ledger.pinned_issuers,
    ).ok


def test_issuance_rejects_unpinned_issuer_and_insufficient_signers() -> None:
    ids, _membership, ledger = setup()
    with pytest.raises(GrantError):
        ledger.issue_format(
            PeerIdentity.generate(),
            proposal_id=PROPOSAL_ID,
            task_id="task-1",
            issued_at_ms=ISSUED_AT_MS,
            expires_at_ms=EXPIRES_AT_MS,
            affected_consumers=consumers(),
            action=action(),
            idempotency_key=IDEMPOTENCY_KEY,
            expected_current=3,
            voter_certificate=certificate(audience=consumers()[:1]),
            signers=ids[:3],
        )
    with pytest.raises(GrantError):
        format_grant(ledger, ids, signers=ids[:2])


def test_format_grant_with_guard_adapter_and_durable_store(tmp_path: Path) -> None:
    ids, membership, ledger = setup()
    grant = format_grant(ledger, ids)
    calls: list[str] = []
    adapter = EngineAdapter(ledger, actuator=lambda _grant: calls.append("executed") or "ok")

    attempt = adapter.execute(grant, bft_time_ms=BFT_TIME_MS)
    assert attempt.executed
    assert attempt.result is not None
    assert len(calls) == 1
    assert not adapter.execute(grant, bft_time_ms=BFT_TIME_MS).executed
    assert len(calls) == 1

    with DurableGrantStore(membership=membership, db_path=tmp_path / "grant.sqlite3") as store:
        fresh = format_grant(store, ids)
        assert store.verify(fresh, bft_time_ms=BFT_TIME_MS).ok
        assert store.issued_digests == frozenset({fresh.digest})
        assert enforce_grant(fresh, ledger=store, bft_time_ms=BFT_TIME_MS).ok


def test_legacy_grants_remain_byte_compatible() -> None:
    ids, _membership, ledger = setup()
    legacy = ledger.issue(
        ids[0],
        proposal_id=PROPOSAL_ID,
        task_id="task-1",
        issued_at_ms=ISSUED_AT_MS,
        expires_at_ms=EXPIRES_AT_MS,
        affected_consumers=consumers(),
    )

    assert not legacy.is_format
    assert legacy.action is None
    assert legacy.idempotency_key is None
    assert legacy.expected_current is None
    assert legacy.next_token is None
    assert legacy.grant_signers is None
    assert legacy.voter_certificate is None
    assert "format" not in legacy.body()
    assert legacy.digest == digest_json(legacy.body())
    assert ledger.verify(legacy, bft_time_ms=BFT_TIME_MS).ok
    assert ledger.verify(replace(legacy, task_id="tampered"), bft_time_ms=BFT_TIME_MS).reason is (
        GrantRejection.INVALID_SIGNATURE
    )
