"""Hermetic multi-peer convergence simulation (§7 Slice B).

Each "peer runtime" is an independent ``PeerAuthority`` with its own SQLite-backed
``PeerRegistry`` and its own hash-chained ``PeerLog``. A simulated ordering engine
is just a list of blocks, each block a list of signed wire bytes; delivery to each
runtime is independent, so a peer may see a late subset in block order.

Convergence means every runtime derives the same tally/decision and the same log
chain head. Log records are re-derived in canonical wire order at the end of every
delivery (with a pinned clock) so the append-only chain does not encode the
accidental arrival order — two runtimes holding the same accepted ballots always
have the same chain head, regardless of when each ballot arrived.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import agency.peers.log as log_module
from agency.peer_envelope import decode_envelope
from agency.peers.adapter import PeerLogAdapter
from agency.peers.authority import AuthorityError, PeerAuthority, PeerRegistry
from agency.peers.envelope import PeerMessage
from agency.peers.identities import PeerIdentity
from agency.peers.log import PeerLog
from agency.peers.membership import derive_genesis_membership

POLICY = "peer-policy/1"
LOG_TS = 1000


@pytest.fixture(autouse=True)
def _fixed_log_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the log's local clock so genesis and record digests are reproducible."""
    monkeypatch.setattr(log_module, "now_ms", lambda: LOG_TS)


@dataclass(frozen=True)
class Decision:
    tally: dict[str, int]
    decision: str


@dataclass
class LogBundle:
    path: Path
    genesis: dict[str, Any]
    identity: PeerIdentity
    log: PeerLog
    adapter: PeerLogAdapter
    generation: int = 0


@dataclass(frozen=True)
class PeerRuntime:
    index: int
    identity: PeerIdentity
    authority: PeerAuthority
    registry: PeerRegistry
    accepted: dict[bytes, PeerMessage]
    seen_proposals: set[str]
    logs: LogBundle


@dataclass(frozen=True)
class Outcome:
    accepted: tuple[str, ...]
    rejected: tuple[str, ...]
    snapshot: dict[str, Decision]


def proposal_id(tag: str) -> str:
    return "prop_" + tag * 32


def make_runtimes(tmp_path: Path, n: int = 4) -> list[PeerRuntime]:
    """n independent runtimes over one shared genesis membership."""
    keys = [Ed25519PrivateKey.generate() for _ in range(n)]
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
    membership = derive_genesis_membership(members, policy_version=POLICY)
    genesis = {
        "epoch": membership.epoch,
        "membership_root": membership.root,
        "policy_version": membership.policy_version,
    }

    runtimes: list[PeerRuntime] = []
    for index, ident in enumerate(ids):
        registry = PeerRegistry(tmp_path / f"peer{index}.sqlite")
        for peer in ids:
            registry.register(peer)
        authority = PeerAuthority(membership, registry)

        log_path = tmp_path / f"peer{index}_log.sqlite"
        log = PeerLog(log_path, peer_id=ident.peer_id, genesis=genesis)
        log.open()
        adapter = PeerLogAdapter(ident, log)
        bundle = LogBundle(log_path, genesis, ident, log, adapter)

        runtimes.append(PeerRuntime(index, ident, authority, registry, {}, set(), bundle))
    return runtimes


def ballot(
    authority: PeerAuthority,
    ident: PeerIdentity,
    pid: str,
    decision: str,
    *,
    seq: int = 1,
    ballot_seq: int = 1,
) -> bytes:
    return authority.make_ballot(ident, pid, decision, monotonic_seq=seq, ballot_seq=ballot_seq)


def _rebuild_log(runtime: PeerRuntime) -> None:
    """Re-derive the local chain from accepted ballots in canonical wire order."""
    bundle = runtime.logs
    bundle.log.close()
    bundle.generation += 1
    path = bundle.path.parent / f"{bundle.path.stem}_g{bundle.generation}.sqlite"
    log = PeerLog(path, peer_id=bundle.identity.peer_id, genesis=bundle.genesis)
    log.open()
    adapter = PeerLogAdapter(bundle.identity, log)
    for wire in sorted(runtime.accepted):
        adapter.append_verified(runtime.accepted[wire], ts=LOG_TS)
    bundle.log = log
    bundle.adapter = adapter


def _snapshot(runtime: PeerRuntime) -> dict[str, Decision]:
    return {
        pid: Decision(dict(runtime.authority.tally(pid)), runtime.authority.decide(pid))
        for pid in sorted(runtime.seen_proposals)
    }


def deliver(runtime: PeerRuntime, blocks: list[list[bytes]]) -> list[Outcome]:
    """Apply each block's records in order; return per-block accepted/rejected/state."""
    outcomes: list[Outcome] = []
    for wires in blocks:
        accepted: list[str] = []
        rejected: list[str] = []
        for wire in wires:
            envelope = decode_envelope(wire)
            runtime.seen_proposals.add(str(envelope["payload"]["proposal_id"]))
            try:
                msg = runtime.authority.submit_ballot(wire)
            except AuthorityError as exc:
                rejected.append(str(exc))
                continue
            runtime.accepted[wire] = msg
            accepted.append(msg.payload_digest)
        _rebuild_log(runtime)
        outcomes.append(Outcome(tuple(accepted), tuple(rejected), _snapshot(runtime)))
    return outcomes


def assert_converged(runtimes: list[PeerRuntime]) -> None:
    """All runtimes agree on every proposal and on their log chain head digest."""
    proposals = set().union(*(runtime.seen_proposals for runtime in runtimes))
    baseline = runtimes[0]
    for pid in sorted(proposals):
        expected = (baseline.authority.tally(pid), baseline.authority.decide(pid))
        for runtime in runtimes[1:]:
            actual = (runtime.authority.tally(pid), runtime.authority.decide(pid))
            assert actual == expected, f"proposal {pid} diverged at runtime {runtime.index}"
    heads = set()
    for runtime in runtimes:
        assert runtime.logs.log.verify().ok
        heads.add(runtime.logs.log.chain_head()["last_record_digest"])
    assert len(heads) == 1, f"log chain heads diverged: {heads}"


def test_three_runtimes_converge_on_same_decision(tmp_path: Path) -> None:
    runtimes = make_runtimes(tmp_path)
    pid = proposal_id("a")
    auth = runtimes[0].authority
    block = [ballot(auth, runtimes[i].identity, pid, "approve") for i in range(3)]

    for runtime in runtimes:
        outcome = deliver(runtime, [block])[0]
        assert len(outcome.accepted) == 3
        assert outcome.rejected == ()
        assert outcome.snapshot[pid] == Decision({"approve": 3, "deny": 0, "abstain": 0}, "passed")

    assert_converged(runtimes)


def test_equivocating_peer_is_caught_identically_everywhere(tmp_path: Path) -> None:
    runtimes = make_runtimes(tmp_path)
    pid = proposal_id("b")
    auth = runtimes[0].authority
    equivocator = runtimes[1].identity
    first = ballot(auth, equivocator, pid, "approve", seq=1, ballot_seq=1)
    second = ballot(auth, equivocator, pid, "deny", seq=2, ballot_seq=2)
    clean = [ballot(auth, runtimes[i].identity, pid, "approve") for i in (0, 2, 3)]
    blocks = [[first], [second], clean]

    for runtime in runtimes:
        outcomes = deliver(runtime, blocks)
        assert any("equivocation" in reason for reason in outcomes[1].rejected)
        assert runtime.authority.is_equivocating(pid, equivocator.peer_id) is True
        evidence = runtime.authority.equivocation_evidence(pid)[equivocator.peer_id]
        assert [msg.wire for msg in evidence] == [first, second]
        assert len({msg.payload_digest for msg in evidence}) == 2
        assert runtime.authority.tally(pid)["approve"] == 3
        assert runtime.authority.decide(pid) == "passed"

    assert_converged(runtimes)


def test_forged_ballot_rejected_identically_everywhere(tmp_path: Path) -> None:
    runtimes = make_runtimes(tmp_path)
    pid = proposal_id("c")
    auth = runtimes[0].authority
    fake = PeerIdentity.generate()
    forged = ballot(auth, fake, pid, "approve")
    for runtime in runtimes:
        runtime.registry.register(fake)
    clean = [ballot(auth, runtimes[i].identity, pid, "approve") for i in range(3)]

    for runtime in runtimes:
        outcomes = deliver(runtime, [[forged], clean])
        assert len(outcomes[0].rejected) == 1
        assert "not eligible" in outcomes[0].rejected[0]
        assert runtime.authority.tally(pid)["approve"] == 3
        assert runtime.authority.decide(pid) == "passed"

    assert_converged(runtimes)


def test_late_peer_catches_up_and_converges(tmp_path: Path) -> None:
    runtimes = make_runtimes(tmp_path)
    pid = proposal_id("d")
    auth = runtimes[0].authority
    block1 = [ballot(auth, runtimes[0].identity, pid, "approve")]
    block2 = [ballot(auth, runtimes[1].identity, pid, "approve")]
    block3 = [ballot(auth, runtimes[2].identity, pid, "approve")]

    for runtime in runtimes[:3]:
        deliver(runtime, [block1, block2, block3])
        assert runtime.authority.decide(pid) == "passed"

    late = runtimes[3]
    deliver(late, [block2, block3])
    assert late.authority.tally(pid)["approve"] == 2
    assert late.authority.decide(pid) == "open"
    deliver(late, [block1])
    assert late.authority.tally(pid)["approve"] == 3
    assert late.authority.decide(pid) == "passed"

    assert_converged(runtimes)


def test_partition_then_heal(tmp_path: Path) -> None:
    runtimes = make_runtimes(tmp_path)
    pid = proposal_id("e")
    auth = runtimes[0].authority
    left = [
        ballot(auth, runtimes[0].identity, pid, "approve"),
        ballot(auth, runtimes[1].identity, pid, "approve"),
    ]
    right = [ballot(auth, runtimes[2].identity, pid, "approve")]

    for runtime in runtimes[:2]:
        deliver(runtime, [left])
        assert runtime.authority.tally(pid)["approve"] < 3
        assert runtime.authority.decide(pid) != "passed"
    for runtime in runtimes[2:]:
        deliver(runtime, [right])
        assert runtime.authority.tally(pid)["approve"] < 3
        assert runtime.authority.decide(pid) != "passed"

    for runtime in runtimes:
        deliver(runtime, [left, right])
        assert runtime.authority.tally(pid)["approve"] == 3
        assert runtime.authority.decide(pid) == "passed"

    assert_converged(runtimes)


def test_replay_is_rejected_and_convergence_holds(tmp_path: Path) -> None:
    runtimes = make_runtimes(tmp_path)
    pid = proposal_id("f")
    auth = runtimes[0].authority
    wires = [ballot(auth, runtimes[i].identity, pid, "approve") for i in range(3)]

    for runtime in runtimes:
        outcomes = deliver(runtime, [wires, [wires[0]]])
        assert len(outcomes[0].accepted) == 3
        assert outcomes[0].rejected == ()
        assert len(outcomes[1].rejected) == 1
        assert outcomes[1].accepted == ()
        assert runtime.authority.tally(pid)["approve"] == 3
        assert runtime.authority.decide(pid) == "passed"

    assert_converged(runtimes)
