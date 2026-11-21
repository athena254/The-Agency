"""Hermetic tests for agency.lab.comet_agency_net (no cometbft binary).

The live 4-node scenario runs only with AGENCY_LIVE_SLICE_C=1 and a pinned
cometbft binary (AGENCY_LIVE_COMETBFT); everything else here is pure logic or
temp-dir I/O and runs in the normal suite.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agency.lab.comet_agency_net import (
    AGENCY_GENESIS_NAME,
    PHASE_SEQUENCE,
    POLICY_VERSION,
    SCHEMA,
    AgencyNetRunner,
    agency_config_edits,
    agency_genesis_payload,
    agency_members,
    ballot_bytes,
    bft_time_ms_from_header,
    build_report,
    host_child_argv,
    issuer_key_text,
    lab_limits,
    peerlog_counts,
    peerlog_verify,
    proposal_id,
    replay_chain,
    run_lab,
    validate_report,
)
from agency.lab.comet_smoke import (
    LabError,
    LabHooks,
    allocate_ports,
    edit_toml_text,
)
from agency.peers.canonical import PROPOSAL_ID_RE
from agency.peers.envelope import decode_envelope
from agency.peers.identities import PeerIdentity
from agency.peers.log import PeerLog
from agency.peers.membership import derive_genesis_membership

LIVE_BINARY = os.environ.get("AGENCY_LIVE_COMETBFT", "")


def make_identities(n: int = 4) -> list[PeerIdentity]:
    return [PeerIdentity.from_private_key(Ed25519PrivateKey.generate()) for _ in range(n)]


def make_membership(identities: list[PeerIdentity]):
    return derive_genesis_membership(agency_members(identities), policy_version=POLICY_VERSION)


# --- pure helpers -------------------------------------------------------------


def test_agency_genesis_payload_round_trips_through_load_genesis(tmp_path: Path) -> None:
    from agency.consensus.genesis import load_genesis

    identities = make_identities()
    payload = agency_genesis_payload(agency_members(identities))
    path = tmp_path / AGENCY_GENESIS_NAME
    path.write_text(json.dumps(payload), encoding="utf-8")
    membership = load_genesis(path)
    assert membership.epoch == 1
    assert membership.policy_version == POLICY_VERSION
    assert [m.peer_id for m in membership.members] == [i.peer_id for i in identities]
    assert membership.root


def test_agency_config_edits_pin_deterministic_blocks() -> None:
    edits = agency_config_edits(26657, 26656, "id@127.0.0.1:26656")
    pinned = {(edit.section, edit.key): edit.value for edit in edits}
    assert pinned[("consensus", "create_empty_blocks")] is False
    assert pinned[("consensus", "timeout_commit")] == "200ms"
    assert pinned[("statesync", "enable")] is False
    assert pinned[("p2p", "persistent_peers")] == "id@127.0.0.1:26656"


def test_agency_config_edits_apply_to_a_lab_config_template() -> None:
    edits = agency_config_edits(10001, 10002, "id@127.0.0.1:10002")
    template = (
        "[rpc]\n"
        "laddr = 'tcp://0.0.0.0:26657'\n"
        "unsafe = false\n"
        "pprof_laddr = ''\n"
        "[p2p]\n"
        "laddr = 'tcp://0.0.0.0:26656'\n"
        "persistent_peers = ''\n"
        "seeds = ''\n"
        "pex = true\n"
        "addr_book_strict = true\n"
        "allow_duplicate_ip = false\n"
        "[instrumentation]\n"
        "prometheus = true\n"
        "[consensus]\n"
        "timeout_commit = '1s'\n"
        "create_empty_blocks = true\n"
        "[statesync]\n"
        "enable = false\n"
    )
    rewritten = edit_toml_text(template, edits)
    data = load_toml_data(rewritten)
    assert data["rpc"]["laddr"] == "tcp://127.0.0.1:10001"
    assert data["p2p"]["laddr"] == "tcp://127.0.0.1:10002"
    assert data["p2p"]["persistent_peers"] == "id@127.0.0.1:10002"
    assert data["p2p"]["pex"] is False
    assert data["consensus"]["create_empty_blocks"] is False
    assert data["consensus"]["timeout_commit"] == "200ms"
    assert data["statesync"]["enable"] is False


def load_toml_data(text: str):
    import tomllib

    return tomllib.loads(text)


def test_proposal_id_is_stable_and_matches_protocol_format() -> None:
    first = proposal_id("converge")
    second = proposal_id("converge")
    assert first == second
    assert PROPOSAL_ID_RE.fullmatch(first) is not None


def test_ballot_bytes_decode_with_expected_header_and_payload() -> None:
    identities = make_identities(1)
    membership = make_membership(identities)
    tx = ballot_bytes(
        identities[0],
        membership,
        proposal_id("converge"),
        "approve",
        7,
        ballot_seq=3,
    )
    env = decode_envelope(tx)
    assert env["kind"] == "ballot"
    assert env["monotonic_seq"] == 7
    assert env["payload"]["decision"] == "approve"
    assert env["payload"]["ballot_seq"] == 3
    assert env["membership_root"] == membership.root


def test_bft_time_ms_from_header_matches_host_conversion() -> None:
    assert bft_time_ms_from_header("2026-11-21T21:57:18.7376175Z") == 1795298238737
    assert bft_time_ms_from_header("2026-11-21T21:57:18Z") == 1795298238000
    assert bft_time_ms_from_header("2026-11-21T21:57:18.5Z") == 1795298238500
    assert bft_time_ms_from_header("2026-11-21T21:57:18.05Z") == 1795298238050


def test_replay_chain_matches_direct_state_machine_application() -> None:
    from agency.consensus.app_state import BlockContext

    identities = make_identities()
    membership = make_membership(identities)
    pid = proposal_id("converge")
    bft_time = 2000

    def ctx(height: int) -> BlockContext:
        return BlockContext(
            height=height,
            chain_id="chain-lab",
            bft_time_ms=bft_time,
            membership_epoch=membership.epoch,
            membership_root=membership.root,
        )

    block1 = [
        ballot_bytes(ids, membership, pid, "approve", index + 1)
        for index, ids in enumerate(identities[:3])
    ]
    block2 = [ballot_bytes(identities[3], membership, pid, "abstain", 1)]

    direct = BlockContext_replay(
        membership, [("chain-lab", bft_time, block1), ("chain-lab", bft_time, block2)]
    )
    expected_root, last_result = replay_chain(
        membership,
        [(1, "chain-lab", bft_time, block1), (2, "chain-lab", bft_time, block2)],
    )
    assert expected_root == direct
    assert last_result is not None
    assert any(d.outcome == "passed" and d.proposal_id == pid for d in last_result.decisions)


def BlockContext_replay(membership, blocks):
    """Reference implementation: chain apply_block results manually."""
    from agency.consensus.app_state import BlockContext, LatticeAppState

    state = LatticeAppState(membership)
    for height, (chain_id, bft_time, txs) in enumerate(blocks, start=1):
        ctx = BlockContext(
            height=height,
            chain_id=chain_id,
            bft_time_ms=bft_time,
            membership_epoch=membership.epoch,
            membership_root=membership.root,
        )
        state.apply_block(ctx, txs)
    return state.state_root


def test_host_child_argv_targets_the_cli_command() -> None:
    argv = host_child_argv(Path("g.json"), "peer_" + "a" * 32, 26658, Path("p/peer.log"))
    assert argv[0] == os.path.abspath(sys_executable())
    assert "-c" in argv
    assert "consensus-abci-host" in argv
    assert "--genesis" in argv and "g.json" in argv
    assert "--peer-id" in argv and "peer_" + "a" * 32 in argv
    assert "--port" in argv and "26658" in argv
    assert "--peer-log" in argv


def sys_executable() -> str:
    import sys

    return sys.executable


def test_issuer_key_text_round_trips_to_same_identity() -> None:
    identity = make_identities(1)[0]
    raw = base64.b64decode(issuer_key_text(identity))
    restored = PeerIdentity.from_private_key(Ed25519PrivateKey.from_private_bytes(raw))
    assert restored.peer_id == identity.peer_id
    assert restored.fingerprint == identity.fingerprint


def test_allocate_ports_yields_distinct_ports() -> None:
    ports = allocate_ports(12)
    assert len(set(ports)) == 12


def test_runner_ballot_sequences_increase_per_voter() -> None:
    runner = AgencyNetRunner(Path("unused"), Path("unused"), 60.0, LabHooks())
    identities = make_identities()
    runner.identities = identities
    runner.membership = make_membership(identities)
    pid = proposal_id("seq")
    first = runner.make_ballot(0, pid, "approve")
    second = runner.make_ballot(0, pid, "deny", ballot_seq=2)
    assert decode_envelope(first)["monotonic_seq"] == 1
    assert decode_envelope(second)["monotonic_seq"] == 2
    assert decode_envelope(second)["payload"]["ballot_seq"] == 2
    other = runner.make_ballot(1, pid, "approve")
    assert decode_envelope(other)["monotonic_seq"] == 1


# --- peer log helpers ----------------------------------------------------------


def open_lab_log(path: Path, identity: PeerIdentity, membership) -> PeerLog:
    log = PeerLog(
        path,
        peer_id=identity.peer_id,
        genesis={
            "epoch": membership.epoch,
            "membership_root": membership.root,
            "policy_version": membership.policy_version,
        },
        expected_genesis_root=membership.root,
    )
    log.open()
    return log


def test_peerlog_counts_map_executed_grants_to_proposals(tmp_path: Path) -> None:
    identity = make_identities(1)[0]
    membership = make_membership([identity])
    log = open_lab_log(tmp_path / "peer.log", identity, membership)
    try:
        log.append(
            "result",
            {"proposal_id": "prop_" + "a" * 32, "grant_digest": "g1", "result_digest": "r1"},
            membership_epoch=membership.epoch,
            membership_root=membership.root,
            ts=2000,
        )
        log.append(
            "executed_grant",
            {"grant_digest": "g1"},
            membership_epoch=membership.epoch,
            membership_root=membership.root,
            ts=2000,
        )
    finally:
        log.close()
    counts = peerlog_counts(tmp_path / "peer.log")
    assert counts["counts"]["result"] == 1
    assert counts["counts"]["executed_grant"] == 1
    assert counts["executed_grants_per_proposal"] == {"prop_" + "a" * 32: 1}
    assert counts["peer_id"] == identity.peer_id


def test_peerlog_verify_confirms_chain_after_reopen(tmp_path: Path) -> None:
    identity = make_identities(1)[0]
    membership = make_membership([identity])
    log = open_lab_log(tmp_path / "peer.log", identity, membership)
    try:
        log.append(
            "intent",
            {"grant_digest": "g2", "status": "PENDING"},
            membership_epoch=membership.epoch,
            membership_root=membership.root,
            ts=2000,
        )
    finally:
        log.close()
    verified = peerlog_verify(tmp_path / "peer.log", membership.root)
    assert verified["peer_id"] == identity.peer_id
    assert verified["chain_ok"] is True
    assert verified["records"] == 2  # genesis record + intent


# --- report validation ----------------------------------------------------------


def minimal_pass_report() -> dict:
    return {
        "schema": SCHEMA,
        "label": "agency-net",
        "not_proved": ["x"],
        "status": "PASS",
        "failures": [],
        "phases": [
            {"name": name, "status": "passed", "detail": "ok", "duration_s": 0.0}
            for name in PHASE_SEQUENCE
        ],
        "cleanup": {"confirmed_stopped": True, "errors": []},
        "live_transport": True,
    }


def test_validate_report_accepts_a_consistent_pass_report() -> None:
    validate_report(minimal_pass_report())


def test_validate_report_rejects_pass_with_failures() -> None:
    report = minimal_pass_report()
    report["failures"] = ["converge: boom"]
    with pytest.raises(LabError):
        validate_report(report)


def test_validate_report_rejects_wrong_phase_order() -> None:
    report = minimal_pass_report()
    report["phases"] = list(reversed(report["phases"]))
    with pytest.raises(LabError):
        validate_report(report)


def test_validate_report_rejects_fail_without_failures() -> None:
    report = minimal_pass_report()
    report["status"] = "FAIL"
    with pytest.raises(LabError):
        validate_report(report)


def test_validate_report_rejects_unknown_phase_name() -> None:
    report = minimal_pass_report()
    report["phases"][0]["name"] = "nope"
    with pytest.raises(LabError):
        validate_report(report)


def test_build_report_carries_sanitized_summary_fields(tmp_path: Path) -> None:
    runner = AgencyNetRunner(Path("binary.exe"), tmp_path, 60.0, LabHooks())
    runner.phases = [
        {"name": name, "status": "passed", "detail": "ok", "duration_s": 0.0}
        for name in PHASE_SEQUENCE
    ]
    cleanup = {"confirmed_stopped": True, "errors": [], "exit_codes": {}}
    report = build_report(runner, cleanup, 1.0)
    assert report["schema"] == SCHEMA
    assert report["label"] == "agency-net"
    assert report["status"] == "PASS"
    assert report["live_transport"] is True
    assert report["cleanup"]["confirmed_stopped"] is True
    assert lab_limits(60.0)["validator_count"] == 4
    validate_report(report)


# --- live scenario (gated) -------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("AGENCY_LIVE_SLICE_C") != "1",
    reason="set AGENCY_LIVE_SLICE_C=1 to run the live 4-node lab",
)
@pytest.mark.skipif(
    not LIVE_BINARY or not Path(LIVE_BINARY).is_file(),
    reason="set AGENCY_LIVE_COMETBFT to the pinned cometbft binary path",
)
def test_live_agency_net_four_nodes(tmp_path: Path) -> None:
    output = tmp_path / "lab"
    output.mkdir()
    report = run_lab(Path(LIVE_BINARY), output, 900.0, LabHooks())
    assert report["status"] == "PASS", report["failures"]
    assert report["cleanup"]["confirmed_stopped"] is True
