"""Live 4-node CometBFT network lab for the Agency ABCI application.

Launches four local ``cometbft`` 0.38.26 validators, each paired with its own
``agency consensus-abci-host`` child process over a real TCP socket, and
exercises the Slice C acceptance surface on real transport:

- converge: quorum ballots commit; app hashes agree on all 4 peers and equal an
  independent in-process replay of the same ordered blocks;
- kill_restart: one node (engine + host child) is killed, 3 keep committing,
  restart converges to the same app hash;
- partition_heal: persistent-peer rewrite splits 1/3 islands, the majority
  keeps committing, the isolated node stalls, heal converges without
  split-brain;
- replay_idempotent: rebroadcasting identical ballot bytes is refused by the
  mempool/CheckTx replay barriers and commits nothing twice;
- revocation_fail_closed: a revoked execution grant fails closed at the
  actuator seam (in-process on the lab membership, documented deviation);
- conflicting_proposals: conflicting ballots from one voter on one proposal
  resolve deterministically (exactly one accepted), app hashes agree.

Evidence comes from real sockets and real CometBFT RPC only; no transport is
mocked. It does NOT prove Byzantine fault tolerance, packet-level network
partition resilience, independent-host resilience or production readiness, and
it is not imported by production code.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import shutil
import socket
import sqlite3
import sys
import tempfile
import time
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)

from agency.consensus.actuator import Actuator
from agency.consensus.app_state import BlockContext, LatticeAppState
from agency.consensus.genesis import load_genesis
from agency.consensus.grants import GrantLedger, GrantRejection, verify_at_actuator
from agency.lab.comet_smoke import (
    EXPECTED_VERSION,
    POLL_INTERVAL_S,
    REPORT_NAME,
    STAGE_TIMEOUT_S,
    VALIDATOR_COUNT,
    Deadline,
    LabError,
    LabHooks,
    LabTimeout,
    NodeProcess,
    NodeSpec,
    RpcClient,
    RpcError,
    TomlEdit,
    UsageError,
    assert_sanitized,
    build_persistent_peers,
    config_edits,
    dig,
    find_committed_height,
    iso_utc,
    poll_until,
    prepare_output_dir,
    rewrite_config,
    run_command,
    show_node_id_argv,
    stop_all,
    stop_node,
    testnet_argv,
    validate_binary_arg,
    validate_whole_timeout,
    verify_config,
    version_supported,
)
from agency.peer_envelope import decode_envelope
from agency.peers.canonical import digest_json
from agency.peers.envelope import build_envelope
from agency.peers.identities import PeerIdentity
from agency.peers.log import PeerLog
from agency.peers.membership import Membership

SCHEMA = "agency.lab.comet_agency_net_report/v1"
LABEL = "agency-net"
AGENCY_GENESIS_NAME = "agency-genesis.json"
POLICY_VERSION = "peer-policy/1"
APP_READY_TIMEOUT_S = 40.0
NODE_READY_TIMEOUT_S = 75.0
AGENCY_TX_POLL_TIMEOUT_S = 60.0
PARTITION_STALL_OBSERVE_S = 12.0
REPLAY_OBSERVE_S = 8.0
AGENCY_TIMEOUT_COMMIT = "200ms"
DEFAULT_WHOLE_TIMEOUT_S = 900.0
PORTS_PER_NODE = 3
NOT_PROVED = (
    "byzantine-fault-tolerance",
    "packet-level-network-partition",
    "independent-host-resilience",
    "production-readiness",
    "revocation-through-live-host-channel",
)
PHASE_SEQUENCE = (
    "version",
    "testnet",
    "identity",
    "start4",
    "converge",
    "kill_restart",
    "partition_heal",
    "replay_idempotent",
    "revocation_fail_closed",
    "conflicting_proposals",
    "stop_and_collect",
)
_MAIN_DISPATCH = "from agency.cli.main import main; main()"
_BALLOT_EXPIRES_AT_MS = 9999999999999
_BALLOT_ISSUED_AT_MS = 1000
_BFT_TIME_MS = 2000


class NetNode:
    """One lab validator: comet_smoke NodeSpec plus Agency-side attachments."""

    def __init__(
        self,
        spec: NodeSpec,
        *,
        abci_port: int,
        peer_id: str,
        peer_log: Path,
        issuer_key: Path,
    ) -> None:
        self.spec = spec
        self.abci_port = abci_port
        self.peer_id = peer_id
        self.peer_log = peer_log
        self.issuer_key = issuer_key

    @property
    def index(self) -> int:
        return self.spec.index

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def abci_laddr(self) -> str:
        return f"tcp://127.0.0.1:{self.abci_port}"


def validate_inputs(binary: str, output: str, whole_timeout: float) -> tuple[Path, Path, float]:
    """Validate the CLI triple; returns (binary, output, whole_timeout)."""
    binary_path = validate_binary_arg(binary)
    output_path = prepare_output_dir(output)
    whole = validate_whole_timeout(whole_timeout)
    return binary_path, output_path, whole


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments (exit code 2 on missing/invalid input)."""
    parser = argparse.ArgumentParser(
        prog="agency.lab.comet_agency_net",
        description="Live 4-node CometBFT + Agency ABCI host fault lab.",
    )
    parser.add_argument("--binary", required=True, help="absolute path to cometbft executable")
    parser.add_argument("--output", required=True, help="new or empty lab output directory")
    parser.add_argument(
        "--whole-timeout",
        type=float,
        default=DEFAULT_WHOLE_TIMEOUT_S,
        help="whole-run deadline in seconds (default: %(default)s)",
    )
    return parser.parse_args(argv)


def agency_members(identities: Sequence[PeerIdentity]) -> list[dict[str, Any]]:
    """Genesis member records for the lab identities (validator role each)."""
    return [
        {
            "peer_id": identity.peer_id,
            "public_key": identity.fingerprint,
            "roles": ["validator"],
            "joined_epoch": 1,
        }
        for identity in identities
    ]


def agency_genesis_payload(members: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """The Agency genesis document all four host children load."""
    return {"policy_version": POLICY_VERSION, "members": list(members)}


def agency_config_edits(rpc_port: int, p2p_port: int, peers: str) -> tuple[TomlEdit, ...]:
    """comet_smoke's lab-critical rewrites plus deterministic-block pinning.

    ``create_empty_blocks=false`` makes heights advance only on submitted
    transactions, so stall/replay observation windows have honest baselines.
    """
    return tuple(config_edits(rpc_port, p2p_port, peers)) + (
        TomlEdit("consensus", "create_empty_blocks", False),
        TomlEdit("consensus", "timeout_commit", AGENCY_TIMEOUT_COMMIT),
        TomlEdit("statesync", "enable", False),
    )


def issuer_key_text(identity: PeerIdentity) -> str:
    """Base64 raw private key for the host child's ``issuer.key`` file."""
    raw = identity.key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    return base64.b64encode(raw).decode("ascii")


def host_child_argv(genesis: Path, peer_id: str, port: int, peer_log: Path) -> list[str]:
    """argv for one ``consensus-abci-host`` child (full module import, no -m)."""
    return [
        sys.executable,
        "-c",
        _MAIN_DISPATCH,
        "consensus-abci-host",
        "--genesis",
        str(genesis),
        "--peer-id",
        peer_id,
        "--port",
        str(port),
        "--peer-log",
        str(peer_log),
    ]


def agency_node_argv(
    binary: Path, node: NetNode, persistent_peers: str, *, log_level: str = "error"
) -> list[str]:
    """argv for one cometbft validator pointed at its own host child port."""
    return [
        str(binary),
        "start",
        "--home",
        str(node.spec.home),
        "--proxy_app",
        node.abci_laddr,
        "--p2p.laddr",
        node.spec.p2p_laddr,
        "--rpc.laddr",
        node.spec.rpc_laddr,
        "--p2p.persistent_peers",
        persistent_peers,
        "--p2p.pex=false",
        "--rpc.unsafe=false",
        "--log_level",
        log_level,
    ]


def proposal_id(tag: str) -> str:
    """``prop_`` + 32 hex characters derived from a stable lab tag."""
    digest = hashlib.sha256(tag.encode("utf-8")).hexdigest()[:32]
    return "prop_" + digest


def ballot_bytes(
    identity: PeerIdentity,
    membership: Membership,
    proposal: str,
    decision: str,
    monotonic_seq: int,
    *,
    ballot_seq: int = 1,
) -> bytes:
    """One signed ballot envelope (same payload schema as the app tests)."""
    payload = {
        "proposal_id": proposal,
        "proposal_version": 1,
        "voter_peer_id": identity.peer_id,
        "decision": decision,
        "affected_role": "peer-voter",
        "evidence_cited": [],
        "human_input_ref": None,
        "ballot_seq": ballot_seq,
        "expires_at_ms": _BALLOT_EXPIRES_AT_MS,
    }
    return build_envelope(
        kind="ballot",
        sender=identity,
        membership_epoch=membership.epoch,
        membership_root=membership.root,
        monotonic_seq=monotonic_seq,
        issued_at_ms=_BALLOT_ISSUED_AT_MS,
        expires_at_ms=_BALLOT_EXPIRES_AT_MS,
        correlation_id=f"{identity.peer_id}:{monotonic_seq}",
        payload=payload,
    )


def bft_time_ms_from_header(time_text: str) -> int:
    """CometBFT RFC3339 header time -> epoch milliseconds (integer math).

    Matches the host's ``seconds*1000 + nanos//1_000_000`` conversion.
    """
    if "." in time_text:
        base, fraction = time_text.split(".", 1)
        fraction_digits = "".join(ch for ch in fraction if ch.isdigit())
    else:
        base, fraction_digits = time_text, ""
    base = base.rstrip("Zz")
    dt = datetime.fromisoformat(base if "+" in base else base + "+00:00")
    whole_seconds = int(dt.timestamp())
    millis_of_second = int(fraction_digits[:3].ljust(3, "0")) if fraction_digits else 0
    return whole_seconds * 1000 + millis_of_second


def replay_chain(
    membership: Membership,
    blocks: Sequence[tuple[int, str, int, list[bytes]]],
) -> tuple[str, Any]:
    """Independently re-derive app hashes by applying blocks 1..H in order.

    Returns ``(final_state_root, last_block_result)``. A mismatch against the
    engine's committed app hash means the application logic is not
    deterministic over the ordered blocks.
    """
    state = LatticeAppState(membership)
    result = None
    for height, chain_id, bft_time_ms, txs in blocks:
        ctx = BlockContext(
            height=height,
            chain_id=chain_id,
            bft_time_ms=bft_time_ms,
            membership_epoch=membership.epoch,
            membership_root=membership.root,
        )
        result = state.apply_block(ctx, txs)
    return state.state_root, result


def peerlog_counts(db_path: Path) -> dict[str, Any]:
    """Read-only record statistics from a live (WAL) PeerLog database.

    Works by snapshotting the db + WAL sidecars to a temp dir, so the running
    host child is never blocked and never sees a foreign writer. Windows locks
    portions of the files while SQLite commits, so the snapshot retries a
    bounded number of times before failing.
    """
    with tempfile.TemporaryDirectory(prefix="peerlog-snap-") as tmp:
        snapshot = Path(tmp) / db_path.name
        for attempt in range(8):
            try:
                shutil.copy2(db_path, snapshot)
                for sidecar in ("-wal", "-shm"):
                    source = Path(str(db_path) + sidecar)
                    if source.exists():
                        shutil.copy2(source, Path(str(snapshot) + sidecar))
                conn = sqlite3.connect(snapshot)
                conn.row_factory = sqlite3.Row
                try:
                    counts: dict[str, int] = {}
                    for row in conn.execute(
                        "SELECT kind, COUNT(*) AS n FROM peer_log GROUP BY kind"
                    ):
                        counts[str(row["kind"])] = int(row["n"])
                    digest_to_proposal: dict[str, str] = {}
                    for row in conn.execute("SELECT payload FROM peer_log WHERE kind = 'result'"):
                        payload = json.loads(str(row["payload"]))
                        proposal = payload.get("proposal_id")
                        grant = payload.get("grant_digest")
                        if isinstance(proposal, str) and isinstance(grant, str):
                            digest_to_proposal[grant] = proposal
                    executed_per_proposal: dict[str, int] = {}
                    for row in conn.execute(
                        "SELECT payload FROM peer_log WHERE kind = 'executed_grant'"
                    ):
                        payload = json.loads(str(row["payload"]))
                        grant = payload.get("grant_digest")
                        proposal = (
                            digest_to_proposal.get(grant, "unknown")
                            if isinstance(grant, str)
                            else "unknown"
                        )
                        executed_per_proposal[proposal] = executed_per_proposal.get(proposal, 0) + 1
                    meta_peer_id = conn.execute(
                        "SELECT value FROM peer_log_meta WHERE key = 'peer_id'"
                    ).fetchone()
                    return {
                        "counts": counts,
                        "executed_grants_per_proposal": executed_per_proposal,
                        "peer_id": meta_peer_id["value"] if meta_peer_id is not None else None,
                    }
                finally:
                    conn.close()
            except (PermissionError, sqlite3.DatabaseError):
                if attempt == 7:
                    raise
                time.sleep(0.4)
        raise LabError(f"peerlog snapshot failed for {db_path}")  # pragma: no cover


def peerlog_verify(db_path: Path, membership_root: str) -> dict[str, Any]:
    """Post-stop chain verification through the production PeerLog class."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT value FROM peer_log_meta WHERE key = 'peer_id'").fetchone()
        peer_id = str(row["value"]) if row is not None else ""
    finally:
        conn.close()
    log = PeerLog(
        db_path,
        peer_id=peer_id,
        genesis=None,
        expected_genesis_root=membership_root,
    )
    log.open()
    try:
        verification = log.verify()
        records = log.records()
        return {
            "peer_id": peer_id,
            "records": len(records),
            "chain_ok": verification.ok,
            "healthy": log.is_healthy,
            "durability": log.durability,
        }
    finally:
        log.close()


def lab_limits(whole_timeout: float) -> dict[str, float | int]:
    """The frozen limits block echoed into every report."""
    return {
        "validator_count": VALIDATOR_COUNT,
        "stage_timeout_s": STAGE_TIMEOUT_S,
        "tx_poll_timeout_s": AGENCY_TX_POLL_TIMEOUT_S,
        "app_ready_timeout_s": APP_READY_TIMEOUT_S,
        "node_ready_timeout_s": NODE_READY_TIMEOUT_S,
        "partition_stall_observe_s": PARTITION_STALL_OBSERVE_S,
        "replay_observe_s": REPLAY_OBSERVE_S,
        "whole_timeout_s": whole_timeout,
    }


def _record_execution(record: list[int]) -> str:
    """Executor for actuation control runs; records one execution."""
    record.append(1)
    return "done"


class AgencyNetRunner:
    """Executes the fixed live scenario in its frozen phase order."""

    def __init__(
        self, binary: Path, output: Path, whole_timeout: float, hooks: LabHooks | None = None
    ) -> None:
        self.binary = binary
        self.output = output
        self.hooks = hooks or LabHooks()
        self.deadline = Deadline(whole_timeout, clock=self.hooks.monotonic)
        self.nodes: list[NetNode] = []
        self.identities: list[PeerIdentity] = []
        self.membership: Membership | None = None
        self.genesis_path: Path | None = None
        self.hosts: dict[int, NodeProcess] = {}
        self.processes: dict[int, NodeProcess] = {}
        self.rpcs: dict[int, RpcClient] = {}
        self.phases: list[dict[str, Any]] = []
        self.failures: list[str] = []
        self.heights: dict[str, Any] = {}
        self.app_hash: list[dict[str, Any]] = []
        self.oracle: list[dict[str, Any]] = []
        self.txs: dict[str, Any] = {}
        self.peer_log: dict[str, Any] = {}
        self.revocation: dict[str, Any] = {}
        self.binary_info: dict[str, Any] = {}
        self.membership_info: dict[str, Any] = {}
        self.next_seq: dict[int, int] = {}
        self.converge_ballots: list[bytes] = []
        self.started_utc = ""
        self.finished_utc = ""

    # --- infrastructure ---------------------------------------------------
    @property
    def pinned_membership(self) -> Membership:
        """The membership loaded by phase_identity (set before any phase uses it)."""
        if self.membership is None:
            raise LabError("membership not initialised; run the identity phase first")
        return self.membership

    def rpc(self, index: int) -> RpcClient:
        client = self.rpcs.get(index)
        if client is None:
            client = self.hooks.make_rpc(self.nodes[index].spec.rpc_url, self.deadline)
            self.rpcs[index] = client
        return client

    def record(
        self, name: str, status: str, detail: str, duration_s: float = 0.0
    ) -> dict[str, Any]:
        entry = {"name": name, "status": status, "detail": detail, "duration_s": duration_s}
        self.phases.append(entry)
        return entry

    def broadcast_tx(self, index: int, tx: bytes) -> tuple[str, int]:
        """Broadcast raw tx bytes as 0x-hex; require CheckTx acceptance."""
        result = self.broadcast_result(index, tx)
        code = result.get("code")
        tx_hash = result.get("hash")
        if code != 0 or not isinstance(tx_hash, str) or not tx_hash:
            raise LabError(
                f"broadcast to node{index} rejected tx: code={code} log={result.get('log')}"
            )
        return tx_hash, int(code)

    def broadcast_result(self, index: int, tx: bytes) -> dict[str, Any]:
        """Broadcast raw tx bytes; return the RPC result (any CheckTx code).

        Raises :class:`RpcError` for JSON-RPC-level errors (the mempool cache
        rejects already-seen transactions with such an error).
        """
        return self.rpc(index).result("/broadcast_tx_sync", {"tx": "0x" + tx.hex()})

    def commit_height(self, index: int, tx_hash: str, what: str) -> int:
        return find_committed_height(
            self.hooks, self.deadline, self.rpc(index), tx_hash, AGENCY_TX_POLL_TIMEOUT_S
        )

    def wait_heights(self, indexes: Sequence[int], minimum: int, what: str) -> dict[str, int]:
        """Poll until every listed peer reports height >= minimum."""

        def attempt() -> dict[str, int] | None:
            heights: dict[str, int] = {}
            for index in indexes:
                height = self.rpc(index).latest_height()
                if height < minimum:
                    return None
                heights[f"node{index}"] = height
            return heights

        return dict(poll_until(self.hooks, self.deadline, NODE_READY_TIMEOUT_S, what, attempt))

    def app_hash_in_header(self, index: int, height: int) -> str:
        """The app hash recorded in block ``height``'s header on ``index``.

        CometBFT's block ``H`` header carries the app hash after executing
        block ``H-1`` (and ``/status latest_app_hash`` is that same
        stale-by-one value). The JSON value is CometBFT ``HexBytes`` (hex of
        the raw bytes); our host emits the hex digest encoded as ASCII, so
        hex-decoding restores the canonical digest string.
        """
        self.wait_heights([index], height, f"node{index} committed height >= {height}")
        value = str(self.rpc(index).app_hash(height))
        try:
            return bytes.fromhex(value).decode("ascii")
        except ValueError:
            return value

    def compare_app_hash(self, phase: str, height: int, indexes: Sequence[int]) -> None:
        """Require one app hash in block ``height``'s header across the peers.

        Since a block header records the hash after the previous block, this
        proves all peers executed the identical history up to ``height - 1``.
        """
        self.wait_heights(indexes, height, f"{phase}: peers committed height >= {height}")
        values = {f"node{index}": self.app_hash_in_header(index, height) for index in indexes}
        equal = len(set(values.values())) == 1
        self.app_hash.append({"phase": phase, "height": height, "equal": equal, "values": values})
        if not equal:
            raise LabError(f"{phase}: app hash mismatch at height {height}: {values}")

    def app_hash_oracle(self, phase: str, index: int, through: int) -> Any:
        """Independently re-derive the app hash produced by block ``through``.

        Replays blocks 1..through from node ``index``'s RPC and compares the
        resulting state root with the app hash recorded in block
        ``through + 1``'s header (CometBFT headers carry the hash after the
        previous block; empty proof blocks may sit between ``through`` and
        its successor, and both carry the same after-``through`` value).
        Returns the replay result of block ``through``.
        """
        blocks: list[tuple[int, str, int, list[bytes]]] = []
        for block_height in range(1, through + 1):
            result = self.rpc(index).result("/block", {"height": str(block_height)})
            header = dig(result, "block", "header")
            data = dig(result, "block", "data")
            txs = [base64.b64decode(str(tx)) for tx in (data.get("txs") or [])]
            blocks.append(
                (
                    int(header["height"]),
                    str(header["chain_id"]),
                    bft_time_ms_from_header(str(header["time"])),
                    txs,
                )
            )
        expected, last_result = replay_chain(self.pinned_membership, blocks)
        actual = self.app_hash_in_header(index, through + 1)
        self.oracle.append(
            {
                "phase": phase,
                "node": f"node{index}",
                "replayed_through": through,
                "expected": expected,
                "actual": actual,
                "match": expected == actual,
            }
        )
        if expected != actual:
            raise LabError(
                f"{phase}: app hash oracle mismatch replayed through {through} on "
                f"node{index}: expected {expected}, actual {actual}"
            )
        return last_result

    def next_seq_for(self, voter: int) -> int:
        """Per-voter strictly increasing envelope sequence numbers."""
        seq = self.next_seq.get(voter, 0) + 1
        self.next_seq[voter] = seq
        return seq

    def make_ballot(
        self,
        voter: int,
        proposal: str,
        decision: str,
        *,
        ballot_seq: int = 1,
    ) -> bytes:
        return ballot_bytes(
            self.identities[voter],
            self.pinned_membership,
            proposal,
            decision,
            self.next_seq_for(voter),
            ballot_seq=ballot_seq,
        )

    def start_host(self, index: int, log_name: str) -> NodeProcess:
        node = self.nodes[index]
        handle = (self.output / log_name).open("ab")
        if self.genesis_path is None:
            raise LabError("genesis not written; run the identity phase first")
        argv = host_child_argv(self.genesis_path, node.peer_id, node.abci_port, node.peer_log)
        try:
            proc = self.hooks.popen(argv, handle)
        except Exception:
            handle.close()
            raise
        return NodeProcess(spec=node.spec, proc=proc, handle=handle, log_name=log_name)

    def wait_app_ready(self, index: int, what: str) -> None:
        """Bounded poll until the host child accepts TCP on its ABCI port."""

        def attempt() -> bool | None:
            try:
                conn = socket.create_connection(
                    ("127.0.0.1", self.nodes[index].abci_port), timeout=0.5
                )
            except OSError:
                return None
            conn.close()
            return True

        poll_until(self.hooks, self.deadline, APP_READY_TIMEOUT_S, what, attempt, 0.2)

    def start_node(self, index: int, peers: str, log_name: str) -> NodeProcess:
        node = self.nodes[index]
        handle = (self.output / log_name).open("ab")
        argv = agency_node_argv(self.binary, node, peers)
        try:
            proc = self.hooks.popen(argv, handle)
        except Exception:
            handle.close()
            raise
        return NodeProcess(spec=node.spec, proc=proc, handle=handle, log_name=log_name)

    def stop_cometbft(self, index: int) -> None:
        proc = self.processes.pop(index, None)
        if proc is not None:
            stop_node(proc)

    def stop_host(self, index: int) -> None:
        proc = self.hosts.pop(index, None)
        if proc is not None:
            stop_node(proc)

    # --- phases -----------------------------------------------------------
    def phase_version(self) -> None:
        """Pinned-version gate; runs before any node state exists."""
        result = run_command(
            self.hooks,
            [str(self.binary), "version"],
            self.deadline,
            30.0,
        )
        stdout = result.stdout.strip()
        version = stdout.split()[0] if stdout else ""
        matches = version_supported(result.stdout)
        self.binary_info = {
            "path": str(self.binary),
            "version": version,
            "expected_version": EXPECTED_VERSION,
            "matches": matches,
        }
        if not matches:
            raise LabError(f"binary version {version!r} does not match pinned {EXPECTED_VERSION}")

    def phase_testnet(self) -> None:
        """Generate 4 homes, read node IDs, rewrite and verify configs."""
        run_command(
            self.hooks,
            testnet_argv(self.binary, self.output),
            self.deadline,
            120.0,
        )
        ports = self.hooks.allocate_ports(PORTS_PER_NODE * VALIDATOR_COUNT)
        node_ids: list[str] = []
        for index in range(VALIDATOR_COUNT):
            home = self.output / f"node{index}"
            if not home.is_dir():
                raise LabError(f"testnet did not create node home {home}")
            result = run_command(
                self.hooks,
                show_node_id_argv(self.binary, home),
                self.deadline,
                30.0,
            )
            lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            node_id = lines[-1] if lines else ""
            if len(node_id) != 40 or any(ch not in "0123456789abcdefABCDEF" for ch in node_id):
                raise LabError(f"node {index}: unusable node ID {node_id!r}")
            node_ids.append(node_id)
        specs = [
            NodeSpec(
                index=index,
                node_id=node_ids[index],
                home=self.output / f"node{index}",
                p2p_port=ports[index],
                rpc_port=ports[VALIDATOR_COUNT + index],
            )
            for index in range(VALIDATOR_COUNT)
        ]
        self.nodes = [
            NetNode(
                spec,
                abci_port=ports[2 * VALIDATOR_COUNT + index],
                peer_id="",
                peer_log=spec.home / "agency" / "peer.log",
                issuer_key=spec.home / "agency" / "issuer.key",
            )
            for index, spec in enumerate(specs)
        ]
        self._write_configs()

    def _write_configs(self, peers_override: dict[int, str] | None = None) -> None:
        for node in self.nodes:
            config = node.spec.home / "config" / "config.toml"
            if not config.is_file():
                raise LabError(f"missing generated config {config}")
            peers = (
                peers_override[node.index]
                if peers_override is not None
                else build_persistent_peers([n.spec for n in self.nodes], node.index)
            )
            edits = agency_config_edits(node.spec.rpc_port, node.spec.p2p_port, peers)
            rewrite_config(config, edits)
            verify_config(config, edits)

    def phase_identity(self) -> None:
        """Generate 4 peer identities, the Agency genesis and issuer keys."""
        self.identities = [PeerIdentity.generate() for _ in range(VALIDATOR_COUNT)]
        payload = agency_genesis_payload(agency_members(self.identities))
        genesis_path = self.output / AGENCY_GENESIS_NAME
        genesis_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        self.genesis_path = genesis_path
        self.membership = load_genesis(genesis_path)
        for node, identity in zip(self.nodes, self.identities, strict=True):
            node.peer_id = identity.peer_id
            node.peer_log.parent.mkdir(parents=True, exist_ok=True)
            node.issuer_key.write_text(issuer_key_text(identity) + "\n", encoding="utf-8")
        self.membership_info = {
            "genesis": str(genesis_path),
            "policy_version": self.membership.policy_version,
            "membership_epoch": self.membership.epoch,
            "membership_root": self.membership.root,
            "peer_ids": [node.peer_id for node in self.nodes],
        }

    def phase_start4(self) -> None:
        """Start 4 host children + 4 cometbft nodes; wait for block 1."""
        for index in range(VALIDATOR_COUNT):
            self.hosts[index] = self.start_host(index, f"host{index}.log")
            self.wait_app_ready(index, f"start4: host{index} accepting ABCI connections")
        for index in range(VALIDATOR_COUNT):
            peers = build_persistent_peers([n.spec for n in self.nodes], index)
            self.processes[index] = self.start_node(index, peers, f"node{index}.log")
        heights = self.wait_heights(
            list(range(VALIDATOR_COUNT)), 1, "start4: all 4 peers at height >= 1"
        )
        self.heights["start4"] = heights

    def phase_converge(self) -> None:
        """Quorum ballots commit; app hashes agree and match the oracle."""
        proposal = proposal_id("converge")
        ballots = [self.make_ballot(voter, proposal, "approve") for voter in (0, 1, 2)]
        self.converge_ballots = list(ballots)
        hashes = []
        for tx in ballots:
            tx_hash, _code = self.broadcast_tx(0, tx)
            hashes.append(tx_hash)
        height = self.commit_height(0, hashes[0], "converge commit")
        self.txs["converge_ballots"] = {
            "proposal_id": proposal,
            "hashes": hashes,
            "height": height,
            "voters": [0, 1, 2],
            "decision": "approve",
        }
        self.heights["converge_decision"] = height
        indexes = list(range(VALIDATOR_COUNT))
        past = self.make_ballot(3, proposal, "abstain")
        past_hash, _code = self.broadcast_tx(3, past)
        past_height = self.commit_height(3, past_hash, "converge past-decision commit")
        if past_height <= height:
            raise LabError(f"converge: post-decision height {past_height} did not pass {height}")
        self.heights["converge_past_decision"] = past_height
        self.txs["converge_past_decision"] = {
            "proposal_id": proposal,
            "hash": past_hash,
            "height": past_height,
            "voter": 3,
            "decision": "abstain",
        }
        # The block after the decision block records the app hash after it:
        # compare across peers and against an independent replay.
        self.compare_app_hash("converge_decision", height + 1, indexes)
        self.app_hash_oracle("converge_decision", 0, height)

    def phase_kill_restart(self) -> None:
        """Kill node 3 (engine + host); 3 peers commit; restart converges."""
        self.stop_cometbft(3)
        self.stop_host(3)
        running = [0, 1, 2]
        proposal = proposal_id("restart")
        ballots = [self.make_ballot(voter, proposal, "approve") for voter in running]
        hashes = []
        for tx in ballots:
            tx_hash, _code = self.broadcast_tx(0, tx)
            hashes.append(tx_hash)
        height = self.commit_height(0, hashes[0], "kill_restart majority commit")
        if height <= int(self.heights.get("converge_past_decision", 0)):
            raise LabError(f"kill_restart: height {height} did not advance past converge")
        self.txs["restart_ballots"] = {
            "proposal_id": proposal,
            "hashes": hashes,
            "height": height,
            "voters": running,
            "decision": "approve",
        }
        self.heights["kill_restart_majority"] = height
        self.compare_app_hash("kill_restart_majority", height, running)
        self.hosts[3] = self.start_host(3, "host3.log")
        self.wait_app_ready(3, "kill_restart: host3 accepting ABCI connections")
        peers = build_persistent_peers([n.spec for n in self.nodes], 3)
        self.processes[3] = self.start_node(3, peers, "node3.log")
        rejoin = self.make_ballot(3, proposal, "approve")
        rejoin_hash, _code = self.broadcast_tx(3, rejoin)
        rejoin_height = self.commit_height(3, rejoin_hash, "kill_restart rejoin commit")
        if rejoin_height <= height:
            raise LabError(f"kill_restart: rejoin height {rejoin_height} did not pass {height}")
        self.txs["rejoin_ballot"] = {
            "proposal_id": proposal,
            "hash": rejoin_hash,
            "height": rejoin_height,
            "voter": 3,
            "decision": "approve",
        }
        self.heights["kill_restart_rejoin"] = rejoin_height
        self.compare_app_hash("kill_restart_rejoin", height + 1, list(range(VALIDATOR_COUNT)))
        # The restarted node replays its stored blocks; the oracle re-derives
        # everything through the pre-restart majority block from node3's RPC.
        self.app_hash_oracle("kill_restart_rejoin", 3, height)

    def phase_partition_heal(self) -> None:
        """1/3 island split via persistent peers; heal converges."""
        for index in range(VALIDATOR_COUNT):
            self.stop_cometbft(index)
        majority = [1, 2, 3]
        isolated_peers = {0: ""}
        majority_peers = {
            index: ",".join(
                f"{n.spec.node_id}@127.0.0.1:{n.spec.p2p_port}"
                for n in self.nodes
                if n.index != index and n.index != 0
            )
            for index in majority
        }
        self._write_configs({**majority_peers, **isolated_peers})
        for index in range(VALIDATOR_COUNT):
            peers = isolated_peers.get(index, majority_peers.get(index, ""))
            self.processes[index] = self.start_node(index, peers, f"node{index}.log")
        baseline = self.wait_heights(
            list(range(VALIDATOR_COUNT)), 1, "partition: all peers restart above height 1"
        )
        self.heights["partition_baseline"] = baseline
        proposal = proposal_id("partition")
        ballots = [self.make_ballot(voter, proposal, "approve") for voter in (0, 1, 2)]
        hashes = []
        for tx in ballots:
            tx_hash, _code = self.broadcast_tx(1, tx)
            hashes.append(tx_hash)
        height = self.commit_height(1, hashes[0], "partition majority commit")
        self.txs["partition_ballots"] = {
            "proposal_id": proposal,
            "hashes": hashes,
            "height": height,
            "voters": [0, 1, 2],
            "decision": "approve",
            "submitted_to": "node1",
        }
        self.heights["partition_majority"] = height
        marker = self.make_ballot(3, proposal, "approve")
        marker_hash, _code = self.broadcast_tx(1, marker)
        marker_height = self.commit_height(1, marker_hash, "partition majority marker commit")
        if marker_height <= height:
            raise LabError(f"partition: marker height {marker_height} did not pass {height}")
        self.txs["partition_marker_ballot"] = {
            "proposal_id": proposal,
            "hash": marker_hash,
            "height": marker_height,
            "voter": 3,
            "decision": "approve",
            "submitted_to": "node1",
        }
        self.heights["partition_majority_marker"] = marker_height
        # The block after the majority's quorum block records the app hash
        # after it: the island agreed on it without node0.
        self.compare_app_hash("partition_majority", height + 1, majority)
        window = min(PARTITION_STALL_OBSERVE_S, self.deadline.remaining())
        if window < PARTITION_STALL_OBSERVE_S:
            raise LabTimeout("partition: no whole-run budget for the stall window")
        start = self.hooks.monotonic()
        isolated_height = self.rpc(0).latest_height()
        while self.hooks.monotonic() - start < window:
            current = self.rpc(0).latest_height()
            if current != isolated_height:
                raise LabError(
                    f"partition: isolated node0 height moved {isolated_height} -> {current}"
                )
            self.hooks.sleep(self.deadline.bound(POLL_INTERVAL_S))
        if isolated_height >= height:
            raise LabError(
                f"partition: isolated node0 at {isolated_height} already at majority height {height}"
            )
        self.heights["partition_isolated_stalled_at"] = isolated_height
        for index in range(VALIDATOR_COUNT):
            self.stop_cometbft(index)
        self._write_configs()
        for index in range(VALIDATOR_COUNT):
            peers = build_persistent_peers([n.spec for n in self.nodes], index)
            self.processes[index] = self.start_node(index, peers, f"node{index}.log")
        self.compare_app_hash("partition_healed", height + 1, list(range(VALIDATOR_COUNT)))
        self.app_hash_oracle("partition_healed", 0, height)
        proof_proposal = proposal_id("heal-proof")
        proof = self.make_ballot(0, proof_proposal, "approve")
        proof_hash, _code = self.broadcast_tx(0, proof)
        proof_height = self.commit_height(0, proof_hash, "partition healed commit")
        self.txs["heal_proof_ballot"] = {
            "proposal_id": proof_proposal,
            "hash": proof_hash,
            "height": proof_height,
            "voter": 0,
            "decision": "approve",
        }
        self.heights["partition_healed_proof"] = proof_height
        self.compare_app_hash("partition_healed_proof", proof_height, list(range(VALIDATOR_COUNT)))

    def phase_replay_idempotent(self) -> None:
        """Rebroadcasting committed ballot bytes must not commit twice."""
        recorded = self.txs.get("converge_ballots")
        ballots = getattr(self, "converge_ballots", None)
        if not isinstance(recorded, dict) or not recorded.get("hashes") or not ballots:
            raise LabError("replay: converge ballot bytes/hashes unavailable")
        raw_hash = recorded["hashes"][0]
        baseline_heights = {
            f"node{index}": self.rpc(index).latest_height() for index in range(VALIDATOR_COUNT)
        }
        baseline_logs = {
            f"node{index}": peerlog_counts(self.nodes[index].peer_log)
            for index in range(VALIDATOR_COUNT)
        }
        self.heights["replay_baseline"] = baseline_heights
        rejections = []
        for index in (0, 2):
            try:
                result = self.broadcast_result(index, ballots[0])
            except RpcError as exc:
                rejections.append({"node": f"node{index}", "rejected": True, "rpc_error": str(exc)})
            else:
                rejections.append(
                    {
                        "node": f"node{index}",
                        "rejected": result.get("code") != 0,
                        "code": result.get("code"),
                        "log": str(result.get("log"))[:200],
                    }
                )
        self.txs["replay_attempts"] = rejections
        if not any(entry.get("rejected") for entry in rejections):
            raise LabError(f"replay: rebroadcast was not rejected: {rejections}")
        window = min(REPLAY_OBSERVE_S, self.deadline.remaining())
        if window < REPLAY_OBSERVE_S:
            raise LabTimeout("replay: no whole-run budget for the observation window")
        original_height = int(recorded["height"])
        start = self.hooks.monotonic()
        while self.hooks.monotonic() - start < window:
            # The tx index keeps the original commitment; a second execution
            # would move the indexed height. It must never change. Heights
            # themselves keep moving: the app hash changes on every block by
            # construction (height + BFT time are hashed in), so CometBFT's
            # needProofBlock keeps committing empty proof blocks even with
            # create_empty_blocks=false.
            try:
                result = self.rpc(0).tx(raw_hash)
                found_height = int(dig(result, "height"))
            except RpcError as exc:
                if exc.not_found:
                    raise LabError("replay: original ballot disappeared from the tx index") from exc
                raise
            if found_height != original_height:
                raise LabError(
                    f"replay: rebroadcast ballot committed a second time: index moved "
                    f"{original_height} -> {found_height}"
                )
            self.hooks.sleep(self.deadline.bound(POLL_INTERVAL_S))
        after_heights = {
            f"node{index}": self.rpc(index).latest_height() for index in range(VALIDATOR_COUNT)
        }
        after_logs = {
            f"node{index}": peerlog_counts(self.nodes[index].peer_log)
            for index in range(VALIDATOR_COUNT)
        }
        for name in baseline_logs:
            if (
                baseline_logs[name]["executed_grants_per_proposal"]
                != (after_logs[name]["executed_grants_per_proposal"])
            ):
                raise LabError(
                    f"replay: executed grant counts changed on {name}: "
                    f"{baseline_logs[name]['executed_grants_per_proposal']} -> "
                    f"{after_logs[name]['executed_grants_per_proposal']}"
                )
        self.peer_log["replay_baseline"] = baseline_logs
        self.peer_log["replay_after"] = after_logs
        self.heights["replay_after_window"] = after_heights

    def phase_revocation_fail_closed(self) -> None:
        """Revoked grant fails closed at the ledger/actuator seam.

        Sequence: issue a grant, revoke it through the ledger, verify it is
        refused with REVOKED and the actuator never runs the executor; a
        fresh (unrevoked) grant then runs as a positive control; the now-used
        control grant also fails closed afterwards (nonce replay barrier).
        """
        proposal = proposal_id("revocation")
        ledger = GrantLedger(membership=self.pinned_membership)
        revoked_grant = ledger.issue(
            self.identities[0],
            proposal_id=proposal,
            task_id="task-revoke",
            issued_at_ms=_BALLOT_ISSUED_AT_MS,
            expires_at_ms=_BALLOT_ISSUED_AT_MS + 60_000,
            affected_consumers=(),
        )
        reason = digest_json({"reason": "slice-c live revocation"})
        ledger.revoke(self.identities[0], revoked_grant, reason_digest=reason)
        after_revoke = verify_at_actuator(revoked_grant, ledger=ledger, bft_time_ms=_BFT_TIME_MS)
        executed: list[int] = []
        actuator = Actuator(ledger=ledger, bft_time_ms=_BFT_TIME_MS)
        refused = actuator.run(revoked_grant, lambda: _record_execution(executed))
        if after_revoke.ok or after_revoke.reason is not GrantRejection.REVOKED:
            raise LabError(
                f"revocation: verification did not fail closed with REVOKED: {after_revoke.reason}"
            )
        revoked_never_executed = not executed
        if not revoked_never_executed or refused.ok:
            raise LabError(f"revocation: actuator executed a revoked grant: executed={executed}")
        control_grant = ledger.issue(
            self.identities[0],
            proposal_id=proposal_id("revocation-control"),
            task_id="task-control",
            issued_at_ms=_BALLOT_ISSUED_AT_MS,
            expires_at_ms=_BALLOT_ISSUED_AT_MS + 60_000,
            affected_consumers=(),
        )
        control_verify = verify_at_actuator(control_grant, ledger=ledger, bft_time_ms=_BFT_TIME_MS)
        if not control_verify.ok:
            raise LabError(
                f"revocation: control grant unexpectedly refused: {control_verify.reason}"
            )
        control_run = actuator.run(control_grant, lambda: _record_execution(executed))
        if not control_run.ok or len(executed) != 1:
            raise LabError(
                f"revocation: control grant did not execute: ok={control_run.ok} "
                f"executed={executed}"
            )
        after_use = verify_at_actuator(control_grant, ledger=ledger, bft_time_ms=_BFT_TIME_MS)
        self.revocation = {
            "proposal_id": proposal,
            "revoked_grant_digest": revoked_grant.digest,
            "post_revocation_ok": after_revoke.ok,
            "post_revocation_reason": (
                after_revoke.reason.value if after_revoke.reason is not None else None
            ),
            "revoked_actuation_ok": refused.ok,
            "revoked_grant_never_executed": revoked_never_executed,
            "control_grant_digest": control_grant.digest,
            "control_verify_ok": control_verify.ok,
            "control_actuation_ok": control_run.ok,
            "executed_total": len(executed),
            "used_grant_reverification_ok": after_use.ok,
            "used_grant_reverification_reason": (
                after_use.reason.value if after_use.reason is not None else None
            ),
        }
        if after_use.ok or after_use.reason is not GrantRejection.REPLAY:
            raise LabError(
                f"revocation: used grant re-verification did not fail closed: {after_use.reason}"
            )

    def phase_conflicting_proposals(self) -> None:
        """Conflicting ballots resolve deterministically; no split-brain."""
        proposal = proposal_id("conflict")
        approve = self.make_ballot(0, proposal, "approve")
        peer_approvals = [self.make_ballot(voter, proposal, "approve") for voter in (1, 2)]
        hashes = []
        for tx in [approve, *peer_approvals]:
            tx_hash, _code = self.broadcast_tx(0, tx)
            hashes.append(tx_hash)
        height = self.commit_height(0, hashes[0], "conflict approve commit")
        self.txs["conflict_approve"] = {
            "proposal_id": proposal,
            "hashes": hashes,
            "height": height,
            "voters": [0, 1, 2],
            "decision": "approve",
        }
        self.heights["conflict_approve"] = height
        indexes = list(range(VALIDATOR_COUNT))
        deny = self.make_ballot(0, proposal, "deny", ballot_seq=2)
        deny_hash, _code = self.broadcast_tx(0, deny)
        deny_height = self.commit_height(0, deny_hash, "conflict deny commit")
        if deny_height <= height:
            raise LabError(f"conflict: deny height {deny_height} did not pass {height}")
        self.txs["conflict_deny"] = {
            "proposal_id": proposal,
            "hash": deny_hash,
            "height": deny_height,
            "voter": 0,
            "decision": "deny",
            "ballot_seq": 2,
        }
        self.heights["conflict_deny"] = deny_height
        marker = self.make_ballot(3, proposal, "abstain")
        marker_hash, _code = self.broadcast_tx(3, marker)
        marker_height = self.commit_height(3, marker_hash, "conflict marker commit")
        if marker_height <= deny_height:
            raise LabError(f"conflict: marker height {marker_height} did not pass {deny_height}")
        self.txs["conflict_marker_ballot"] = {
            "proposal_id": proposal,
            "hash": marker_hash,
            "height": marker_height,
            "voter": 3,
            "decision": "abstain",
        }
        self.heights["conflict_marker"] = marker_height
        # The marker block's header records the app hash after the deny block.
        self.compare_app_hash("conflict_deny", deny_height + 1, indexes)
        last_result = self.app_hash_oracle("conflict", 1, deny_height)
        deny_digest = str(decode_envelope(deny)["payload_digest"])
        rejected_reasons = {
            rejection.payload_digest: rejection.reason for rejection in last_result.rejected
        }
        accepted_digests = set(last_result.accepted)
        self.oracle[-1]["deny_payload_digest"] = deny_digest
        self.oracle[-1]["deny_rejection_reason"] = rejected_reasons.get(deny_digest)
        self.oracle[-1]["deny_in_accepted"] = deny_digest in accepted_digests
        if rejected_reasons.get(deny_digest) != "equivocation":
            raise LabError(
                f"conflict: deny ballot not deterministically rejected as equivocation: "
                f"{rejected_reasons.get(deny_digest)!r}"
            )
        if deny_digest in accepted_digests:
            raise LabError("conflict: deny ballot appears in accepted records")
        outcomes = {
            decision.proposal_id: (
                decision.outcome,
                decision.approve,
                decision.deny,
                decision.abstain,
            )
            for decision in last_result.decisions
        }
        # Fail-closed tally: the equivocator's earlier approve is retracted,
        # so only the two clean voters count and no quorum can be reached.
        outcome = outcomes.get(proposal)
        if outcome != ("open", 2, 0, 0):
            raise LabError(
                f"conflict: proposal tally {outcome!r} is not the deterministic "
                "fail-closed outcome ('open', approve=2, deny=0, abstain=0)"
            )

    def phase_stop_and_collect(self) -> None:
        """Stop every child, then verify each node's durable PeerLog chain."""
        for index in range(VALIDATOR_COUNT):
            self.stop_cometbft(index)
        for index in range(VALIDATOR_COUNT):
            self.stop_host(index)
        collected: dict[str, Any] = {}
        for node in self.nodes:
            final = peerlog_verify(node.peer_log, self.pinned_membership.root)
            counts = peerlog_counts(node.peer_log)
            final["counts"] = counts["counts"]
            final["executed_grants_per_proposal"] = counts["executed_grants_per_proposal"]
            collected[node.name] = final
        self.peer_log["final"] = collected
        for name, entry in collected.items():
            if not entry["chain_ok"]:
                raise LabError(f"stop_and_collect: peer log chain broken on {name}")
            non_genesis = {
                proposal: count
                for proposal, count in entry["executed_grants_per_proposal"].items()
                if proposal != "unknown"
            }
            if not non_genesis or any(count != 1 for count in non_genesis.values()):
                raise LabError(
                    f"stop_and_collect: executed grants not exactly once per proposal "
                    f"on {name}: {entry['executed_grants_per_proposal']}"
                )


def build_report(
    runner: AgencyNetRunner, cleanup: dict[str, Any], duration_s: float
) -> dict[str, Any]:
    """Assemble the sanitized report."""
    status = "PASS" if not runner.failures else "FAIL"
    binary = dict(runner.binary_info)
    return {
        "schema": SCHEMA,
        "label": LABEL,
        "not_proved": list(NOT_PROVED),
        "status": status,
        "failures": list(runner.failures),
        "limits": lab_limits(runner.deadline.whole_timeout),
        "binary": binary,
        "output_dir": str(runner.output),
        "started_utc": runner.started_utc,
        "finished_utc": runner.finished_utc,
        "duration_s": round(duration_s, 3),
        "live_transport": True,
        "membership": dict(runner.membership_info),
        "nodes": [
            {
                "index": node.index,
                "node_id": node.spec.node_id,
                "peer_id": node.peer_id,
                "home": str(node.spec.home),
                "p2p_laddr": node.spec.p2p_laddr,
                "rpc_laddr": node.spec.rpc_laddr,
                "abci_laddr": node.abci_laddr,
            }
            for node in runner.nodes
        ],
        "phases": list(runner.phases),
        "heights": dict(runner.heights),
        "app_hash": list(runner.app_hash),
        "app_hash_oracle": list(runner.oracle),
        "txs": dict(runner.txs),
        "revocation": dict(runner.revocation),
        "peer_log": dict(runner.peer_log),
        "cleanup": dict(cleanup),
    }


def validate_report(report: dict[str, Any]) -> None:
    """Refuse self-contradicting reports."""
    status = report.get("status")
    if status not in ("PASS", "FAIL"):
        raise LabError(f"report status must be PASS or FAIL, got {status!r}")
    failures = report.get("failures")
    if not isinstance(failures, list):
        raise LabError("report must carry a failures list")
    phases = report.get("phases")
    if not isinstance(phases, list):
        raise LabError("report must carry a phases list")
    for phase in phases:
        if not isinstance(phase, dict):
            raise LabError(f"every phase entry must be an object, got {phase!r}")
        name = phase.get("name")
        if not isinstance(name, str) or name not in PHASE_SEQUENCE:
            raise LabError(f"phase has an invalid name: {name!r}")
        if phase.get("status") not in ("passed", "failed", "skipped"):
            raise LabError(f"phase {name} has an invalid status: {phase.get('status')!r}")
    names = [phase["name"] for phase in phases]
    if len(set(names)) != len(names):
        raise LabError(f"report phases contain duplicate names: {names}")
    if tuple(names) != PHASE_SEQUENCE:
        raise LabError(f"report phases must be exactly {PHASE_SEQUENCE}, got {names}")
    if status == "PASS":
        if report.get("label") != LABEL:
            raise LabError(f"report label must be {LABEL!r}")
        if report.get("schema") != SCHEMA:
            raise LabError(f"report schema must be {SCHEMA!r}")
        if report.get("live_transport") is not True:
            raise LabError("report must set live_transport to True")
        if failures:
            raise LabError(f"PASS report with recorded failures: {failures}")
        if any(phase["status"] != "passed" for phase in phases):
            raise LabError("PASS report with non-passed phases")
        cleanup = report.get("cleanup")
        if not isinstance(cleanup, dict):
            raise LabError("PASS report must carry a cleanup object")
        if cleanup.get("errors"):
            raise LabError(f"PASS report with cleanup errors: {cleanup['errors']}")
        if cleanup.get("confirmed_stopped") is not True:
            raise LabError("PASS report without confirmed child cleanup")
    elif status == "FAIL":
        if not failures:
            raise LabError("FAIL report with no recorded failure")


def run_lab(
    binary: Path, output: Path, whole_timeout: float, hooks: LabHooks | None = None
) -> dict[str, Any]:
    """Run the frozen scenario, always clean up owned children, write the report."""
    runner = AgencyNetRunner(binary, output, whole_timeout, hooks)
    runner.started_utc = iso_utc(runner.hooks.now_utc())
    started = runner.hooks.monotonic()
    cleanup: dict[str, Any] = {}
    try:
        for name in PHASE_SEQUENCE:
            if runner.failures:
                runner.record(name, "skipped", "not executed after earlier failure")
                continue
            phase_start = runner.hooks.monotonic()
            try:
                getattr(runner, f"phase_{name}")()
            except LabTimeout as exc:
                runner.failures.append(f"{name}: timeout: {exc}")
            except LabError as exc:
                runner.failures.append(f"{name}: {exc}")
            except (KeyboardInterrupt, SystemExit) as exc:
                runner.failures.append(f"{name}: interrupted: {type(exc).__name__}: {exc}")
            except Exception as exc:  # noqa: BLE001 - record failure and finish cleanup
                runner.failures.append(f"{name}: unexpected {type(exc).__name__}: {exc}")
            duration = runner.hooks.monotonic() - phase_start
            if runner.failures:
                runner.record(name, "failed", runner.failures[-1], duration)
            else:
                runner.record(name, "passed", "ok", duration)
    finally:
        node_cleanup = stop_all(list(runner.processes.values()))
        host_cleanup = stop_all(list(runner.hosts.values()))
        cleanup = {
            "terminated": node_cleanup["terminated"] + host_cleanup["terminated"],
            "exit_codes": {**node_cleanup["exit_codes"], **host_cleanup["exit_codes"]},
            "log_files": node_cleanup["log_files"] + host_cleanup["log_files"],
            "errors": node_cleanup["errors"] + host_cleanup["errors"],
            "confirmed_stopped": bool(
                node_cleanup["confirmed_stopped"] and host_cleanup["confirmed_stopped"]
            ),
            "groups": {"cometbft": node_cleanup, "agency-host": host_cleanup},
        }
    for message in cleanup.get("errors", []):
        runner.failures.append(f"cleanup: {message}")
    if cleanup.get("confirmed_stopped") is not True:
        runner.failures.append("cleanup: child termination not confirmed")
    runner.finished_utc = iso_utc(runner.hooks.now_utc())
    report = build_report(runner, cleanup, runner.hooks.monotonic() - started)
    validate_report(report)
    assert_sanitized(json.dumps(report))
    report_path = output / REPORT_NAME
    try:
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        raise LabError(f"report could not be written to {output}: {exc}") from exc
    return report


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point: 0 pass, 1 incomplete/error/timeout, 2 usage error."""
    args = parse_args(argv)
    try:
        binary, output, whole_timeout = validate_inputs(
            args.binary, args.output, args.whole_timeout
        )
    except UsageError as exc:
        print(f"usage error: {exc}", file=sys.stderr)
        return 2
    try:
        report = run_lab(binary, output, whole_timeout)
    except LabError as exc:
        print(f"lab error: {exc}", file=sys.stderr)
        return 1
    status = str(report["status"])
    print(f"{status} agency-net comet lab: {output / REPORT_NAME}")
    for phase in report["phases"]:
        print(
            f"  {phase['name']}: {phase['status']} ({phase['duration_s']:.1f}s) {phase['detail']}"
        )
    for line in report["failures"]:
        print(f"  failure: {line}", file=sys.stderr)
    return 0 if status == "PASS" else 1


if __name__ == "__main__":  # pragma: no cover - manual entry point
    raise SystemExit(main())
