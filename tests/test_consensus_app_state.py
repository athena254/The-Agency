"""Deterministic Slice B application state machine (pure module) tests."""

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agency.consensus import (
    ZERO_STATE_ROOT,
    BlockContext,
    Decision,
    LatticeAppState,
    RejectionReason,
)
from agency.peers.envelope import build_envelope
from agency.peers.identities import PeerIdentity
from agency.peers.membership import Membership, derive_genesis_membership

POLICY = "peer-policy/1"
_FAR_FUTURE = 9999999999999


def setup4() -> tuple[list[PeerIdentity], Membership]:
    keys = [Ed25519PrivateKey.generate() for _ in range(4)]
    ids = [PeerIdentity.from_private_key(key) for key in keys]
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


def ballot(
    ident: PeerIdentity,
    mem: Membership,
    proposal_id: str,
    decision: str,
    monotonic_seq: int,
    *,
    ballot_seq: int = 1,
    expires_at_ms: int = _FAR_FUTURE,
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
        "expires_at_ms": expires_at_ms,
    }
    return build_envelope(
        kind="ballot",
        sender=ident,
        membership_epoch=mem.epoch,
        membership_root=mem.root,
        monotonic_seq=monotonic_seq,
        issued_at_ms=1000,
        expires_at_ms=expires_at_ms,
        correlation_id=f"{ident.peer_id}:{monotonic_seq}",
        payload=payload,
    )


def context(mem: Membership, *, height: int = 1, bft_time_ms: int = 2000) -> BlockContext:
    return BlockContext(
        height=height,
        chain_id="test-chain",
        bft_time_ms=bft_time_ms,
        membership_epoch=mem.epoch,
        membership_root=mem.root,
    )


def proposal(letter: str) -> str:
    return "prop_" + letter * 32


def test_same_block_yields_identical_app_hash_and_results() -> None:
    ids, mem = setup4()
    p = proposal("a")
    records = [ballot(ids[index], mem, p, "approve", 1) for index in range(3)]
    ctx = context(mem)

    first = LatticeAppState(mem).apply_block(ctx, records)
    second = LatticeAppState(mem).apply_block(ctx, records)

    assert first == second
    assert len(first.app_hash) == 64
    assert all(char in "0123456789abcdef" for char in first.app_hash)
    assert len(first.accepted) == 3
    assert first.rejected == ()
    assert len(first.decisions) == 1
    assert first.decisions[0].outcome == "passed"


def test_order_matters_and_is_stable() -> None:
    ids, mem = setup4()
    p = proposal("b")
    a = ballot(ids[0], mem, p, "approve", 1)
    b = ballot(ids[1], mem, p, "approve", 1)
    ctx = context(mem)

    ab = LatticeAppState(mem).apply_block(ctx, [a, b])
    ab_replay = LatticeAppState(mem).apply_block(ctx, [a, b])
    ba = LatticeAppState(mem).apply_block(ctx, [b, a])
    ba_replay = LatticeAppState(mem).apply_block(ctx, [b, a])

    assert ab.app_hash != ba.app_hash
    assert ab.accepted != ba.accepted
    assert set(ab.accepted) == set(ba.accepted)
    assert ab == ab_replay
    assert ba == ba_replay


def test_forged_and_unknown_records_are_rejected_not_raised() -> None:
    ids, mem = setup4()
    forged_proposal = proposal("c")
    clean_proposal = proposal("d")
    fake = PeerIdentity.generate()
    forged = ballot(fake, mem, forged_proposal, "approve", 1)
    garbage = b"\x00\x01not-a-canonical-envelope"
    valid = [ballot(ids[index], mem, clean_proposal, "approve", 1) for index in range(3)]

    result = LatticeAppState(mem).apply_block(context(mem), [forged, garbage, *valid])

    by_reason = {item.reason: item.payload_digest for item in result.rejected}
    assert by_reason[RejectionReason.UNKNOWN_PEER.value]
    assert by_reason[RejectionReason.MALFORMED.value]
    assert len(result.rejected) == 2
    assert len(result.accepted) == 3
    assert result.decisions == (Decision(clean_proposal, "passed", 3, 0, 0),)


def test_equivocation_rejected_and_excluded_in_state_machine() -> None:
    ids, mem = setup4()
    p = proposal("e")
    first = ballot(ids[0], mem, p, "approve", 1, ballot_seq=1)
    second = ballot(ids[0], mem, p, "deny", 2, ballot_seq=2)
    clean = [ballot(ids[index], mem, p, "approve", 1) for index in (1, 2, 3)]

    result = LatticeAppState(mem).apply_block(context(mem), [first, second, *clean])

    equivocations = [
        item for item in result.rejected if item.reason == RejectionReason.EQUIVOCATION.value
    ]
    assert len(equivocations) == 1
    decision = result.decisions[0]
    assert (decision.approve, decision.deny, decision.abstain) == (3, 0, 0)
    assert decision.outcome == "passed"


def test_decision_passed_at_three_of_four() -> None:
    ids, mem = setup4()
    two = proposal("f")
    three = proposal("7")
    ctx = context(mem)

    low = LatticeAppState(mem).apply_block(
        ctx, [ballot(ids[index], mem, two, "approve", 1) for index in (0, 1)]
    )
    high = LatticeAppState(mem).apply_block(
        ctx, [ballot(ids[index], mem, three, "approve", 1) for index in (0, 1, 2)]
    )

    assert (low.decisions[0].outcome, low.decisions[0].approve) == ("open", 2)
    assert (high.decisions[0].outcome, high.decisions[0].approve) == ("passed", 3)


def test_state_root_chains_across_blocks() -> None:
    ids, mem = setup4()
    p = proposal("9")
    block1 = [ballot(ids[index], mem, p, "approve", 1) for index in range(3)]
    ctx1 = context(mem, height=1, bft_time_ms=2000)
    ctx2 = context(mem, height=2, bft_time_ms=3000)

    state = LatticeAppState(mem)
    first = state.apply_block(ctx1, block1)
    assert state.state_root == first.app_hash
    assert state.state_root != ZERO_STATE_ROOT

    second = state.apply_block(ctx2, [])
    assert second.app_hash != first.app_hash

    replayed = LatticeAppState(mem)
    assert replayed.apply_block(ctx1, block1) == first
    assert replayed.apply_block(ctx2, []) == second

    unchained = LatticeAppState(mem).apply_block(ctx2, [])
    assert unchained.app_hash != second.app_hash
