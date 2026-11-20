"""Minimal ABCI socket host bridging CometBFT to AgencyApplication.

Hermetic-testable: accepts a single client connection at a time and dispatches
framed requests through the landed codec. No network assumptions beyond TCP.
"""

from __future__ import annotations

import socket
import struct
import threading
from typing import Any, cast

from agency.consensus.abci import (
    PROPOSAL_ACCEPT,
    PROPOSAL_REJECT,
    VERIFY_ACCEPT,
    ExecTxResult,
    RequestEnvelope,
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
    on_block: Any = None

    def __init__(self, app: AgencyApplication, *, host: str = "127.0.0.1", port: int = 0) -> None:
        self._app = app
        self._host = host
        self._port = port
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.on_block = None

    @property
    def port(self) -> int:
        assert self._sock is not None
        return int(self._sock.getsockname()[1])

    def start(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self._host, self._port))
        self._sock.listen(4)
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

    def _serve(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            try:
                self._handle_conn(conn)
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

    def _handle_conn(self, conn: socket.socket) -> None:
        conn.settimeout(None)
        while not self._stop.is_set():
            try:
                raw = self._read_frame(conn)
            except (ConnectionError, OSError, struct.error):
                break
            if raw is None:
                break
            try:
                env = decode_request(raw)
                resp = self._dispatch(env)
            except Exception as exc:  # noqa: BLE001 - fail closed
                resp = ResponseException(str(exc))
            try:
                self._write_frame(conn, encode_response(resp))
            except (ConnectionError, OSError):
                break

    def _read_frame(self, conn: socket.socket) -> bytes | None:
        header = _recv_exact(conn, 4)
        if header is None:
            return None
        (n,) = struct.unpack(">I", header)
        if n == 0:
            return b""
        data = _recv_exact(conn, n)
        if data is None:
            return None
        return data

    def _write_frame(self, conn: socket.socket, data: bytes) -> None:
        conn.sendall(struct.pack(">I", len(data)) + data)

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
                last_block_app_hash=info.last_block_app_hash.encode() if isinstance(info.last_block_app_hash, str) else info.last_block_app_hash,
            )
        if kind == "init_chain":
            return ResponseInitChain()
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
            chain_id = "agency"
            ctx = _ctx(app, height=height, bft_time_ms=bft, chain_id=chain_id)
            verdict = app.process_proposal(txs, ctx)
            status = PROPOSAL_ACCEPT if verdict == "ACCEPT" else PROPOSAL_REJECT
            return ResponseProcessProposal(status=status)
        if kind == "extend_vote":
            ev = env.extend_vote
            height = ev.height if ev else 0
            raw_hash = ev.hash if ev else b""
            app_hash = raw_hash.decode("utf-8", "replace") if isinstance(raw_hash, (bytes, bytearray)) else str(raw_hash or "")
            ext = app.extend_vote(height, app_hash)
            return ResponseExtendVote(vote_extension=ext.encode() if isinstance(ext, str) else ext)
        if kind == "verify_vote_extension":
            vv = env.verify_vote_extension
            height = vv.height if vv else 0
            raw_hash = vv.hash if vv else b""
            app_hash = raw_hash.decode("utf-8", "replace") if isinstance(raw_hash, (bytes, bytearray)) else str(raw_hash or "")
            ext = vv.vote_extension if vv else b""
            verdict = app.verify_vote_extension(height, app_hash, ext)
            status = VERIFY_ACCEPT if verdict == "ACCEPT" else (0 if verdict == "UNKNOWN" else 2)
            return ResponseVerifyVoteExtension(status=status)
        if kind == "finalize_block":
            fb = env.finalize_block
            txs = list(fb.txs) if fb else []
            height = fb.height if fb else 0
            bft = _bft_time_ms(fb.time if fb else None)
            chain_id = (fb.header.chain_id if (fb and fb.header) else "") or "agency"
            ctx = _ctx(app, height=height, bft_time_ms=bft, chain_id=chain_id)
            result = app.finalize_block(txs, ctx)
            tx_results = tuple(ExecTxResult(code=0, info="ok") for _ in txs)
            if self.on_block is not None:
                try:
                    self.on_block(result, ctx)
                except Exception:  # noqa: BLE001, S110
                    pass
            raw_hash1: object = result.app_hash
            app_hash_bytes = cast(bytes, raw_hash1.encode() if isinstance(raw_hash1, str) else raw_hash1)
            return ResponseFinalizeBlock(tx_results=tx_results, app_hash=app_hash_bytes)
        if kind == "commit":
            app.commit()
            return ResponseCommit(retain_height=app.info().last_block_height)
        return ResponseException("unknown request kind")


def _recv_exact(conn: socket.socket, n: int) -> bytes | None:
    buf = bytearray()
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)
