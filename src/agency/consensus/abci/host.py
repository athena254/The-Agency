"""Minimal ABCI socket host bridging CometBFT to AgencyApplication.

Hermetic-testable: accepts a single client connection at a time and dispatches
framed requests through the landed codec. No network assumptions beyond TCP.
"""

from __future__ import annotations

import socket
import threading
from typing import Any, cast

from agency.consensus.abci import (
    PROPOSAL_ACCEPT,
    PROPOSAL_REJECT,
    VERIFY_ACCEPT,
    ExecTxResult,
    RequestEnvelope,
    ResponseCheckTx,
    ResponseCommit,
    ResponseEcho,
    ResponseEnvelope,
    ResponseException,
    ResponseExtendVote,
    ResponseFinalizeBlock,
    ResponseFlush,
    ResponseInfo,
    ResponseInitChain,
    ResponsePrepareProposal,
    ResponseProcessProposal,
    ResponseQuery,
    ResponseVerifyVoteExtension,
    decode_request,
    encode_response,
)
from agency.consensus.app import AgencyApplication
from agency.consensus.app_state import BlockContext


def _bft_time_ms(ts: Any) -> int:
    if ts is None:
        return 0
    return int(getattr(ts, "seconds", 0)) * 1000 + int(getattr(ts, "nanos", 0)) // 1_000_000


def _ctx(app: AgencyApplication, *, height: int, bft_time_ms: int, chain_id: str) -> BlockContext:
    mem = app._membership
    return BlockContext(
        height=height,
        chain_id=chain_id or "agency",
        bft_time_ms=bft_time_ms,
        membership_epoch=mem.epoch,
        membership_root=mem.root,
    )


class ABCIHost:
    """Socket ABCI server for one AgencyApplication.

    CometBFT 0.38 opens four concurrent connections (consensus, mempool,
    query, snapshot); each accepted connection is served on its own thread and
    every request is serialized through one lock so the shared application
    never observes concurrent mutation. An unanswered CheckTx/Query (mempool
    and query connections) would end the application connection via
    ResponseException and crash the node, so both are answered here.
    """

    on_block: Any = None

    def __init__(self, app: AgencyApplication, *, host: str = "127.0.0.1", port: int = 0) -> None:
        self._app = app
        self._host = host
        self._port = port
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._conns: list[threading.Thread] = []
        # v0.38's RequestFinalizeBlock carries no chain id; the host pins the
        # chain id from InitChain so every block context binds the genesis
        # chain deterministically across peers.
        self._chain_id = "agency"
        self.on_block = None

    @property
    def port(self) -> int:
        assert self._sock is not None
        return int(self._sock.getsockname()[1])

    def start(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self._host, self._port))
        self._sock.listen(8)
        self._sock.settimeout(0.5)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2)
        for thread in list(self._conns):
            thread.join(timeout=2)
        self._conns.clear()

    def _serve(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            thread = threading.Thread(target=self._serve_conn, args=(conn,), daemon=True)
            self._conns.append(thread)
            thread.start()

    def _serve_conn(self, conn: socket.socket) -> None:
        try:
            self._handle_conn(conn)
        finally:
            try:
                conn.close()
            except OSError:
                pass
            try:
                self._conns.remove(threading.current_thread())
            except ValueError:
                pass

    def _handle_conn(self, conn: socket.socket) -> None:
        conn.settimeout(None)
        while not self._stop.is_set():
            try:
                raw = self._read_frame(conn)
            except (ConnectionError, OSError, ValueError):
                break
            if raw is None:
                break
            try:
                env = decode_request(raw)
                with self._lock:
                    resp = self._dispatch(env)
            except Exception as exc:  # noqa: BLE001 - fail closed
                resp = ResponseException(str(exc))
            try:
                self._write_frame(conn, encode_response(resp))
            except (ConnectionError, OSError):
                break

    def _read_frame(self, conn: socket.socket) -> bytes | None:
        # CometBFT socket transport frames each protobuf message with a
        # base-128 varint length prefix (abci/types/messages.go: WriteMessage /
        # ReadMessage via protoio.NewDelimitedWriter). A fixed 4-byte length
        # prefix desynchronizes the connection and stalls the handshake.
        length = 0
        shift = 0
        while True:
            byte = _recv_exact(conn, 1)
            if byte is None:
                return None
            value = byte[0]
            length |= (value & 0x7F) << shift
            if not value & 0x80:
                break
            shift += 7
            if shift > 35:  # >2^28 bytes: no Agency message is ever this large
                raise ValueError("varint length prefix exceeds the maximum frame size")
        if length == 0:
            return b""
        if length > _MAX_FRAME_BYTES:
            raise ValueError(f"frame length {length} exceeds the maximum frame size")
        return _recv_exact(conn, length)

    def _write_frame(self, conn: socket.socket, data: bytes) -> None:
        header = bytearray()
        length = len(data)
        while True:
            byte = length & 0x7F
            length >>= 7
            if length:
                header.append(byte | 0x80)
            else:
                header.append(byte)
                break
        conn.sendall(bytes(header) + data)

    def _dispatch(self, env: RequestEnvelope) -> ResponseEnvelope:
        app = self._app
        kind = env.kind
        if kind == "echo":
            return ResponseEcho(env.echo.message if env.echo else "")
        if kind == "flush":
            return ResponseFlush()
        if kind == "info":
            info = app.info()
            return ResponseInfo(
                data="agency",
                app_version=info.app_version,
                last_block_height=info.last_block_height,
                last_block_app_hash=info.last_block_app_hash.encode()
                if isinstance(info.last_block_app_hash, str)
                else info.last_block_app_hash,
            )
        if kind == "init_chain":
            req = env.init_chain
            if req is not None and req.chain_id:
                self._chain_id = req.chain_id
            return ResponseInitChain()
        if kind == "query":
            info = app.info()
            return ResponseQuery(
                code=0,
                height=info.last_block_height,
                value=info.last_block_app_hash.encode()
                if isinstance(info.last_block_app_hash, str)
                else info.last_block_app_hash,
            )
        if kind == "check_tx":
            check = env.check_tx
            code, log = app.check_tx(check.tx if check else b"")
            return ResponseCheckTx(code=code, log=log)
        if kind == "prepare_proposal":
            pp = env.prepare_proposal
            txs = list(pp.txs) if pp else []
            max_bytes = pp.max_tx_bytes if pp else 0
            selected = app.prepare_proposal(txs, max_bytes)
            return ResponsePrepareProposal(txs=tuple(selected))
        if kind == "process_proposal":
            proc = env.process_proposal
            txs = list(proc.txs) if proc else []
            height = proc.height if proc else 0
            bft = _bft_time_ms(proc.time if proc else None)
            ctx = _ctx(app, height=height, bft_time_ms=bft, chain_id=self._chain_id)
            verdict = app.process_proposal(txs, ctx)
            status = PROPOSAL_ACCEPT if verdict == "ACCEPT" else PROPOSAL_REJECT
            return ResponseProcessProposal(status=status)
        if kind == "extend_vote":
            ev = env.extend_vote
            height = ev.height if ev else 0
            raw_hash = ev.hash if ev else b""
            app_hash = (
                raw_hash.decode("utf-8", "replace")
                if isinstance(raw_hash, (bytes, bytearray))
                else str(raw_hash or "")
            )
            ext = app.extend_vote(height, app_hash)
            return ResponseExtendVote(vote_extension=ext.encode() if isinstance(ext, str) else ext)
        if kind == "verify_vote_extension":
            vv = env.verify_vote_extension
            height = vv.height if vv else 0
            raw_hash = vv.hash if vv else b""
            app_hash = (
                raw_hash.decode("utf-8", "replace")
                if isinstance(raw_hash, (bytes, bytearray))
                else str(raw_hash or "")
            )
            ext = vv.vote_extension if vv else b""
            verdict = app.verify_vote_extension(height, app_hash, ext)
            status = VERIFY_ACCEPT if verdict == "ACCEPT" else (0 if verdict == "UNKNOWN" else 2)
            return ResponseVerifyVoteExtension(status=status)
        if kind == "finalize_block":
            fb = env.finalize_block
            txs = list(fb.txs) if fb else []
            height = fb.height if fb else 0
            bft = _bft_time_ms(fb.time if fb else None)
            ctx = _ctx(app, height=height, bft_time_ms=bft, chain_id=self._chain_id)
            result = app.finalize_block(txs, ctx)
            tx_results = tuple(ExecTxResult(code=0, info="ok") for _ in txs)
            if self.on_block is not None:
                try:
                    self.on_block(result, ctx)
                except Exception:  # noqa: BLE001, S110
                    pass
            raw_hash1: object = result.app_hash
            app_hash_bytes = cast(
                bytes, raw_hash1.encode() if isinstance(raw_hash1, str) else raw_hash1
            )
            return ResponseFinalizeBlock(tx_results=tx_results, app_hash=app_hash_bytes)
        if kind == "commit":
            app.commit()
            # retain_height=0 keeps the whole block history: a nonzero retain
            # height instructs CometBFT to prune the blocks below it, which
            # would make committed blocks unreplayable after a node restart
            # (handshake replay needs the stored block data).
            return ResponseCommit(retain_height=0)
        return ResponseException("unknown request kind")


_MAX_FRAME_BYTES: int = 1 << 28  # cometbft maxMsgSize is MaxInt32; we fail closed far earlier


def _recv_exact(conn: socket.socket, n: int) -> bytes | None:
    buf = bytearray()
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)
