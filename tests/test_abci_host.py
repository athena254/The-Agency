"""Hermetic ABCI socket host tests (no CometBFT binary).

Framing helpers mirror the CometBFT socket transport: a base-128 varint length
prefix per protobuf message (abci/types/messages.go WriteMessage/ReadMessage).
"""

from __future__ import annotations

import socket

from agency.consensus.abci.host import ABCIHost
from agency.consensus.abci.wire import encode_message_field
from tests.test_consensus_grants import setup4 as grants_setup4


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _recv(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def _read_varint(sock):
    value = 0
    shift = 0
    while True:
        first = _recv(sock, 1)
        if not first:
            return None
        byte = first[0]
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value
        shift += 7


def _read_frame(sock):
    length = _read_varint(sock)
    if length is None:
        return None
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
        s.sendall(_varint(len(req)) + req)
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
        s.sendall(_varint(len(req)) + req)
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
        s.sendall(_varint(len(req)) + req)
        resp = _read_frame(s)
        assert resp is not None
        # commit: field 11
        req2 = encode_message_field(11, b"")
        s.sendall(_varint(len(req2)) + req2)
        resp2 = _read_frame(s)
        s.close()
        assert resp2 is not None
    finally:
        host.stop()


def test_host_check_tx_accepts_valid_ballot_and_refuses_replay():
    from tests.test_consensus_app_state import ballot as make_ballot
    from tests.test_consensus_app_state import proposal

    ids, membership, _ledger = grants_setup4()
    from agency.consensus.app import AgencyApplication

    app = AgencyApplication(membership)
    host = ABCIHost(app, host="127.0.0.1", port=0)
    host.start()
    try:
        s = socket.create_connection(("127.0.0.1", host.port), timeout=5)
        tx = make_ballot(ids[0], membership, proposal("a"), "approve", 1)
        body = encode_message_field(1, tx)  # RequestCheckTx.tx = 1
        req = encode_message_field(8, body)  # Request.check_tx = 8
        s.sendall(_varint(len(req)) + req)
        resp = _read_frame(s)
        assert resp is not None
        fields = list(
            __import__("agency.consensus.abci.wire", fromlist=["iter_fields"]).iter_fields(resp)
        )
        assert fields[0][0] == 9  # Response.check_tx = 9
        assert resp is not None
        s.close()

        # commit the ballot, then CheckTx on the same bytes must refuse (replay)
        body = encode_message_field(1, tx)
        req = encode_message_field(20, body)
        s2 = socket.create_connection(("127.0.0.1", host.port), timeout=5)
        s2.sendall(_varint(len(req)) + req)
        _read_frame(s2)
        req2 = encode_message_field(11, b"")
        s2.sendall(_varint(len(req2)) + req2)
        _read_frame(s2)
        s2.close()

        s3 = socket.create_connection(("127.0.0.1", host.port), timeout=5)
        req = encode_message_field(8, encode_message_field(1, tx))
        s3.sendall(_varint(len(req)) + req)
        resp = _read_frame(s3)
        s3.close()
        code_fields = list(
            __import__("agency.consensus.abci.wire", fromlist=["iter_fields"]).iter_fields(resp)
        )
        # Response.check_tx frame with code=1 (nonzero)
        assert code_fields[0][0] == 9
        inner = list(
            __import__("agency.consensus.abci.wire", fromlist=["iter_fields"]).iter_fields(
                code_fields[0][2]
            )
        )
        assert inner[0][0] == 1 and inner[0][2] == 1
    finally:
        host.stop()


def test_host_query_returns_height_and_root():
    host = _make_host()
    try:
        s = socket.create_connection(("127.0.0.1", host.port), timeout=5)
        req = encode_message_field(6, b"")  # Request.query = 6, empty
        s.sendall(_varint(len(req)) + req)
        resp = _read_frame(s)
        s.close()
        assert resp is not None
        fields = list(
            __import__("agency.consensus.abci.wire", fromlist=["iter_fields"]).iter_fields(resp)
        )
        assert fields[0][0] == 7  # Response.query = 7
    finally:
        host.stop()
