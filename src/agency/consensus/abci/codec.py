"""Consensus-connection ABCI message codec for CometBFT v0.38.26.

Scope is deliberately narrow: only the messages the consensus connection
exchanges with the app. Requests are decoded, responses are encoded. The module
is stdlib-only and hand-rolls the wire format via
:mod:`agency.consensus.abci.wire`.

Every field number below is authoritative for v0.38.26 and cites
``tendermint/abci/types.proto`` (lines match the v0.38.26 file). v0.38 carries
``height``/``time``/``hash`` directly on the proposal/finalize requests — there
is no embedded ``tendermint.types.Header`` on the consensus connection.

Framing: a Request is exactly one oneof member at the top level; the same for a
Response. Unknown oneof members decode as ``kind="unknown"`` so protocol
additions never break the app.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, TypeAlias

from agency.consensus.abci.wire import (
    WIRE_LENGTH_DELIMITED,
    WIRE_VARINT,
    WireError,
    encode_bytes_field,
    encode_message_field,
    encode_string_field,
    encode_uint64_field,
    iter_fields,
)

__all__ = [
    "PROPOSAL_ACCEPT",
    "PROPOSAL_REJECT",
    "PROPOSAL_UNKNOWN",
    "TIMESTAMP_SECONDS_FIELD",
    "VERIFY_ACCEPT",
    "VERIFY_REJECT",
    "VERIFY_UNKNOWN",
    "CommitInfo",
    "Echo",
    "ExecTxResult",
    "ExtendVote",
    "FinalizeBlock",
    "Flush",
    "Header",
    "Info",
    "InitChain",
    "PrepareProposal",
    "ProcessProposal",
    "RequestEnvelope",
    "ResponseCommit",
    "ResponseEcho",
    "ResponseEnvelope",
    "ResponseException",
    "ResponseExtendVote",
    "ResponseFinalizeBlock",
    "ResponseFlush",
    "ResponseInfo",
    "ResponseInitChain",
    "ResponsePrepareProposal",
    "ResponseProcessProposal",
    "ResponseVerifyVoteExtension",
    "Timestamp",
    "VerifyVoteExtension",
    "decode_finalize_payload",
    "decode_request",
    "encode_response",
    "split_frame",
]

_UINT64_MASK: Final = (1 << 64) - 1

# --- Request oneof members (types.proto:43-59, v0.38.26) ---------------------
_REQ_ECHO: Final = 1  # types.proto:44 Request.echo = 1
_REQ_FLUSH: Final = 2  # types.proto:45 Request.flush = 2
_REQ_INFO: Final = 3  # types.proto:46 Request.info = 3
_REQ_INIT_CHAIN: Final = 5  # types.proto:47 Request.init_chain = 5
_REQ_PREPARE_PROPOSAL: Final = 16  # types.proto:55 Request.prepare_proposal = 16
_REQ_PROCESS_PROPOSAL: Final = 17  # types.proto:56 Request.process_proposal = 17
_REQ_EXTEND_VOTE: Final = 18  # types.proto:57 Request.extend_vote = 18
_REQ_VERIFY_VOTE_EXTENSION: Final = 19  # types.proto:58 verify_vote_extension = 19
_REQ_FINALIZE_BLOCK: Final = 20 
  # types.proto:59 Request.finalize_block = 20

# --- Response oneof members (types.proto:200-217, v0.38.26) ------------------
_RES_EXCEPTION: Final = 1  # types.proto:201 Response.exception = 1
_RES_ECHO: Final = 2  # types.proto:202 Response.echo = 2
_RES_FLUSH: Final = 3  # types.proto:203 Response.flush = 3
_RES_INFO: Final = 4  # types.proto:204 Response.info = 4
_RES_INIT_CHAIN: Final = 6  # types.proto:205 Response.init_chain = 6
_RES_PREPARE_PROPOSAL: Final = 17  # types.proto:213 Response.prepare_proposal = 17
_RES_PROCESS_PROPOSAL: Final = 18  # types.proto:214 Response.process_proposal = 18
_RES_EXTEND_VOTE: Final = 19  # types.proto:215 Response.extend_vote = 19
_RES_VERIFY_VOTE_EXTENSION: Final = 20  # types.proto:216 verify_vote_extension = 20
_RES_COMMIT: Final = 12  # types.proto:208 Response.commit = 12
_RES_FINALIZE_BLOCK: Final = 21  # types.proto:217 Response.finalize_block = 21

# --- Inner-message field numbers -------------------------------------------
TIMESTAMP_SECONDS_FIELD: Final = 1  # google.protobuf.Timestamp.seconds = 1
_TIMESTAMP_NANOS_FIELD: Final = 2  # google.protobuf.Timestamp.nanos = 2

PROPOSAL_UNKNOWN: Final = 0  # types.proto ProposalStatus.UNKNOWN = 0
PROPOSAL_ACCEPT: Final = 1  # types.proto ProposalStatus.ACCEPT = 1
PROPOSAL_REJECT: Final = 2  # types.proto ProposalStatus.REJECT = 2
PREPARE_ACCEPT: Final = PROPOSAL_ACCEPT
PREPARE_REJECT: Final = PROPOSAL_REJECT
PREPARE_UNKNOWN: Final = PROPOSAL_UNKNOWN

VERIFY_UNKNOWN: Final = 0  # types.proto VerifyStatus.UNKNOWN = 0
VERIFY_ACCEPT: Final = 1  # types.proto VerifyStatus.ACCEPT = 1
VERIFY_REJECT: Final = 2  # types.proto VerifyStatus.REJECT = 2


@dataclass(frozen=True, slots=True)
class Timestamp:
    """The subset of ``google.protobuf.Timestamp`` the codec consumes."""

    seconds: int = 0
    nanos: int = 0


@dataclass(frozen=True, slots=True)
class CommitInfo:
    """``CommitInfo`` (types.proto:371-374); nested VoteInfo detail is skipped."""

    round: int = 0


@dataclass(frozen=True, slots=True)
class Header:
    """Agency header carried in ``FinalizeBlock`` (chain_id, height, time)."""

    chain_id: str = ""
    height: int = 0
    time: Timestamp | None = None


@dataclass(frozen=True, slots=True)
class Echo:
    message: str = ""


@dataclass(frozen=True, slots=True)
class Flush:
    pass


@dataclass(frozen=True, slots=True)
class Info:
    version: str = ""  # types.proto:71 RequestInfo.version = 1
    block_version: int = 0  # types.proto:72 RequestInfo.block_version = 2
    p2p_version: int = 0  # types.proto:73 RequestInfo.p2p_version = 3
    abci_version: str = ""  # types.proto:74 RequestInfo.abci_version = 4


@dataclass(frozen=True, slots=True)
class InitChain:
    time: Timestamp | None = None  # types.proto:79 RequestInitChain.time = 1
    chain_id: str = ""  # types.proto:80 RequestInitChain.chain_id = 2
    initial_height: int = 0  # types.proto:84 RequestInitChain.initial_height = 6
    app_state_bytes: bytes = b""  # types.proto:83 RequestInitChain.app_state_bytes = 5
    validators: bytes = b""  # types.proto:82 RequestInitChain.validators = 4 (opaque)


@dataclass(frozen=True, slots=True)
class PrepareProposal:
    txs: tuple[bytes, ...] = ()  # types.proto:134 txs = 2
    max_tx_bytes: int = 0  # types.proto:131 max_tx_bytes = 1
    height: int = 0  # types.proto:137 height = 5
    time: Timestamp | None = None  # types.proto:138 time = 6


@dataclass(frozen=True, slots=True)
class ProcessProposal:
    txs: tuple[bytes, ...] = ()  # types.proto:145 txs = 1
    hash: bytes = b""  # types.proto:149 hash = 4
    height: int = 0  # types.proto:150 height = 5
    time: Timestamp | None = None  # types.proto:151 time = 6


@dataclass(frozen=True, slots=True)
class ExtendVote:
    hash: bytes = b""  # types.proto:160 hash = 1
    height: int = 0  # types.proto:162 height = 2


@dataclass(frozen=True, slots=True)
class VerifyVoteExtension:
    hash: bytes = b""  # types.proto:176 hash = 1
    validator_address: bytes = b""  # types.proto:178 validator_address = 2
    height: int = 0  # types.proto:179 height = 3
    vote_extension: bytes = b""  # types.proto:180 vote_extension = 4


@dataclass(frozen=True, slots=True)
class FinalizeBlock:
    txs: tuple[bytes, ...] = ()  # types.proto:184 txs = 1
    hash: bytes = b""  # types.proto:188 hash = 4
    height: int = 0  # types.proto:189 height = 5
    time: Timestamp | None = None  # types.proto:190 time = 6
    header: Header | None = None  # Agency header carried in FinalizeBlock
    misbehavior: tuple[bytes, ...] = ()  # types.proto:186 = 3 (opaque)


@dataclass(frozen=True, slots=True)
class RequestEnvelope:
    """Decoded request: ``kind`` plus the one active typed message."""

    kind: str
    echo: Echo | None = None
    flush: Flush | None = None
    info: Info | None = None
    init_chain: InitChain | None = None
    prepare_proposal: PrepareProposal | None = None
    process_proposal: ProcessProposal | None = None
    extend_vote: ExtendVote | None = None
    verify_vote_extension: VerifyVoteExtension | None = None
    finalize_block: FinalizeBlock | None = None
    payload: bytes = b""


@dataclass(frozen=True, slots=True)
class ResponseEcho:
    message: str = ""  # types.proto:228 ResponseEcho.message = 1


@dataclass(frozen=True, slots=True)
class ResponseFlush:
    pass


@dataclass(frozen=True, slots=True)
class ResponseInfo:
    data: str = ""  # types.proto:234 ResponseInfo.data = 1
    app_version: int = 0  # types.proto:237 ResponseInfo.app_version = 3
    last_block_height: int = 0  # types.proto:239 ResponseInfo.last_block_height = 4
    last_block_app_hash: bytes = b""  # types.proto:240 last_block_app_hash = 5


@dataclass(frozen=True, slots=True)
class ResponseInitChain:
    validators: bytes = b""  # types.proto:245 validators = 2 (opaque)
    app_hash: bytes = b""  # types.proto:246 app_hash = 3


@dataclass(frozen=True, slots=True)
class ResponsePrepareProposal:
    txs: tuple[bytes, ...] = ()  # types.proto:321 txs = 1


@dataclass(frozen=True, slots=True)
class ResponseProcessProposal:
    status: int = PROPOSAL_ACCEPT  # types.proto:325 status = 1 (ProposalStatus)


@dataclass(frozen=True, slots=True)
class ResponseExtendVote:
    vote_extension: bytes = b""  # types.proto:335 vote_extension = 1


@dataclass(frozen=True, slots=True)
class ResponseVerifyVoteExtension:
    status: int = VERIFY_ACCEPT  # types.proto:339 status = 1 (VerifyStatus)


@dataclass(frozen=True, slots=True)
class ExecTxResult:
    code: int = 0  # types.proto:409 code = 1
    log: str = ""  # types.proto:411 log = 3
    info: str = ""  # types.proto:412 info = 4
    gas_wanted: int = 0  # types.proto:413 gas_wanted = 5
    gas_used: int = 0  # types.proto:414 gas_used = 6


@dataclass(frozen=True, slots=True)
class ResponseFinalizeBlock:
    events: tuple[bytes, ...] = ()  # types.proto:354 events = 1 (we emit none)
    tx_results: tuple[ExecTxResult, ...] = ()  # types.proto:359 tx_results = 2
    validator_updates: bytes = b""  # types.proto:361 validator_updates = 3 (opaque)
    consensus_param_updates: bytes = b""  # types.proto:363 consensus_param_updates = 4 (opaque)
    app_hash: bytes = b""  # types.proto:365 app_hash = 5


@dataclass(frozen=True, slots=True)
class ResponseCommit:
    # types.proto:279-282: fields 1 and 2 are RESERVED (data moved to FinalizeBlock).
    retain_height: int = 0  # types.proto:281 retain_height = 3


@dataclass(frozen=True, slots=True)
class ResponseException:
    error: str = ""  # types.proto:224 ResponseException.error = 1


ResponseEnvelope: TypeAlias = (
    ResponseEcho
    | ResponseFlush
    | ResponseInfo
    | ResponseInitChain
    | ResponsePrepareProposal
    | ResponseProcessProposal
    | ResponseExtendVote
    | ResponseVerifyVoteExtension
    | ResponseFinalizeBlock
    | ResponseCommit
    | ResponseException
)


def _as_int64(value: int) -> int:
    unsigned = value & _UINT64_MASK
    return unsigned - (1 << 64) if unsigned >= (1 << 63) else unsigned


def _text(payload: bytes) -> str:
    return payload.decode("utf-8", errors="replace")


def split_frame(data: bytes) -> tuple[int, bytes]:
    """Split one top-level length-delimited frame into ``(field_number, payload)``."""
    fields = list(iter_fields(data))
    if len(fields) != 1:
        raise WireError("expected exactly one top-level frame")
    field_number, wire_type, value = fields[0]
    if wire_type != WIRE_LENGTH_DELIMITED or not isinstance(value, bytes):
        raise WireError("top-level frame must be length-delimited")
    return field_number, value


def _decode_timestamp(payload: bytes) -> Timestamp:
    seconds = 0
    nanos = 0
    for field_number, wire_type, value in iter_fields(payload):
        if not isinstance(value, int) or wire_type != WIRE_VARINT:
            continue
        if field_number == TIMESTAMP_SECONDS_FIELD:
            seconds = _as_int64(value)
        elif field_number == _TIMESTAMP_NANOS_FIELD:
            nanos = _as_int64(value)
    return Timestamp(seconds=seconds, nanos=nanos)


def _decode_commit_info(payload: bytes) -> CommitInfo:
    round_number = 0
    for field_number, wire_type, value in iter_fields(payload):
        if (
            field_number == 1  # types.proto:372 round = 1
            and wire_type == WIRE_VARINT
            and isinstance(value, int)
        ):
            round_number = _as_int64(value)
    return CommitInfo(round=round_number)


def _decode_header(payload: bytes) -> Header:
    chain_id = ""
    height = 0
    time: Timestamp | None = None
    for field_number, wire_type, value in iter_fields(payload):
        if wire_type == WIRE_LENGTH_DELIMITED and isinstance(value, bytes):
            if field_number == 2:  # chain_id
                chain_id = _text(value)
            elif field_number == 4:  # time
                time = _decode_timestamp(value)
        elif field_number == 3 and wire_type == WIRE_VARINT and isinstance(value, int):
            height = _as_int64(value)  # height
    return Header(chain_id, height, time)


def _decode_info(payload: bytes) -> Info:
    version = ""
    abci_version = ""
    block_version = 0
    p2p_version = 0
    for field_number, wire_type, value in iter_fields(payload):
        if wire_type == WIRE_LENGTH_DELIMITED and isinstance(value, bytes):
            if field_number == 1:  # version = 1
                version = _text(value)
            elif field_number == 4:  # abci_version = 4
                abci_version = _text(value)
        elif wire_type == WIRE_VARINT and isinstance(value, int):
            if field_number == 2:  # block_version = 2
                block_version = value
            elif field_number == 3:  # p2p_version = 3
                p2p_version = value
    return Info(version, block_version, p2p_version, abci_version)


def _decode_init_chain(payload: bytes) -> InitChain:
    time: Timestamp | None = None
    chain_id = ""
    initial_height = 0
    app_state_bytes = b""
    validators = b""
    for field_number, wire_type, value in iter_fields(payload):
        if wire_type == WIRE_LENGTH_DELIMITED and isinstance(value, bytes):
            if field_number == 1:  # types.proto:79 time = 1
                time = _decode_timestamp(value)
            elif field_number == 2:  # types.proto:80 chain_id = 2
                chain_id = _text(value)
            elif field_number == 4:  # types.proto:82 validators = 4 (opaque)
                validators = value
            elif field_number == 5:  # types.proto:83 app_state_bytes = 5
                app_state_bytes = value
        elif field_number == 6 and wire_type == WIRE_VARINT and isinstance(value, int):
            initial_height = _as_int64(value)  # types.proto:84 initial_height = 6
    return InitChain(time, chain_id, initial_height, app_state_bytes, validators)


def _decode_repeated_bytes(payload: bytes, target_field: int) -> tuple[bytes, ...]:
    result: list[bytes] = []
    for field_number, wire_type, value in iter_fields(payload):
        if (
            field_number == target_field
            and wire_type == WIRE_LENGTH_DELIMITED
            and isinstance(value, bytes)
        ):
            result.append(value)
    return tuple(result)


def _decode_extend_vote(payload: bytes) -> ExtendVote:
    hash_bytes = b""
    height = 0
    for field_number, wire_type, value in iter_fields(payload):
        if field_number == 1 and wire_type == WIRE_LENGTH_DELIMITED and isinstance(value, bytes):
            hash_bytes = value  # types.proto:160 hash = 1
        elif field_number == 2 and wire_type == WIRE_VARINT and isinstance(value, int):
            height = _as_int64(value)  # types.proto:162 height = 2
    return ExtendVote(hash_bytes, height)


def _decode_verify_vote_extension(payload: bytes) -> VerifyVoteExtension:
    hash_bytes = b""
    validator_address = b""
    height = 0
    vote_extension = b""
    for field_number, wire_type, value in iter_fields(payload):
        if wire_type == WIRE_LENGTH_DELIMITED and isinstance(value, bytes):
            if field_number == 1:  # types.proto:176 hash = 1
                hash_bytes = value
            elif field_number == 2:  # types.proto:178 validator_address = 2
                validator_address = value
            elif field_number == 4:  # types.proto:180 vote_extension = 4
                vote_extension = value
        elif field_number == 3 and wire_type == WIRE_VARINT and isinstance(value, int):
            height = _as_int64(value)  # types.proto:179 height = 3
    return VerifyVoteExtension(hash_bytes, validator_address, height, vote_extension)


def decode_finalize_request(payload: bytes) -> FinalizeBlock:
    """Decode a ``RequestFinalizeBlock`` body (types.proto:183-194)."""
    txs: list[bytes] = []
    hash_bytes = b""
    height = 0
    time: Timestamp | None = None
    header: Header | None = None
    misbehavior: list[bytes] = []
    for field_number, wire_type, value in iter_fields(payload):
        if wire_type == WIRE_LENGTH_DELIMITED and isinstance(value, bytes):
            if field_number == 1:  # types.proto:184 txs = 1
                txs.append(value)
            elif field_number == 2:  # header
                header = _decode_header(value)
            elif field_number == 3:  # types.proto:186 misbehavior = 3 (opaque)
                misbehavior.append(value)
            elif field_number == 4:  # types.proto:188 hash = 4
                hash_bytes = value
            elif field_number == 6:  # types.proto:190 time = 6
                time = _decode_timestamp(value)
        elif field_number == 5 and wire_type == WIRE_VARINT and isinstance(value, int):
            height = _as_int64(value)  # types.proto:189 height = 5
    return FinalizeBlock(tuple(txs), hash_bytes, height, time, header, tuple(misbehavior))


def decode_request(data: bytes) -> RequestEnvelope:
    """Decode one ``Request`` frame; unknown oneof members yield ``unknown``."""
    try:
        field_number, payload = split_frame(data)
    except WireError:
        return RequestEnvelope(kind="unknown", payload=data)
    if field_number == _REQ_ECHO:
        return RequestEnvelope(kind="echo", echo=Echo(message=_text(payload)))
    if field_number == _REQ_FLUSH:
        return RequestEnvelope(kind="flush", flush=Flush())
    if field_number == _REQ_INFO:
        return RequestEnvelope(kind="info", info=_decode_info(payload))
    if field_number == _REQ_INIT_CHAIN:
        return RequestEnvelope(kind="init_chain", init_chain=_decode_init_chain(payload))
    if field_number == _REQ_PREPARE_PROPOSAL:
        proposal = PrepareProposal(txs=_decode_repeated_bytes(payload, 2))
        for inner_field, inner_type, value in iter_fields(payload):
            if inner_field == 1 and inner_type == WIRE_VARINT and isinstance(value, int):
                proposal = PrepareProposal(proposal.txs, _as_int64(value))
            elif inner_field == 5 and inner_type == WIRE_VARINT and isinstance(value, int):
                proposal = PrepareProposal(
                    proposal.txs, proposal.max_tx_bytes, height=_as_int64(value)
                )
            elif (
                inner_field == 6
                and inner_type == WIRE_LENGTH_DELIMITED
                and isinstance(value, bytes)
            ):
                proposal = PrepareProposal(
                    proposal.txs, proposal.max_tx_bytes, time=_decode_timestamp(value)
                )
        return RequestEnvelope(kind="prepare_proposal", prepare_proposal=proposal)
    if field_number == _REQ_PROCESS_PROPOSAL:
        process = ProcessProposal(txs=_decode_repeated_bytes(payload, 1))
        for inner_field, inner_type, value in iter_fields(payload):
            if (
                inner_field == 4
                and inner_type == WIRE_LENGTH_DELIMITED
                and isinstance(value, bytes)
            ):
                process = ProcessProposal(
                    process.txs, hash=value, height=process.height, time=process.time
                )
            elif inner_field == 5 and inner_type == WIRE_VARINT and isinstance(value, int):
                process = ProcessProposal(
                    process.txs, hash=process.hash, height=_as_int64(value), time=process.time
                )
            elif (
                inner_field == 6
                and inner_type == WIRE_LENGTH_DELIMITED
                and isinstance(value, bytes)
            ):
                process = ProcessProposal(
                    process.txs,
                    hash=process.hash,
                    height=process.height,
                    time=_decode_timestamp(value),
                )
        return RequestEnvelope(kind="process_proposal", process_proposal=process)
    if field_number == _REQ_EXTEND_VOTE:
        return RequestEnvelope(kind="extend_vote", extend_vote=_decode_extend_vote(payload))
    if field_number == _REQ_VERIFY_VOTE_EXTENSION:
        return RequestEnvelope(
            kind="verify_vote_extension",
            verify_vote_extension=_decode_verify_vote_extension(payload),
        )
    if field_number == _REQ_FINALIZE_BLOCK:
        return RequestEnvelope(
            kind="finalize_block", finalize_block=decode_finalize_request(payload)
        )
    return RequestEnvelope(kind="unknown", payload=payload)


def _encode_exec_tx_result(result: ExecTxResult) -> bytes:
    out = bytearray()
    if result.code:
        out += encode_uint64_field(1, result.code)
    if result.log:
        out += encode_string_field(3, result.log)
    if result.info:
        out += encode_string_field(4, result.info)
    if result.gas_wanted:
        out += encode_uint64_field(5, result.gas_wanted)
    if result.gas_used:
        out += encode_uint64_field(6, result.gas_used)
    return bytes(out)


def _encode_finalize_payload(response: ResponseFinalizeBlock) -> bytes:
    out = bytearray()
    for event in response.events:
        out += encode_message_field(1, event)
    for result in response.tx_results:
        out += encode_message_field(2, _encode_exec_tx_result(result))
    if response.validator_updates:
        out += encode_message_field(3, response.validator_updates)
    if response.consensus_param_updates:
        out += encode_message_field(4, response.consensus_param_updates)
    if response.app_hash:
        out += encode_bytes_field(5, response.app_hash)
    return bytes(out)


def encode_response(response: ResponseEnvelope) -> bytes:
    """Encode one response into its canonical oneof frame (field order canonical)."""
    if isinstance(response, ResponseEcho):
        return encode_message_field(_RES_ECHO, response.message.encode("utf-8"))
    if isinstance(response, ResponseFlush):
        return encode_message_field(_RES_FLUSH, b"")
    if isinstance(response, ResponseInfo):
        out = bytearray()
        if response.data:
            out += encode_string_field(1, response.data)
        if response.app_version:
            out += encode_uint64_field(3, response.app_version)
        if response.last_block_height:
            out += encode_uint64_field(4, response.last_block_height)
        if response.last_block_app_hash:
            out += encode_bytes_field(5, response.last_block_app_hash)
        return encode_message_field(_RES_INFO, bytes(out))
    if isinstance(response, ResponseInitChain):
        out = bytearray()
        if response.validators:
            out += encode_message_field(2, response.validators)
        if response.app_hash:
            out += encode_bytes_field(3, response.app_hash)
        return encode_message_field(_RES_INIT_CHAIN, bytes(out))
    if isinstance(response, ResponsePrepareProposal):
        out = bytearray()
        for tx in response.txs:
            out += encode_bytes_field(1, tx)
        return encode_message_field(_RES_PREPARE_PROPOSAL, bytes(out))
    if isinstance(response, ResponseProcessProposal):
        return encode_message_field(_RES_PROCESS_PROPOSAL, encode_uint64_field(1, response.status))
    if isinstance(response, ResponseExtendVote):
        body = encode_bytes_field(1, response.vote_extension) if response.vote_extension else b""
        return encode_message_field(_RES_EXTEND_VOTE, body)
    if isinstance(response, ResponseVerifyVoteExtension):
        return encode_message_field(
            _RES_VERIFY_VOTE_EXTENSION, encode_uint64_field(1, response.status)
        )
    if isinstance(response, ResponseFinalizeBlock):
        return encode_message_field(_RES_FINALIZE_BLOCK, _encode_finalize_payload(response))
    if isinstance(response, ResponseCommit):
        out = bytearray()
        if response.retain_height:
            out += encode_uint64_field(3, response.retain_height)
        return encode_message_field(_RES_COMMIT, bytes(out))
    if isinstance(response, ResponseException):
        body = encode_string_field(1, response.error) if response.error else b""
        return encode_message_field(_RES_EXCEPTION, body)
    raise TypeError(f"unsupported response type: {type(response).__name__}")


def _decode_exec_tx_result(payload: bytes) -> ExecTxResult:
    code = 0
    log = ""
    info = ""
    gas_wanted = 0
    gas_used = 0
    for field_number, wire_type, value in iter_fields(payload):
        if wire_type == WIRE_VARINT and isinstance(value, int):
            if field_number == 1:
                code = value
            elif field_number == 5:
                gas_wanted = value
            elif field_number == 6:
                gas_used = value
        elif wire_type == WIRE_LENGTH_DELIMITED and isinstance(value, bytes):
            if field_number == 3:
                log = _text(value)
            elif field_number == 4:
                info = _text(value)
    return ExecTxResult(code, log, info, gas_wanted, gas_used)


def decode_finalize_payload(payload: bytes) -> ResponseFinalizeBlock:
    """Decode a ``ResponseFinalizeBlock`` body (used for wire checks)."""
    events: list[bytes] = []
    tx_results: list[ExecTxResult] = []
    validator_updates = b""
    consensus_param_updates = b""
    app_hash = b""
    for field_number, wire_type, value in iter_fields(payload):
        if wire_type != WIRE_LENGTH_DELIMITED or not isinstance(value, bytes):
            continue
        if field_number == 1:
            events.append(value)
        elif field_number == 2:
            tx_results.append(_decode_exec_tx_result(value))
        elif field_number == 3:
            validator_updates = value
        elif field_number == 4:
            consensus_param_updates = value
        elif field_number == 5:
            app_hash = value
    return ResponseFinalizeBlock(
        tuple(events),
        tuple(tx_results),
        validator_updates,
        consensus_param_updates,
        app_hash,
    )
