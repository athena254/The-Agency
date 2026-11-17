"""Hermetic Slice C ordering-adapter tests (no clock, network, or engine)."""

from __future__ import annotations

from collections.abc import Sequence

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agency.consensus.app_state import ZERO_STATE_ROOT, RejectionReason
from agency.consensus.ordering import (
    OrderingAdapter,
    OrderingPlan,
    canonical_order,
    group_into_blocks,
)
from agency.peers.canonical import digest_hex
from agency.peers.envelope import build_envelope
from agency.peers.identities import PeerIdentity
from agency.peers.membership import Membership, derive_genesis_membership

POLICY = "peer-policy/1"
_FAR_FUTURE = 9999999999999


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


def plan(mem: Membership, *, block_size: int = 4) -> OrderingPlan:
    return OrderingPlan(
        chain_id="test-chain",
        membership_epoch=mem.epoch,
        membership_root=mem.root,
        block_size=block_size,
        genesis_time_ms=1000,
        time_step_ms=1000,
    )


def ballot(
    ident: PeerIdentity,
    mem: Membership,
    proposal_id: str,
    decision: str,
    monotonic_seq: int,
    *,
    epoch: int | None = None,
    ballot_seq: int = 1,
) -> bytes:
    payload = {
        "proposal_id": proposal_id,
        "proposal_version": 1,
        "voter_peer_id": ident.peer_id,
        "decision": decision,
        "affected_role": "peer-voter",
        "evidence_cited": [],
        "human_input_ref": None,
        "ballot_seq": ballot_seq,
        "expires_at_ms": _FAR_FUTURE,
    }
    return build_envelope(
        kind="ballot",
        sender=ident,
        membership_epoch=mem.epoch if epoch is None else epoch,
        membership_root=mem.root,
        monotonic_seq=monotonic_seq,
        issued_at_ms=1000,
        expires_at_ms=_FAR_FUTURE,
        correlation_id=f"{ident.peer_id}:{monotonic_seq}",
        payload=payload,
    )


def test_identical_state_root_chains_across_four_runtimes() -> None:
    ids, mem = setup4()
    proposal_id = "prop_" + "a" * 32
    records = [ballot(ids[index], mem, proposal_id, "approve", index + 1) for index in range(3)]

    result = OrderingAdapter(plan(mem)).derive(records, membership=mem)

    assert result.deterministic
    assert len(result.chains) == 4
    baseline = result.chains[0]
    assert result.head == baseline.head == baseline.roots[-1]
    assert baseline.head != ZERO_STATE_ROOT
    assert len(baseline.roots) == 1
    assert baseline.points[0].result.decisions[0].outcome == "passed"
    for chain in result.chains[1:]:
        assert chain.roots == baseline.roots
        assert chain.points == baseline.points
        assert chain.head == baseline.head


def test_delivery_order_does_not_change_the_chain() -> None:
    ids, mem = setup4()
    proposal_id = "prop_" + "b" * 32
    records = [ballot(ids[index], mem, proposal_id, "approve", index + 1) for index in range(3)]

    permutations: Sequence[Sequence[bytes]] = [
        records,
        list(reversed(records)),
        records[1:] + records[:1],
    ]
    chains = [OrderingAdapter(plan(mem)).derive(order, membership=mem) for order in permutations]

    assert all(item.deterministic for item in chains)
    assert all(item.head == chains[0].head for item in chains)
    assert all(item.chains[0].roots == chains[0].chains[0].roots for item in chains)
    assert canonical_order(records) == canonical_order(list(reversed(records)))


def test_bad_records_are_rejected_without_exceptions() -> None:
    ids, mem = setup4()
    forged_proposal = "prop_" + "c" * 32
    clean_proposal = "prop_" + "d" * 32
    fake = PeerIdentity.generate()
    replay = ballot(ids[1], mem, clean_proposal, "approve", 2)
    equivocation_pair: tuple[bytes, bytes] | None = None
    for low_seq in range(5, 64):
        first = ballot(ids[0], mem, clean_proposal, "approve", low_seq, ballot_seq=1)
        second = ballot(ids[0], mem, clean_proposal, "deny", low_seq + 1, ballot_seq=2)
        if digest_hex(first) < digest_hex(second):
            equivocation_pair = (first, second)
            break
    assert equivocation_pair is not None

    records = [
        ballot(fake, mem, forged_proposal, "approve", 1),
        b"\x00\x01not-a-canonical-envelope",
        ballot(ids[0], mem, clean_proposal, "approve", 1, epoch=mem.epoch + 1),
        replay,
        replay,
        *equivocation_pair,
        ballot(ids[2], mem, clean_proposal, "approve", 3),
        ballot(ids[3], mem, clean_proposal, "approve", 4),
    ]

    result = OrderingAdapter(plan(mem)).derive(records, membership=mem)

    baseline = result.chains[0]
    reasons = {item.reason for point in baseline.points for item in point.result.rejected}
    assert RejectionReason.UNKNOWN_PEER.value in reasons
    assert RejectionReason.MALFORMED.value in reasons
    assert RejectionReason.WRONG_EPOCH.value in reasons
    assert RejectionReason.REPLAY.value in reasons
    assert RejectionReason.EQUIVOCATION.value in reasons
    assert result.deterministic
    decisions = [decision for point in baseline.points for decision in point.result.decisions]
    assert decisions[-1].outcome == "passed"


def test_state_root_chains_across_multiple_blocks() -> None:
    ids, mem = setup4()
    proposal_id = "prop_" + "e" * 32
    records = [ballot(ids[index % 4], mem, proposal_id, "approve", index + 1) for index in range(8)]

    blocks = group_into_blocks(records, block_size=4)
    result = OrderingAdapter(plan(mem, block_size=4)).derive(records, membership=mem)

    assert len(blocks) == 2
    assert all(len(block) == 4 for block in blocks)
    assert result.deterministic
    baseline = result.chains[0]
    assert len(baseline.roots) == 2
    assert baseline.roots[0] != baseline.roots[1]
    assert baseline.head == baseline.roots[-1]
