"""Pure ABCI application logic tests (no transport, no wire codec)."""

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agency.consensus.app import AgencyApplication
from agency.consensus.app_state import ZERO_STATE_ROOT, BlockContext, LatticeAppState
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


def test_info_reflects_commit_height_and_root() -> None:
    ids, mem = setup4()
    app = AgencyApplication(mem)
    assert app.info().last_block_height == 0
    assert app.info().last_block_app_hash == ZERO_STATE_ROOT

    p = proposal("a")
    ballots = [ballot(ids[index], mem, p, "approve", 1) for index in range(3)]
    result = app.finalize_block(ballots, context(mem))
    assert app.info().last_block_height == 0

    app.commit()
    info = app.info()
    assert info.last_block_height == 1
    assert info.last_block_app_hash == result.app_hash
    assert info.app_version == 1


def test_finalize_block_matches_direct_state_machine() -> None:
    ids, mem = setup4()
    p = proposal("b")
    ballots = [ballot(ids[index], mem, p, "approve", 1) for index in range(3)]
    ctx = context(mem)

    via_app = AgencyApplication(mem).finalize_block(ballots, ctx)
    via_state = LatticeAppState(mem).apply_block(ctx, ballots)
    assert via_app == via_state


def test_commit_persists_exactly_once_per_finalize() -> None:
    ids, mem = setup4()
    app = AgencyApplication(mem)
    p = proposal("c")
    ballots = [ballot(ids[index], mem, p, "approve", 1) for index in range(3)]
    app.finalize_block(ballots, context(mem))

    first = app.commit()
    assert app.info().last_block_height == 1
    second = app.commit()
    assert second == first
    assert app.info().last_block_height == 1


def test_process_proposal_accepts_clean_and_rejects_faults() -> None:
    ids, mem = setup4()
    p = proposal("d")
    ctx = context(mem, bft_time_ms=2000)
    clean = [ballot(ids[index], mem, p, "approve", 1) for index in range(3)]
    app = AgencyApplication(mem)

    assert app.process_proposal(clean, ctx) == "ACCEPT"

    forged = ballot(PeerIdentity.generate(), mem, p, "approve", 1)
    assert app.process_proposal([forged, *clean], ctx) == "REJECT"
    assert app.process_proposal([b"\x00\x01not-a-canonical-envelope"], ctx) == "REJECT"
    expired = ballot(ids[0], mem, p, "approve", 1, expires_at_ms=2000)
    assert app.process_proposal([expired], ctx) == "REJECT"


def test_process_proposal_is_read_only() -> None:
    ids, mem = setup4()
    p = proposal("e")
    ballots = [ballot(ids[index], mem, p, "approve", 1) for index in range(3)]
    ctx = context(mem)
    forged = ballot(PeerIdentity.generate(), mem, p, "approve", 1)
    block = [forged, *ballots]

    app = AgencyApplication(mem)
    before = app.info()
    assert app.process_proposal(block, ctx) == "REJECT"
    assert app.process_proposal(ballots, ctx) == "ACCEPT"
    assert app.info() == before

    result = app.finalize_block(block, ctx)
    assert len(result.accepted) == 3
    assert len(result.rejected) == 1
    assert app.info().last_block_app_hash != before.last_block_app_hash
    app.commit()

    committed = app.info()
    replay = [ballot(ids[0], mem, p, "approve", 1)]
    assert app.process_proposal(replay, context(mem, height=2)) == "REJECT"
    assert app.info() == committed


def test_prepare_proposal_respects_byte_cap() -> None:
    ids, mem = setup4()
    first = ballot(ids[0], mem, proposal("a"), "approve", 1)
    second = ballot(ids[1], mem, proposal("b"), "approve", 2)
    cap = len(first) + len(second) - 1
    app = AgencyApplication(mem)

    selected = app.prepare_proposal([first, second], cap)
    assert selected == [first]
    assert sum(len(tx) for tx in selected) <= cap
    assert selected[0] == first
    assert app.prepare_proposal([second], len(second) - 1) == []


def test_vote_extensions_trivial() -> None:
    app = AgencyApplication(setup4()[1])
    assert app.extend_vote(1, ZERO_STATE_ROOT) == b""
    for extension in (b"", b"anything", b"\x00\xff"):
        verdict = app.verify_vote_extension(1, ZERO_STATE_ROOT, extension)
        assert verdict == "ACCEPT"
        assert verdict != "UNKNOWN"


def test_finalized_rejections_do_not_stop_the_block() -> None:
    ids, mem = setup4()
    p = proposal("f")
    forged = ballot(PeerIdentity.generate(), mem, p, "approve", 1)
    clean = [ballot(ids[index], mem, p, "approve", 1) for index in range(3)]

    result = AgencyApplication(mem).finalize_block([forged, *clean], context(mem))
    assert len(result.rejected) == 1
    assert len(result.accepted) == 3
    assert result.decisions[0].outcome == "passed"
