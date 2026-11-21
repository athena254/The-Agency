"""ABCI v0.38.26 consensus-connection wire codec tests (stdlib only)."""

from __future__ import annotations

import pytest

from agency.consensus.abci import (
    WIRE_LENGTH_DELIMITED,
    CommitInfo,
    ExecTxResult,
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
    Timestamp,
    WireError,
    decode_finalize_payload,
    decode_request,
    encode_message_field,
    encode_response,
    encode_tag,
    encode_uint64_field,
    encode_varint,
    read_varint,
    split_frame,
)
from agency.consensus.abci.codec import PROPOSAL_ACCEPT, VERIFY_ACCEPT


def test_varint_roundtrip_and_bounds() -> None:
    for value in (0, 1, 127, 128, 300, 1 << 63, (1 << 64) - 1):
        encoded = encode_varint(value)
        assert read_varint(encoded, 0) == (value, len(encoded))
    assert encode_varint(0) == b"\x00"
    assert encode_varint(1) == b"\x01"
    assert encode_varint(127) == b"\x7f"
    assert encode_varint(128) == b"\x80\x01"
    assert encode_varint((1 << 64) - 1) == b"\xff" * 9 + b"\x01"
    with pytest.raises(WireError):
        read_varint(b"\x80", 0)
    with pytest.raises(WireError):
        read_varint(b"", 0)
    with pytest.raises(WireError):
        read_varint(b"\x00", 1)
    with pytest.raises(WireError):
        read_varint(b"\x00", 2)
    with pytest.raises(WireError):
        read_varint(b"\x80" * 10, 0)


def test_echo_response_exact_bytes() -> None:
    encoded = encode_response(ResponseEcho("abci_example"))
    assert encoded == bytes.fromhex("120c616263695f6578616d706c65")
    assert encoded.hex(" ") == "12 0c 61 62 63 69 5f 65 78 61 6d 70 6c 65"


def test_decode_request_echo_exact_vector() -> None:
    message = b"ok-abci-wire"
    encoded = encode_message_field(1, message)
    assert encoded.hex(" ")[:8] == "0a 0c 6f"
    envelope = decode_request(encoded)
    assert envelope.kind == "echo"
    assert envelope.echo is not None
    assert envelope.echo.message == message.decode("utf-8")


def test_decode_finalize_block_with_commit_info_and_unknown_fields() -> None:
    timestamp = encode_uint64_field(1, 1_700_000_000) + encode_uint64_field(2, 123)
    # RequestFinalizeBlock field 2 is CommitInfo (decided_last_commit); its
    # only decoded member is round = 1.
    commit_info_body = encode_uint64_field(1, 3) + encode_uint64_field(99, 7)
    body = (
        encode_message_field(1, b"a")
        + encode_message_field(1, b"b")
        + encode_message_field(2, commit_info_body)
        + encode_message_field(4, b"\xaa" * 32)
        + encode_message_field(6, timestamp)
        + encode_uint64_field(5, 42)
    )
    envelope = decode_request(encode_message_field(20, body))
    assert envelope.kind == "finalize_block"
    finalize = envelope.finalize_block
    assert finalize is not None
    assert finalize.txs == (b"a", b"b")
    assert finalize.hash == b"\xaa" * 32
    assert finalize.height == 42
    assert finalize.time == Timestamp(seconds=1_700_000_000, nanos=123)
    assert finalize.decided_last_commit == CommitInfo(round=3)


def test_decode_unknown_oneof_returns_unknown() -> None:
    data = encode_uint64_field(99, 1)
    envelope = decode_request(data)
    assert envelope.kind == "unknown"
    assert envelope.payload == data
    assert decode_request(encode_message_field(88, b"future")).kind == "unknown"


def test_response_roundtrip_stability() -> None:
    responses: tuple[ResponseEnvelope, ...] = (
        ResponseEcho("hello"),
        ResponseFlush(),
        ResponseInfo(data="v0.38.26", app_version=42, last_block_app_hash=b"app-hash"),
        ResponseInitChain(b"\x0a\x00"),
        ResponsePrepareProposal((b"tx1", b"")),
        ResponseProcessProposal(PROPOSAL_ACCEPT),
        ResponseExtendVote(b"vote-ext"),
        ResponseVerifyVoteExtension(VERIFY_ACCEPT),
        ResponseFinalizeBlock(
            tx_results=(ExecTxResult(code=0, info="ok", gas_wanted=10, gas_used=5, log="did"),),
            validator_updates=b"\x12\x03abc",
            consensus_param_updates=b"\x1a\x02cd",
            app_hash=b"\xef" * 32,
        ),
        ResponseCommit(retain_height=0),
        ResponseException("boom"),
    )
    for response in responses:
        encoded = encode_response(response)
        field_number, payload = split_frame(encoded)
        frame = encode_tag(field_number, WIRE_LENGTH_DELIMITED) + encode_varint(len(payload))
        assert frame + payload == encoded


def test_finalize_block_response_shape() -> None:
    response = ResponseFinalizeBlock(
        tx_results=(ExecTxResult(code=0, info="a"), ExecTxResult(code=0, info="b")),
        app_hash=b"\x01" * 32,
    )
    encoded = encode_response(response)
    field_number, payload = split_frame(encoded)
    assert field_number == 21
    assert encoded[0] == 0xAA
    decoded = decode_finalize_payload(payload)
    assert len(decoded.tx_results) == 2
    assert decoded.tx_results[0].code == 0
    assert decoded.tx_results[0].info == "a"
    assert decoded.tx_results[1].info == "b"
    assert decoded.app_hash == b"\x01" * 32
    assert decoded.validator_updates == b""
    assert decoded.consensus_param_updates == b""


def test_commit_response_and_flush() -> None:
    commit = encode_response(ResponseCommit(retain_height=0))
    assert commit == bytes.fromhex("6200")
    flush = encode_response(ResponseFlush())
    assert flush == bytes.fromhex("1a00")
    field_number, payload = split_frame(encode_response(ResponseCommit(retain_height=9)))
    assert field_number == 12
    assert payload == b"\x18\x09"
