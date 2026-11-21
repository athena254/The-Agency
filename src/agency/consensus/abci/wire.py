"""Minimal protobuf wire-format primitives (stdlib only, no protobuf package).

Implements the subset of the Protocol Buffers binary encoding needed by the
Consensus Connection ABCI codec: base-128 varints, field tags and
length-delimited fields. Readers are bounds-checked and fail closed with
:class:`WireError`.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Final

__all__ = [
    "WIRE_32BIT",
    "WIRE_64BIT",
    "WIRE_LENGTH_DELIMITED",
    "WIRE_VARINT",
    "WireError",
    "encode_bool_field",
    "encode_bytes_field",
    "encode_message_field",
    "encode_string_field",
    "encode_tag",
    "encode_uint64_field",
    "encode_varint",
    "iter_fields",
    "read_varint",
]

WIRE_VARINT: Final = 0
WIRE_64BIT: Final = 1
WIRE_LENGTH_DELIMITED: Final = 2
WIRE_32BIT: Final = 5

_UINT64_MASK: Final = (1 << 64) - 1
_MAX_VARINT_BYTES: Final = 10


class WireError(ValueError):
    """Raised when a buffer is not well-formed protobuf wire data."""


def encode_varint(value: int) -> bytes:
    """Encode a uint64 (or a two's-complement int64) as a base-128 varint."""
    if value < -(1 << 63) or value > _UINT64_MASK:
        raise WireError("varint value outside the 64-bit range")
    remaining = value & _UINT64_MASK
    out = bytearray()
    while True:
        byte = remaining & 0x7F
        remaining >>= 7
        if remaining:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def read_varint(data: bytes, offset: int) -> tuple[int, int]:
    """Read one varint at ``offset``; return ``(value, new_offset)``."""
    if offset < 0 or offset > len(data):
        raise WireError("varint offset out of bounds")
    result = 0
    shift = 0
    pos = offset
    while True:
        if pos >= len(data):
            raise WireError("truncated varint")
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result & _UINT64_MASK, pos
        shift += 7
        if shift >= 7 * _MAX_VARINT_BYTES:
            raise WireError("varint exceeds 10 bytes")


def encode_tag(field_number: int, wire_type: int) -> bytes:
    """Encode a field key (``field_number << 3 | wire_type``) as a varint."""
    if field_number < 1 or field_number > (1 << 29) - 1:
        raise WireError("invalid field number")
    if wire_type not in (WIRE_VARINT, WIRE_64BIT, WIRE_LENGTH_DELIMITED, WIRE_32BIT):
        raise WireError("invalid wire type")
    return encode_varint((field_number << 3) | wire_type)


def encode_bytes_field(field_number: int, value: bytes) -> bytes:
    """Encode a length-delimited bytes field."""
    return encode_tag(field_number, WIRE_LENGTH_DELIMITED) + encode_varint(len(value)) + value


def encode_string_field(field_number: int, value: str) -> bytes:
    """Encode a length-delimited UTF-8 string field."""
    return encode_bytes_field(field_number, value.encode("utf-8"))


def encode_uint64_field(field_number: int, value: int) -> bytes:
    """Encode a varint field (uint64, or two's-complement int64)."""
    return encode_tag(field_number, WIRE_VARINT) + encode_varint(value)


def encode_bool_field(field_number: int, value: bool) -> bytes:
    """Encode a boolean field as a varint (0 or 1)."""
    return encode_uint64_field(field_number, 1 if value else 0)


def encode_message_field(field_number: int, payload: bytes) -> bytes:
    """Encode a nested message body as a length-delimited field."""
    return encode_bytes_field(field_number, payload)


def iter_fields(data: bytes) -> Iterator[tuple[int, int, int | bytes]]:
    """Yield ``(field_number, wire_type, value)`` for every top-level field.

    ``value`` is an ``int`` for wire types 0/1/5 and ``bytes`` for wire type 2.
    Nested payloads are returned raw; callers re-parse them as needed. Unknown
    field numbers are yielded like any other field so callers can skip them.
    Never reads past ``len(data)``; malformed input raises :class:`WireError`.
    """
    pos = 0
    size = len(data)
    while pos < size:
        tag, pos = read_varint(data, pos)
        field_number = tag >> 3
        wire_type = tag & 0x07
        if field_number == 0:
            raise WireError("field number zero is invalid")
        value: int | bytes
        if wire_type == WIRE_VARINT:
            value, pos = read_varint(data, pos)
        elif wire_type == WIRE_64BIT:
            if pos + 8 > size:
                raise WireError("truncated 64-bit field")
            value = int.from_bytes(data[pos : pos + 8], "little")
            pos += 8
        elif wire_type == WIRE_LENGTH_DELIMITED:
            length, pos = read_varint(data, pos)
            if length > size - pos:
                raise WireError("truncated length-delimited field")
            value = data[pos : pos + length]
            pos += length
        elif wire_type == WIRE_32BIT:
            if pos + 4 > size:
                raise WireError("truncated 32-bit field")
            value = int.from_bytes(data[pos : pos + 4], "little")
            pos += 4
        else:
            raise WireError(f"unsupported wire type {wire_type}")
        yield field_number, wire_type, value
