"""Hermetic ABCI socket host tests (no CometBFT binary)."""

from __future__ import annotations

import socket
import struct

from agency.consensus.abci.host import ABCIHost
from agency.consensus.abci.wire import encode_message_field
from tests.test_consensus_grants import setup4 as grants_setup4


def _recv(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def _read_frame(sock):
    header = _recv(sock, 4)
    if header is None:
        return None
    (length,) = struct.unpack(">I", header)
    if length == 0:
        return b""
    return _recv(sock, length)


def _make_host():
    _ids, membership, _ledger = grants_setup4()
    app = __import__("agency.consensus.app", fromlist=["AgencyApplication"]).AgencyApplication(
        membership
    )
    host = ABCIHost(app, host="127.0.0.1", port=0)
    host.start()
    return host


def test_host_info_returns_frame():
    host = _make_host()
    try:
        s = socket.create_connection(("127.0.0.1", host.port), timeout=5)
        req = encode_message_field(3, b"")
        s.sendall(struct.pack(">I", len(req)) + req)
        resp = _read_frame(s)
        s.close()
        assert resp is not None and len(resp) > 0
    finally:
        host.stop()


def test_host_unknown_kind_returns_frame():
    host = _make_host()
    try:
        s = socket.create_connection(("127.0.0.1", host.port), timeout=5)
        req = encode_message_field(99, b"\x01")
        s.sendall(struct.pack(">I", len(req)) + req)
        resp = _read_frame(s)
        s.close()
        assert resp is not None
    finally:
        host.stop()


def test_host_finalize_and_commit():
    host = _make_host()
    try:
        s = socket.create_connection(("127.0.0.1", host.port), timeout=5)
        # finalize_block: field 20, empty body
        req = encode_message_field(20, b"")
        s.sendall(struct.pack(">I", len(req)) + req)
        resp = _read_frame(s)
        assert resp is not None
        # commit: field 11
        req2 = encode_message_field(11, b"")
        s.sendall(struct.pack(">I", len(req2)) + req2)
        resp2 = _read_frame(s)
        s.close()
        assert resp2 is not None
    finally:
        host.stop()
