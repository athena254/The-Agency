"""Engine-only CometBFT lab harness (stdlib only, loopback only).

Launches four local ``cometbft`` 0.38.26 processes with the built-in
``persistent_kvstore`` ABCI app, exercises transaction commit, single-process
crash/rejoin and quorum loss, then writes a sanitized ``report.json``.

This is engine evidence only. It does NOT prove Byzantine fault tolerance,
network-partition resilience, independent-host resilience, Agency policy
authorization or production readiness. It is not imported by production code.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import socket
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

SCHEMA = "agency.lab.comet_engine_report/v1"
LABEL = "engine-only"
REPORT_NAME = "report.json"
EXPECTED_VERSION = "0.38.26"
VALIDATOR_COUNT = 4
VERSION_TIMEOUT_S = 30.0
TESTNET_TIMEOUT_S = 120.0
NODE_ID_TIMEOUT_S = 30.0
STAGE_TIMEOUT_S = 90.0
TX_POLL_TIMEOUT_S = 30.0
POLL_INTERVAL_S = 0.5
RPC_TIMEOUT_S = 5.0
QUORUM_LOSS_OBSERVE_S = 20.0
QUORUM_LOSS_STABLE_S = 2.0
DEFAULT_WHOLE_TIMEOUT_S = 420.0
PROBE_KEY = "probe"
TX4_VALUE = "v4"
TX5_VALUE = "v5"
QUORUM_LOSS_VALUE = "v6"
NOT_PROVED = (
    "byzantine-fault-tolerance",
    "network-partition",
    "independent-host-resilience",
    "agency-policy-authorization",
    "production-readiness",
)
FORBIDDEN_REPORT_MARKERS = ("priv_validator_key.json", "node_key.json", "PRIVATE KEY")


class LabError(Exception):
    """Base class for lab harness failures."""


class UsageError(LabError):
    """Invalid command line input: exit code 2."""


class LabTimeout(LabError):
    """A bounded wait or deadline expired."""


class ReportError(LabError):
    """The produced report contradicts itself or is not sanitized."""


@dataclass(frozen=True)
class Inputs:
    """Validated CLI inputs."""

    binary: Path
    output: Path
    whole_timeout: float


@dataclass(frozen=True)
class NodeSpec:
    """One lab validator node: independent home, keys and loopback ports."""

    index: int
    node_id: str
    home: Path
    p2p_port: int
    rpc_port: int

    @property
    def name(self) -> str:
        return f"node{self.index}"

    @property
    def p2p_laddr(self) -> str:
        return f"tcp://127.0.0.1:{self.p2p_port}"

    @property
    def rpc_laddr(self) -> str:
        return f"tcp://127.0.0.1:{self.rpc_port}"

    @property
    def rpc_url(self) -> str:
        return f"http://127.0.0.1:{self.rpc_port}"


def validate_binary_arg(raw: str) -> Path:
    """Require an absolute path to an existing file (not a directory)."""
    path = Path(raw)
    if not path.is_absolute():
        raise UsageError(f"--binary must be an absolute path: {raw!r}")
    if path.is_dir():
        raise UsageError(f"--binary must be a file, not a directory: {raw!r}")
    if not path.is_file():
        raise UsageError(f"--binary does not exist: {raw!r}")
    return path


def prepare_output_dir(raw: str) -> Path:
    """Create the output dir (with parents) or reuse an existing empty one."""
    path = Path(raw)
    if path.exists():
        if not path.is_dir():
            raise UsageError(f"--output exists and is not a directory: {raw!r}")
        if any(path.iterdir()):
            raise UsageError(
                f"--output exists and is not empty; refusing to erase lab state: {raw!r}"
            )
        return path
    path.mkdir(parents=True, exist_ok=True)
    return path


def validate_whole_timeout(value: float) -> float:
    """Reject non-positive and non-finite whole-run timeouts."""
    timeout = float(value)
    if not math.isfinite(timeout):
        raise UsageError(f"--whole-timeout must be finite, got {value!r}")
    if timeout <= 0.0:
        raise UsageError(f"--whole-timeout must be positive, got {value!r}")
    return timeout


def validate_inputs(binary: str, output: str, whole_timeout: float) -> Inputs:
    """Validate every CLI input; returns the frozen validated bundle."""
    whole = validate_whole_timeout(whole_timeout)
    binary_path = validate_binary_arg(binary)
    output_path = prepare_output_dir(output)
    return Inputs(binary=binary_path, output=output_path, whole_timeout=whole)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments (exit code 2 on missing/invalid input)."""
    parser = argparse.ArgumentParser(
        prog="agency.lab.comet_smoke",
        description="Engine-only 4-process CometBFT loopback lab (not Byzantine proof).",
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


def version_supported(stdout: str, expected: str = EXPECTED_VERSION) -> bool:
    """True only when the stripped version output is exactly ``expected``.

    ``cometbft version`` prints exactly one version token; accepting a token from
    a longer/wrong-leading output (e.g. "0.38.25 0.38.26") would let the wrong
    binary through the version gate.
    """
    return stdout.strip() == expected


def read_kvstore_value(value_b64: str) -> str:
    """Decode an ABCI query ``response.value`` (base64) as text."""
    return base64.b64decode(value_b64).decode("utf-8")


def probe_tx(key: str, value: str) -> str:
    """Build the synthetic ``key=value`` transaction payload."""
    return f"{key}={value}"


@dataclass(frozen=True)
class TomlEdit:
    """One deliberate ``(section, key)`` rewrite in a TOML config."""

    section: str
    key: str
    value: str | bool | int

    def rendered(self) -> str:
        if isinstance(self.value, bool):
            return "true" if self.value else "false"
        if isinstance(self.value, int):
            return str(self.value)
        return json.dumps(self.value)


def edit_toml_text(text: str, edits: Sequence[TomlEdit]) -> str:
    """Rewrite ``(section, key)`` lines, tracking the current section.

    Each edit must match exactly one active (non-comment) assignment line inside
    its own section; zero or multiple matches is fatal. Unrelated lines are
    returned byte-identical.
    """
    lines = text.splitlines(keepends=True)
    for edit in edits:
        current: str | None = None
        matches: list[int] = []
        for index, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if stripped.startswith("[") and stripped.endswith("]"):
                current = stripped.strip("[]").strip()
                continue
            if current != edit.section or "=" not in stripped:
                continue
            if stripped.split("=", 1)[0].strip() == edit.key:
                matches.append(index)
        if len(matches) != 1:
            raise LabError(
                f"config edit [{edit.section}] {edit.key} matched {len(matches)} lines "
                "(exactly one required)"
            )
        lines[matches[0]] = f"{edit.key} = {edit.rendered()}\n"
    return "".join(lines)


def load_toml(path: Path) -> dict[str, Any]:
    """Parse a TOML file into plain Python data."""
    with path.open("rb") as handle:
        data: dict[str, Any] = tomllib.load(handle)
    return data


def rewrite_config(path: Path, edits: Sequence[TomlEdit]) -> str:
    """Apply ``edits`` to ``path`` and return the resulting text."""
    original = path.read_text(encoding="utf-8")
    updated = edit_toml_text(original, edits)
    path.write_text(updated, encoding="utf-8")
    return updated


def verify_config(path: Path, edits: Sequence[TomlEdit]) -> None:
    """Re-parse the rewritten config and assert every edit landed."""
    data = load_toml(path)
    for edit in edits:
        section = data.get(edit.section)
        if not isinstance(section, dict) or section.get(edit.key) != edit.value:
            actual = section.get(edit.key) if isinstance(section, dict) else None
            raise LabError(
                f"config verification failed for [{edit.section}] {edit.key}: "
                f"expected {edit.value!r}, found {actual!r}"
            )


def allocate_ports(count: int) -> list[int]:
    """Reserve ``count`` distinct ephemeral ports bound to 127.0.0.1."""
    ports: list[int] = []
    socks: list[socket.socket] = []
    attempts = 0
    max_attempts = max(count, 1) * 4
    try:
        while len(ports) < count:
            if attempts >= max_attempts:
                raise LabError(
                    f"allocate_ports: could not bind {count} port(s) in {attempts} attempt(s)"
                )
            attempts += 1
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.bind(("127.0.0.1", 0))
                port = int(sock.getsockname()[1])
            except OSError:
                sock.close()
                continue
            except BaseException:
                sock.close()
                raise
            if port in ports:
                sock.close()
                continue
            ports.append(port)
            socks.append(sock)
    finally:
        for sock in socks:
            sock.close()
    return ports


def build_persistent_peers(nodes: Sequence[NodeSpec], index: int) -> str:
    """Comma-separated ``ID@127.0.0.1:port`` list for one node, excluding itself."""
    return ",".join(
        f"{node.node_id}@127.0.0.1:{node.p2p_port}" for node in nodes if node.index != index
    )


def config_edits(rpc_port: int, p2p_port: int, persistent_peers: str) -> tuple[TomlEdit, ...]:
    """The exact lab-critical rewrites from the frozen spec, in order."""
    return (
        TomlEdit("rpc", "laddr", f"tcp://127.0.0.1:{rpc_port}"),
        TomlEdit("p2p", "laddr", f"tcp://127.0.0.1:{p2p_port}"),
        TomlEdit("p2p", "persistent_peers", persistent_peers),
        TomlEdit("p2p", "seeds", ""),
        TomlEdit("p2p", "pex", False),
        TomlEdit("rpc", "unsafe", False),
        TomlEdit("p2p", "addr_book_strict", False),
        TomlEdit("p2p", "allow_duplicate_ip", True),
        TomlEdit("instrumentation", "prometheus", False),
        TomlEdit("rpc", "pprof_laddr", ""),
    )


def testnet_argv(binary: Path, output: Path) -> list[str]:
    return [
        str(binary),
        "testnet",
        "--v",
        str(VALIDATOR_COUNT),
        "--populate-persistent-peers=false",
        "--o",
        str(output),
    ]


def show_node_id_argv(binary: Path, home: Path) -> list[str]:
    return [str(binary), "show-node-id", "--home", str(home)]


@dataclass
class Deadline:
    """Whole-run deadline shared by every wait loop and subprocess call."""

    whole_timeout: float
    clock: Callable[[], float] = time.monotonic
    started: float = 0.0

    def __post_init__(self) -> None:
        self.started = self.clock()

    def remaining(self) -> float:
        """Seconds left, never negative."""
        return max(0.0, self.whole_timeout - (self.clock() - self.started))

    def expired(self) -> bool:
        return self.remaining() <= 0.0

    def bound(self, cap_s: float) -> float:
        """``min(cap_s, remaining())``; raises once nothing is left."""
        value = min(cap_s, self.remaining())
        if value <= 0.0:
            raise LabTimeout(
                f"whole-run deadline of {self.whole_timeout:.1f}s exhausted before {cap_s:.1f}s wait"
            )
        return value


class RpcError(LabError):
    """JSON-RPC error object returned by CometBFT."""

    def __init__(
        self,
        message: str,
        code: int | None = None,
        data: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.data = data

    @property
    def not_found(self) -> bool:
        """True only for a verified -32603 'tx (HEX) not found' response."""
        if self.code != -32603:
            return False
        data = self.data
        if not isinstance(data, str):
            return False
        if not (data.startswith("tx (") and data.endswith(") not found")):
            return False
        inner = data[4:-11]
        return len(inner) == 64 and all(ch in "0123456789abcdefABCDEF" for ch in inner)


def dig(data: Any, *keys: str) -> Any:
    """Fetch a nested mapping path or fail loudly with the missing path."""
    current = data
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            raise LabError(f"RPC response missing field {'/'.join(keys)}")
        current = current[key]
    return current


class RpcClient:
    """Bounded loopback HTTP RPC client for a single lab node."""

    def __init__(self, base_url: str, deadline: Deadline) -> None:
        self._base_url = base_url
        self._deadline = deadline

    def _timeout(self) -> float:
        return min(RPC_TIMEOUT_S, self._deadline.bound(RPC_TIMEOUT_S))

    def call(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        try:
            with urllib.request.urlopen(url, timeout=self._timeout()) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read()
            finally:
                exc.close()
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise LabError(f"RPC {path} failed on {self._base_url}: {exc}") from exc
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LabError(f"RPC {path} returned a non-JSON body") from exc
        if not isinstance(payload, dict):
            raise LabError(f"RPC {path} returned {type(payload).__name__}, expected object")
        error = payload.get("error")
        if error:
            code = error.get("code") if isinstance(error, dict) else None
            message = error.get("message") if isinstance(error, dict) else str(error)
            data = error.get("data") if isinstance(error, dict) else None
            if not isinstance(data, str):
                data = None
            raise RpcError(
                f"RPC {path} error {code}: {message}" + (f" ({data})" if data else ""), code, data
            )
        return payload

    def result(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        payload = self.call(path, params)
        result = payload.get("result")
        if not isinstance(result, dict):
            raise LabError(f"RPC {path} returned no result object")
        return result

    def latest_height(self) -> int:
        sync_info = dig(self.result("/status"), "sync_info")
        return int(dig(sync_info, "latest_block_height"))

    def broadcast_tx_sync(self, payload: str) -> dict[str, Any]:
        params = {"tx": json.dumps(payload)}
        return self.result("/broadcast_tx_sync", params)

    def tx(self, tx_hash: str) -> dict[str, Any]:
        """Fetch a tx by its internal canonical hash."""
        if (
            not isinstance(tx_hash, str)
            or len(tx_hash) != 64
            or not all(ch in "0123456789abcdefABCDEF" for ch in tx_hash)
        ):
            raise LabError(f"tx hash must be 64 hexadecimal chars, got {tx_hash!r}")
        return self.result("/tx", {"hash": "0x" + tx_hash, "prove": "false"})

    def query_value(self, key: str) -> str:
        response = dig(self.result("/abci_query", {"data": json.dumps(key)}), "response")
        value = dig(response, "value")
        if not isinstance(value, str):
            raise LabError("abci_query response.value was not a base64 string")
        return value

    def app_hash(self, height: int) -> str:
        header = dig(self.result("/commit", {"height": str(height)}), "signed_header", "header")
        value = dig(header, "app_hash")
        if not isinstance(value, str) or not value:
            raise LabError(f"empty app_hash at height {height}")
        return value


def node_argv(binary: Path, node: NodeSpec, persistent_peers: str) -> list[str]:
    return [
        str(binary),
        "start",
        "--home",
        str(node.home),
        "--proxy_app",
        "persistent_kvstore",
        "--p2p.laddr",
        node.p2p_laddr,
        "--rpc.laddr",
        node.rpc_laddr,
        "--p2p.persistent_peers",
        persistent_peers,
        "--p2p.pex=false",
        "--rpc.unsafe=false",
        "--log_level",
        "error",
    ]


@dataclass(frozen=True)
class CommandResult:
    """Result of a bounded one-shot subprocess call."""

    returncode: int
    stdout: str
    stderr: str


def _default_run(argv: Sequence[str], timeout: float) -> CommandResult:
    completed = subprocess.run(
        list(argv),
        capture_output=True,
        timeout=timeout,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    return CommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout.decode("utf-8", errors="replace"),
        stderr=completed.stderr.decode("utf-8", errors="replace"),
    )


def _default_popen(argv: Sequence[str], handle: IO[bytes]) -> Any:
    return subprocess.Popen(  # explicit lab binary path, never a shell
        list(argv),
        stdin=subprocess.DEVNULL,
        stdout=handle,
        stderr=subprocess.STDOUT,
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class LabHooks:
    """Replaceable side-effect seams (unit tests inject subprocess-free fakes)."""

    popen: Callable[[Sequence[str], IO[bytes]], Any] = _default_popen
    run: Callable[[Sequence[str], float], CommandResult] = _default_run
    make_rpc: Callable[[str, Deadline], RpcClient] = RpcClient
    allocate_ports: Callable[[int], list[int]] = allocate_ports
    sleep: Callable[[float], None] = time.sleep
    monotonic: Callable[[], float] = time.monotonic
    now_utc: Callable[[], datetime] = _utc_now


@dataclass
class NodeProcess:
    """One owned lab child process plus its log handle."""

    spec: NodeSpec
    proc: Any
    handle: IO[bytes]
    log_name: str
    exit_code: int | None = None

    def running(self) -> bool:
        return self.proc.poll() is None


def run_command(
    hooks: LabHooks, argv: Sequence[str], deadline: Deadline, cap_s: float
) -> CommandResult:
    """Run a bounded one-shot command; timeout or nonzero exit is fatal."""
    label = argv[1] if len(argv) > 1 else str(argv[0])
    try:
        result = hooks.run(argv, deadline.bound(cap_s))
    except subprocess.TimeoutExpired as exc:
        raise LabTimeout(f"command {label} timed out") from exc
    if result.returncode != 0:
        raise LabError(
            f"command {label} failed ({result.returncode}): {' '.join(argv)}\n"
            f"stdout: {result.stdout.strip()}\nstderr: {result.stderr.strip()}"
        )
    return result


def start_node(
    hooks: LabHooks,
    binary: Path,
    node: NodeSpec,
    persistent_peers: str,
    output: Path,
) -> NodeProcess:
    """Start one node with its log redirected into the lab output dir."""
    log_name = f"{node.name}.log"
    handle = (output / log_name).open("ab")
    try:
        proc = hooks.popen(node_argv(binary, node, persistent_peers), handle)
    except Exception:
        handle.close()
        raise
    return NodeProcess(spec=node, proc=proc, handle=handle, log_name=log_name)


def stop_node(node_proc: NodeProcess, grace_s: float = 10.0) -> int:
    """Terminate then (if needed) kill one child; always close the log."""
    try:
        if node_proc.exit_code is None:
            try:
                if node_proc.running():
                    node_proc.proc.terminate()
                node_proc.proc.wait(timeout=grace_s)
            except (subprocess.TimeoutExpired, OSError):
                node_proc.proc.kill()
                node_proc.proc.wait(timeout=grace_s)
        code = node_proc.proc.poll()
        if code is None:
            raise LabError(f"child {node_proc.spec.name} still running after stop")
        node_proc.exit_code = int(code)
        return node_proc.exit_code
    finally:
        if not node_proc.handle.closed:
            node_proc.handle.close()


def stop_all(processes: Sequence[NodeProcess]) -> dict[str, Any]:
    """Cleanup every owned child, always closing every log handle."""
    terminated: list[str] = []
    exit_codes: dict[str, int] = {}
    errors: list[str] = []
    alive: list[str] = []
    for node_proc in processes:
        name = node_proc.spec.name
        try:
            was_running = node_proc.running()
        except Exception as exc:  # noqa: BLE001 - record failure and finish owned-child cleanup
            was_running = False
            errors.append(f"{name}: running check failed: {type(exc).__name__}: {exc}")
        except (KeyboardInterrupt, SystemExit) as exc:
            was_running = False
            errors.append(f"{name}: running check interrupted: {type(exc).__name__}: {exc}")
        try:
            exit_codes[name] = stop_node(node_proc)
        except Exception as exc:  # noqa: BLE001 - record failure and finish owned-child cleanup
            errors.append(f"{name}: cleanup failed: {type(exc).__name__}: {exc}")
        except (KeyboardInterrupt, SystemExit) as exc:
            errors.append(f"{name}: cleanup interrupted: {type(exc).__name__}: {exc}")
        finally:
            if not node_proc.handle.closed:
                try:
                    node_proc.handle.close()
                except Exception as exc:  # noqa: BLE001 - record failure and finish owned-child cleanup
                    errors.append(f"{name}: log close failed: {type(exc).__name__}: {exc}")
        try:
            still_running = node_proc.proc.poll()
        except Exception as exc:  # noqa: BLE001 - record failure and finish owned-child cleanup
            still_running = None
            errors.append(f"{name}: post-cleanup poll failed: {type(exc).__name__}: {exc}")
        except (KeyboardInterrupt, SystemExit) as exc:
            still_running = None
            errors.append(f"{name}: post-cleanup poll interrupted: {type(exc).__name__}: {exc}")
        if still_running is None:
            alive.append(name)
        elif was_running:
            terminated.append(name)
    if alive:
        errors.append("could not confirm stopped: " + ", ".join(alive))
    return {
        "terminated": terminated,
        "exit_codes": exit_codes,
        "log_files": [node_proc.log_name for node_proc in processes],
        "errors": errors,
        "confirmed_stopped": not alive,
    }


def poll_until(
    hooks: LabHooks,
    deadline: Deadline,
    stage_timeout_s: float,
    what: str,
    attempt: Callable[[], Any | None],
    interval_s: float = POLL_INTERVAL_S,
) -> Any:
    """Bounded poll: stage timeout AND whole deadline; honest failure message."""
    start = hooks.monotonic()
    budget = min(stage_timeout_s, deadline.remaining())
    if budget <= 0.0:
        raise LabTimeout(f"{what}: no whole-run budget left")
    last_error = ""
    while True:
        if hooks.monotonic() - start >= budget or deadline.expired():
            raise LabTimeout(f"{what} not satisfied within {budget:.1f}s{last_error}")
        try:
            value = attempt()
        except RpcError as exc:
            if exc.not_found:
                value = None
                last_error = f" (last error: {exc})"
            else:
                raise
        except LabError as exc:
            value = None
            last_error = f" (last error: {exc})"
        else:
            if value is not None and value is not False:
                if hooks.monotonic() - start >= budget or deadline.expired():
                    raise LabTimeout(f"{what} not satisfied within {budget:.1f}s{last_error}")
                return value
        hooks.sleep(deadline.bound(interval_s))


def probe_tx_hash(rpc: RpcClient, payload: str) -> tuple[str, int]:
    """Broadcast a probe tx and require the sync response's top-level code 0."""
    result = rpc.broadcast_tx_sync(payload)
    code = result.get("code")
    if code != 0:
        raise LabError(
            f"broadcast_tx_sync rejected {payload!r}: code={code} log={result.get('log')}"
        )
    tx_hash = result.get("hash")
    if not isinstance(tx_hash, str) or not tx_hash:
        raise LabError("broadcast_tx_sync returned no transaction hash")
    return tx_hash, int(code)


def find_committed_height(
    hooks: LabHooks,
    deadline: Deadline,
    rpc: RpcClient,
    tx_hash: str,
    stage_timeout_s: float = TX_POLL_TIMEOUT_S,
) -> int:
    """Poll /tx until the transaction is found and its execution succeeded."""

    def attempt() -> int | None:
        try:
            result = rpc.tx(tx_hash)
        except RpcError as exc:
            if exc.not_found:
                return None
            raise
        code = dig(result, "tx_result", "code")
        if code != 0:
            raise LabError(f"transaction {tx_hash} failed execution: code={code}")
        height = int(dig(result, "height"))
        if height <= 0:
            raise LabError(f"transaction {tx_hash} reported height {height}")
        return height

    return int(poll_until(hooks, deadline, stage_timeout_s, f"commit of {tx_hash}", attempt))


def iso_utc(moment: datetime) -> str:
    return moment.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def lab_limits(whole_timeout: float) -> dict[str, float | int]:
    """The frozen limits block echoed into every report."""
    return {
        "validator_count": VALIDATOR_COUNT,
        "stage_timeout_s": STAGE_TIMEOUT_S,
        "tx_poll_timeout_s": TX_POLL_TIMEOUT_S,
        "version_timeout_s": VERSION_TIMEOUT_S,
        "testnet_timeout_s": TESTNET_TIMEOUT_S,
        "rpc_timeout_s": RPC_TIMEOUT_S,
        "poll_interval_s": POLL_INTERVAL_S,
        "quorum_loss_observe_s": QUORUM_LOSS_OBSERVE_S,
        "quorum_loss_stable_s": QUORUM_LOSS_STABLE_S,
        "whole_timeout_s": whole_timeout,
    }


class LabRunner:
    """Executes the frozen engine-only scenario in its fixed phase order."""

    def __init__(self, inputs: Inputs, hooks: LabHooks | None = None) -> None:
        self.inputs = inputs
        self.hooks = hooks or LabHooks()
        self.deadline = Deadline(inputs.whole_timeout, clock=self.hooks.monotonic)
        self.nodes: list[NodeSpec] = []
        self.processes: dict[int, NodeProcess] = {}
        self.rpcs: dict[int, RpcClient] = {}
        self.phases: list[dict[str, Any]] = []
        self.failures: list[str] = []
        self.heights: dict[str, Any] = {}
        self.app_hashes: list[dict[str, Any]] = []
        self.txs: dict[str, Any] = {}
        self.quorum_loss: dict[str, Any] = {
            "submitted": False,
            "check_tx_code": None,
            "observe_s": QUORUM_LOSS_OBSERVE_S,
            "observed_s": 0.0,
            "height_unchanged": False,
            "tx_found": False,
            "observed_heights": {},
            "late_commit_observed": False,
        }
        self.binary_info: dict[str, Any] = {}
        self.started_utc = ""
        self.finished_utc = ""
        self.tx4_height = 0
        self.tx3_height = 0

    # --- helpers ---------------------------------------------------------
    def rpc(self, index: int) -> RpcClient:
        client = self.rpcs.get(index)
        if client is None:
            client = self.hooks.make_rpc(self.nodes[index].rpc_url, self.deadline)
            self.rpcs[index] = client
        return client

    def record(
        self, name: str, status: str, detail: str, duration_s: float = 0.0
    ) -> dict[str, Any]:
        entry = {"name": name, "status": status, "detail": detail, "duration_s": duration_s}
        self.phases.append(entry)
        return entry

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

        return dict(poll_until(self.hooks, self.deadline, STAGE_TIMEOUT_S, what, attempt))

    def compare_app_hash(self, phase: str, height: int, indexes: Sequence[int]) -> None:
        """Require one app hash at one committed height across the given peers."""
        self.wait_heights(indexes, height, f"{phase}: peers committed height >= {height}")
        values = {f"node{index}": self.rpc(index).app_hash(height) for index in indexes}
        equal = len(set(values.values())) == 1
        self.app_hashes.append({"phase": phase, "height": height, "equal": equal, "values": values})
        if not equal:
            raise LabError(f"{phase}: app hash mismatch at committed height {height}: {values}")

    def require_value(self, expected: str, indexes: Sequence[int], what: str) -> None:
        """Poll until every listed peer's independent ABCI query returns expected."""

        def attempt() -> bool | None:
            for index in indexes:
                found = read_kvstore_value(self.rpc(index).query_value(PROBE_KEY))
                if found != expected:
                    return None
            return True

        poll_until(self.hooks, self.deadline, TX_POLL_TIMEOUT_S, what, attempt)

    def stable_heights(self, indexes: Sequence[int], stable_s: float) -> dict[str, int]:
        """Wait for a stable post-stop baseline: no listed height changes for stable_s."""
        budget = min(max(stable_s * 4.0, stable_s + POLL_INTERVAL_S), self.deadline.remaining())
        if budget <= 0.0:
            raise LabTimeout("quorum_loss: no whole-run budget for a stable baseline")
        start = self.hooks.monotonic()
        previous: dict[str, int] | None = None
        stable_since: float | None = None
        while True:
            numbers = {f"node{index}": self.rpc(index).latest_height() for index in indexes}
            now = self.hooks.monotonic()
            if previous is not None and numbers == previous:
                if stable_since is not None and now - stable_since >= stable_s:
                    return numbers
            else:
                stable_since = now
                previous = numbers
            if now - start >= budget:
                raise LabTimeout(
                    f"quorum_loss: heights never stayed stable for {stable_s:.1f}s: {numbers}"
                )
            self.hooks.sleep(self.deadline.bound(POLL_INTERVAL_S))

    # --- phases ----------------------------------------------------------
    def phase_version(self) -> None:
        """Pinned-version gate; runs before any node state exists."""
        result = run_command(
            self.hooks,
            [str(self.inputs.binary), "version"],
            self.deadline,
            VERSION_TIMEOUT_S,
        )
        stdout = result.stdout.strip()
        version = stdout.split()[0] if stdout else ""
        matches = version_supported(result.stdout)
        self.binary_info = {
            "path": str(self.inputs.binary),
            "version": version,
            "expected_version": EXPECTED_VERSION,
            "matches": matches,
        }
        if not matches:
            raise LabError(f"binary version {version!r} does not match pinned {EXPECTED_VERSION}")

    def phase_testnet(self) -> None:
        """Generate 4 homes, read public node IDs, rewrite and verify configs."""
        run_command(
            self.hooks,
            testnet_argv(self.inputs.binary, self.inputs.output),
            self.deadline,
            TESTNET_TIMEOUT_S,
        )
        ports = self.hooks.allocate_ports(2 * VALIDATOR_COUNT)
        node_ids: list[str] = []
        for index in range(VALIDATOR_COUNT):
            home = self.inputs.output / f"node{index}"
            if not home.is_dir():
                raise LabError(f"testnet did not create node home {home}")
            result = run_command(
                self.hooks,
                show_node_id_argv(self.inputs.binary, home),
                self.deadline,
                NODE_ID_TIMEOUT_S,
            )
            lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            node_id = lines[-1] if lines else ""
            if len(node_id) != 40 or any(ch not in "0123456789abcdefABCDEF" for ch in node_id):
                raise LabError(f"node {index}: unusable node ID {node_id!r}")
            node_ids.append(node_id)
        self.nodes = [
            NodeSpec(
                index=index,
                node_id=node_ids[index],
                home=self.inputs.output / f"node{index}",
                p2p_port=ports[index],
                rpc_port=ports[VALIDATOR_COUNT + index],
            )
            for index in range(VALIDATOR_COUNT)
        ]
        for node in self.nodes:
            config = node.home / "config" / "config.toml"
            if not config.is_file():
                raise LabError(f"missing generated config {config}")
            peers = build_persistent_peers(self.nodes, node.index)
            edits = config_edits(node.rpc_port, node.p2p_port, peers)
            rewrite_config(config, edits)
            verify_config(config, edits)

    def phase_start4(self) -> None:
        """Start all 4 peers and wait for the first committed heights."""
        for node in self.nodes:
            peers = build_persistent_peers(self.nodes, node.index)
            self.processes[node.index] = start_node(
                self.hooks, self.inputs.binary, node, peers, self.inputs.output
            )
        heights = self.wait_heights(
            list(range(VALIDATOR_COUNT)), 1, "start4: all 4 peers at height >= 1"
        )
        self.heights["start4"] = heights

    def phase_tx4(self) -> None:
        """One synthetic transaction: acceptance, execution, query and app hash."""
        payload = probe_tx(PROBE_KEY, TX4_VALUE)
        tx_hash, code = probe_tx_hash(self.rpc(0), payload)
        height = find_committed_height(self.hooks, self.deadline, self.rpc(0), tx_hash)
        self.txs["tx4"] = {
            "hash": tx_hash,
            "check_tx_code": code,
            "tx_result_code": 0,
            "height": height,
        }
        self.tx4_height = height
        self.heights["tx4_commit"] = height
        indexes = list(range(VALIDATOR_COUNT))
        self.require_value(TX4_VALUE, indexes, f"tx4: all 4 peers return {TX4_VALUE}")
        self.compare_app_hash("tx4", height + 1, indexes)

    def phase_stop1_commit3(self) -> None:
        """Crash one peer, then commit with the remaining three."""
        stop_node(self.processes[3])
        running = [0, 1, 2]
        payload = probe_tx(PROBE_KEY, TX5_VALUE)
        tx_hash, code = probe_tx_hash(self.rpc(0), payload)
        height = find_committed_height(self.hooks, self.deadline, self.rpc(0), tx_hash)
        if height <= self.tx4_height:
            raise LabError(
                f"stop1_commit3: expected height > {self.tx4_height} with 3 peers, got {height}"
            )
        self.txs["tx3"] = {
            "hash": tx_hash,
            "check_tx_code": code,
            "tx_result_code": 0,
            "height": height,
        }
        self.tx3_height = height
        self.heights["tx3_commit"] = height
        self.require_value(TX5_VALUE, running, f"tx3: 3 live peers return {TX5_VALUE}")
        self.compare_app_hash("tx3", height + 1, running)

    def phase_restart_catchup(self) -> None:
        """Restart the crashed peer and require true rejoin plus convergence.

        Independently queries the restarted peer (index 3) for the expected v5
        value, not merely the shared app-hash comparison.
        """
        node = self.nodes[3]
        peers = build_persistent_peers(self.nodes, node.index)
        self.processes[3] = start_node(
            self.hooks, self.inputs.binary, node, peers, self.inputs.output
        )
        self.compare_app_hash("restart", self.tx3_height + 1, list(range(VALIDATOR_COUNT)))
        restarted_value = read_kvstore_value(self.rpc(3).query_value(PROBE_KEY))
        if restarted_value != "v5":
            raise LabError(f"restart: restarted peer 3 expected v5, got {restarted_value!r}")

    def phase_quorum_loss(self) -> None:
        """2 of 4 peers: a submitted transaction must not commit in a bounded window."""
        stop_node(self.processes[2])
        stop_node(self.processes[3])
        running = [0, 1]
        baseline = self.stable_heights(running, QUORUM_LOSS_STABLE_S)
        payload = probe_tx(PROBE_KEY, QUORUM_LOSS_VALUE)
        tx_hash, code = probe_tx_hash(self.rpc(0), payload)
        self.quorum_loss["hash"] = tx_hash
        self.quorum_loss["submitted"] = True
        self.quorum_loss["check_tx_code"] = code
        self.quorum_loss["baseline_heights"] = baseline
        window = min(QUORUM_LOSS_OBSERVE_S, self.deadline.remaining())
        if window < QUORUM_LOSS_OBSERVE_S:
            raise LabTimeout(
                "quorum_loss: whole-run budget cannot cover the "
                f"{QUORUM_LOSS_OBSERVE_S:.0f}s observation window"
            )
        start = self.hooks.monotonic()
        found = False
        while self.hooks.monotonic() - start < window:
            for index in running:
                height = self.rpc(index).latest_height()
                if height != baseline[f"node{index}"]:
                    raise LabError(
                        f"quorum_loss: node{index} height moved "
                        f"{baseline[f'node{index}']} -> {height} with only 2 of 4 peers"
                    )
            try:
                self.rpc(0).tx(tx_hash)
                found = True
            except RpcError as exc:
                if not exc.not_found:
                    raise
            self.hooks.sleep(self.deadline.bound(POLL_INTERVAL_S))
        observed = {f"node{index}": self.rpc(index).latest_height() for index in running}
        self.quorum_loss["observed_s"] = round(self.hooks.monotonic() - start, 3)
        self.quorum_loss["observed_heights"] = observed
        self.quorum_loss["tx_found"] = found
        self.quorum_loss["height_unchanged"] = all(
            observed[name] == baseline[name] for name in observed
        )
        if found:
            raise LabError("quorum_loss: 2 of 4 peers committed the new transaction")
        if not self.quorum_loss["height_unchanged"]:
            raise LabError(f"quorum_loss: heights moved during the window: {observed}")

    def phase_restore(self) -> None:
        """Restart both stopped peers and prove restored liveness."""
        for index in (2, 3):
            peers = build_persistent_peers(self.nodes, index)
            self.processes[index] = start_node(
                self.hooks, self.inputs.binary, self.nodes[index], peers, self.inputs.output
            )
        nodes = list(range(VALIDATOR_COUNT))
        observed = self.quorum_loss.get("observed_heights") or {}
        floor = max(int(value) for value in observed.values())
        self.wait_heights(nodes, floor, "restore: restarted peers reach quorum-loss height")
        tx_hash, check_tx_code = probe_tx_hash(self.rpc(0), probe_tx("restore-proof", "restored"))
        committed = find_committed_height(self.hooks, self.deadline, self.rpc(0), tx_hash)
        if committed <= floor:
            raise LabError(
                f"restore: tx committed at {committed}, not above quorum-loss floor {floor}"
            )
        self.wait_heights(nodes, committed + 1, f"restore: all 4 peers reach {committed + 1}")

        def attempt() -> bool | None:
            if all(
                read_kvstore_value(self.rpc(index).query_value("restore-proof")) == "restored"
                for index in nodes
            ):
                return True
            return None

        poll_until(self.hooks, self.deadline, TX_POLL_TIMEOUT_S, "restore values", attempt)
        self.compare_app_hash("restore", committed + 1, nodes)
        self.heights["restore_common"] = committed + 1
        self.heights["restore_committed"] = committed
        self.txs["restore"] = {
            "hash": tx_hash,
            "check_tx_code": check_tx_code,
            "tx_result_code": 0,
            "height": committed,
            "key": "restore-proof",
            "value": "restored",
        }
        self.quorum_loss["late_commit_observed"] = self.late_commit_seen()

    def late_commit_seen(self) -> bool:
        """Informational: did the quorum-loss transaction commit after restore?"""
        tx_hash = self.quorum_loss.get("hash")
        if not isinstance(tx_hash, str):
            return False
        end = self.hooks.monotonic() + min(5.0, self.deadline.remaining())
        while True:
            try:
                self.rpc(0).tx(tx_hash)
                return True
            except RpcError as exc:
                if not exc.not_found:
                    raise
            if self.hooks.monotonic() >= end or self.deadline.expired():
                return False
            self.hooks.sleep(self.deadline.bound(POLL_INTERVAL_S))


PHASE_SEQUENCE = (
    "version",
    "testnet",
    "start4",
    "tx4",
    "stop1_commit3",
    "restart_catchup",
    "quorum_loss",
    "restore",
)


def build_report(
    runner: LabRunner, cleanup: Mapping[str, Any], duration_s: float
) -> dict[str, Any]:
    """Assemble the sanitized report exactly as frozen in the spec."""
    status = "PASS" if not runner.failures else "FAIL"
    binary = dict(runner.binary_info) or {
        "path": str(runner.inputs.binary),
        "version": "",
        "expected_version": EXPECTED_VERSION,
        "matches": False,
    }
    return {
        "schema": SCHEMA,
        "label": LABEL,
        "not_proved": list(NOT_PROVED),
        "status": status,
        "failures": list(runner.failures),
        "limits": lab_limits(runner.inputs.whole_timeout),
        "binary": binary,
        "output_dir": str(runner.inputs.output),
        "started_utc": runner.started_utc,
        "finished_utc": runner.finished_utc,
        "duration_s": round(duration_s, 3),
        "engine_only": True,
        "nodes": [
            {
                "index": node.index,
                "node_id": node.node_id,
                "home": str(node.home),
                "p2p_laddr": node.p2p_laddr,
                "rpc_laddr": node.rpc_laddr,
            }
            for node in runner.nodes
        ],
        "phases": list(runner.phases),
        "heights": dict(runner.heights),
        "app_hash": list(runner.app_hashes),
        "txs": dict(runner.txs),
        "quorum_loss": dict(runner.quorum_loss),
        "cleanup": dict(cleanup),
    }


def assert_sanitized(text: str) -> None:
    """Reject any report text carrying key-file or private key markers."""
    for marker in FORBIDDEN_REPORT_MARKERS:
        if marker in text:
            raise ReportError(f"report text contains forbidden marker {marker!r}")


def validate_report(report: Mapping[str, Any]) -> None:
    """Refuse self-contradicting reports (PASS with failures, FAIL with none)."""
    status = report.get("status")
    if status not in ("PASS", "FAIL"):
        raise ReportError(f"report status must be PASS or FAIL, got {status!r}")
    failures = report.get("failures")
    if not isinstance(failures, list):
        raise ReportError("report must carry a failures list")
    label = report.get("label")
    schema = report.get("schema")
    engine_only = report.get("engine_only")
    not_proved = report.get("not_proved")
    status = report.get("status")
    failures = report.get("failures")

    phases = report.get("phases")
    if not isinstance(phases, list):
        raise ReportError("report must carry a phases list")
    for phase in phases:
        if not isinstance(phase, dict):
            raise ReportError(f"every phase entry must be an object, got {phase!r}")
        name = phase.get("name")
        if not isinstance(name, str) or name not in PHASE_SEQUENCE:
            raise ReportError(f"phase has an invalid name: {name!r}")
        if phase.get("status") not in ("passed", "failed", "skipped"):
            raise ReportError(f"phase {name} has an invalid status: {phase.get('status')!r}")
    names = [phase["name"] for phase in phases]
    if len(set(names)) != len(names):
        raise ReportError(f"report phases contain duplicate names: {names}")
    if tuple(names) != PHASE_SEQUENCE:
        raise ReportError(f"report phases must be exactly {PHASE_SEQUENCE}, got {names}")
    statuses = [phase["status"] for phase in phases]

    if status == "PASS":
        if label != LABEL:
            raise ReportError(f"report label must be {LABEL!r}, got {label!r}")
        if schema != SCHEMA:
            raise ReportError(f"report schema must be {SCHEMA!r}, got {schema!r}")
        if engine_only is not True:
            raise ReportError("report must set engine_only to True")
        if not isinstance(not_proved, list) or any(label not in not_proved for label in NOT_PROVED):
            raise ReportError(f"report must list {NOT_PROVED!r} in not_proved")
        if failures:
            raise ReportError(f"PASS report with recorded failures: {failures}")
        if any(value != "passed" for value in statuses):
            raise ReportError(f"PASS report with non-passed phases: {statuses}")
        cleanup = report.get("cleanup")
        if not isinstance(cleanup, dict):
            raise ReportError("PASS report must carry a cleanup object")
        if cleanup.get("errors"):
            raise ReportError(f"PASS report with cleanup errors: {cleanup['errors']}")
        if cleanup.get("confirmed_stopped") is not True:
            raise ReportError("PASS report without confirmed child cleanup")
    elif status == "FAIL":
        if not failures:
            raise ReportError("FAIL report with no recorded failure")
    else:
        raise ReportError(f"report status must be PASS or FAIL, got {status!r}")


def write_report(output: Path, report: Mapping[str, Any]) -> Path:
    """Write the report JSON; raises if it cannot be written."""
    path = output / REPORT_NAME
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return path


def run_lab(inputs: Inputs, hooks: LabHooks | None = None) -> dict[str, Any]:
    """Run the frozen scenario, always clean up owned children, write the report."""
    runner = LabRunner(inputs, hooks)
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
            except Exception as exc:  # noqa: BLE001 - record failure and finish owned-child cleanup
                runner.failures.append(f"{name}: unexpected {type(exc).__name__}: {exc}")
            duration = runner.hooks.monotonic() - phase_start
            if runner.failures:
                runner.record(name, "failed", runner.failures[-1], duration)
            else:
                runner.record(name, "passed", "ok", duration)
    finally:
        cleanup = stop_all(list(runner.processes.values()))
    for message in cleanup.get("errors", []):
        runner.failures.append(f"cleanup: {message}")
    if cleanup.get("confirmed_stopped") is not True:
        runner.failures.append("cleanup: child termination not confirmed")
    runner.finished_utc = iso_utc(runner.hooks.now_utc())
    report = build_report(runner, cleanup, runner.hooks.monotonic() - started)
    validate_report(report)
    assert_sanitized(json.dumps(report))
    try:
        write_report(inputs.output, report)
    except OSError as exc:
        raise LabError(f"report could not be written to {inputs.output}: {exc}") from exc
    return report


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point: 0 pass, 1 incomplete/error/timeout, 2 usage error."""
    args = parse_args(argv)
    try:
        inputs = validate_inputs(args.binary, args.output, args.whole_timeout)
    except UsageError as exc:
        print(f"usage error: {exc}", file=sys.stderr)
        return 2
    try:
        report = run_lab(inputs)
    except LabError as exc:
        print(f"lab error: {exc}", file=sys.stderr)
        return 1
    status = str(report["status"])
    print(f"{status} engine-only comet lab: {inputs.output / REPORT_NAME}")
    for phase in report["phases"]:
        print(
            f"  {phase['name']}: {phase['status']} ({phase['duration_s']:.1f}s) {phase['detail']}"
        )
    for line in report["failures"]:
        print(f"  failure: {line}", file=sys.stderr)
    return 0 if status == "PASS" else 1


if __name__ == "__main__":  # pragma: no cover - manual entry point
    raise SystemExit(main())
