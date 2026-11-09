"""Canonical serialization and closed-schema validation for the peer protocol.

This module owns the byte-level contract that every peer implementation must
agree on: canonical JSON, digests, and *closed* schema validation. It contains
no peer identity, no authority, and no admission decision. A value that
validates here is structurally well-formed only; it is not trusted.

Re-exports :func:`agency.peer_envelope.canonical_json` unchanged rather than
copying it, so the digest input stays one implementation across the repo.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Mapping
from typing import Any, Final, cast

from agency.peer_envelope import (
    MAX_DEPTH,
    EnvelopeError,
    canonical_json,
)

__all__ = [
    "CONSUMER_ID_RE",
    "HEX64_RE",
    "MAX_DEPTH",
    "MAX_I64",
    "MAX_MEMBERS",
    "MAX_U64",
    "MIN_I64",
    "PROPOSAL_ID_RE",
    "DigestError",
    "SchemaError",
    "canonical_json",
    "digest_bytes",
    "digest_hex",
    "digest_json",
    "validate_closed",
    "validate_consumer_id",
    "validate_digest",
    "validate_digest_list",
    "validate_enum",
    "validate_integer",
    "validate_list",
    "validate_mapping",
    "validate_optional_digest",
    "validate_optional_integer",
    "validate_peer_id",
    "validate_proposal_id_format",
    "validate_string",
    "validate_timestamp",
    "validate_u64",
]

# Approved policy input (D-01): four validator peers, one Byzantine fault.
# This bound is a *type-level* constraint on membership sets, not a quorum
# computation and not an authority claim.
MAX_MEMBERS: Final = 4

MIN_I64: Final = -(1 << 63)
MAX_I64: Final = (1 << 63) - 1
MAX_U64: Final = (1 << 64) - 1

HEX64_RE: Final = re.compile(r"[0-9a-f]{64}\Z")
PEER_ID_RE: Final = re.compile(r"peer_[0-9a-f]{32}\Z")
CONSUMER_ID_RE: Final = re.compile(r"consumer_[0-9a-f]{32}\Z")
PROPOSAL_ID_RE: Final = re.compile(r"prop_[0-9a-f]{32}\Z")


class SchemaError(EnvelopeError):
    """A value violates a closed peer-protocol schema."""


class DigestError(EnvelopeError):
    """A digest does not have the required shape."""


def validate_digest(value: Any, field: str = "digest") -> str:
    """Require a lowercase 32-byte hex digest."""

    if type(value) is not str or HEX64_RE.fullmatch(value) is None:
        raise SchemaError(f"invalid {field}")
    return value


def validate_optional_digest(value: Any, field: str = "digest") -> str | None:
    if value is None:
        return None
    return validate_digest(value, field)


def validate_peer_id(value: Any, field: str = "peer_id") -> str:
    if type(value) is not str or PEER_ID_RE.fullmatch(value) is None:
        raise SchemaError(f"invalid {field}")
    return value


def validate_consumer_id(value: Any, field: str = "consumer_id") -> str:
    if type(value) is not str or CONSUMER_ID_RE.fullmatch(value) is None:
        raise SchemaError(f"invalid {field}")
    return value


def validate_proposal_id_format(value: Any, field: str = "proposal_id") -> str:
    """A ``proposal_id`` is ``prop_`` plus 32 hex characters (§5.3)."""

    if type(value) is not str or PROPOSAL_ID_RE.fullmatch(value) is None:
        raise SchemaError(f"invalid {field}")
    return value


def validate_u64(value: Any, field: str = "u64") -> int:
    if type(value) is not int or not 0 <= value <= MAX_U64:
        raise SchemaError(f"invalid {field}")
    return value


def validate_integer(value: Any, minimum: int, maximum: int, field: str) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise SchemaError(f"invalid {field}")
    return value


def validate_optional_integer(value: Any, minimum: int, maximum: int, field: str) -> int | None:
    if value is None:
        return None
    return validate_integer(value, minimum, maximum, field)


def validate_string(value: Any, field: str, *, minimum: int = 1, maximum: int = 4096) -> str:
    if type(value) is not str:
        raise SchemaError(f"invalid {field}")
    length = len(value)
    if not minimum <= length <= maximum:
        raise SchemaError(f"invalid {field} length")
    if any(ord(char) < 0x20 for char in value):
        raise SchemaError(f"{field} contains a control character")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:  # pragma: no cover - defensive
        raise SchemaError(f"invalid {field} encoding") from exc
    if value != unicodedata.normalize("NFC", value):
        raise SchemaError(f"{field} is not NFC-normalized")
    return value


def validate_enum(value: Any, allowed: frozenset[str] | tuple[str, ...], field: str) -> str:
    if type(value) is not str or value not in allowed:
        raise SchemaError(f"invalid {field}")
    return value


def validate_timestamp(value: Any, field: str = "timestamp") -> int:
    return validate_integer(value, MIN_I64, MAX_I64, field)


def validate_list(value: Any, field: str, *, maximum: int) -> list[Any]:
    if type(value) is not list:
        raise SchemaError(f"invalid {field}")
    if len(value) > maximum:
        raise SchemaError(f"{field} exceeds its bound")
    return value


def validate_mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SchemaError(f"invalid {field}")
    for key in value:
        if type(key) is not str:
            raise SchemaError(f"non-string {field} key")
    return value


def validate_closed(value: Any, keys: frozenset[str] | set[str], field: str) -> dict[str, Any]:
    """Reject unknown, duplicate, or noncanonical object fields.

    ``canonical_json`` already rejects duplicate *and* canonically colliding
    keys on encode, and re-encoding a parsed value is checked against the
    original wire bytes, so an object that reaches this function with the wrong
    key set is malformed rather than merely surprising.
    """

    mapping = validate_mapping(value, field)
    if set(mapping.keys()) != set(keys):
        raise SchemaError(f"invalid {field} fields")
    return cast(dict[str, Any], dict(mapping))


def digest_bytes(payload: bytes) -> bytes:
    """Raw SHA-256 digest bytes."""

    if type(payload) is not bytes:
        raise DigestError("digest input must be bytes")
    return hashlib.sha256(payload).digest()


def digest_hex(payload: bytes) -> str:
    """Lowercase hex SHA-256 of ``payload``."""

    return digest_bytes(payload).hex()


def digest_json(value: Any) -> str:
    """Lowercase hex SHA-256 of the canonical JSON encoding of ``value``."""

    return digest_hex(canonical_json(value))


def validate_digest_list(value: Any, field: str, *, maximum: int) -> list[str]:
    """Validate a bounded list of distinct digests."""

    items = validate_list(value, field, maximum=maximum)
    seen: set[str] = set()
    for index, item in enumerate(items):
        digest = validate_digest(item, f"{field}[{index}]")
        if digest in seen:
            raise SchemaError(f"duplicate {field} digest")
        seen.add(digest)
    return items
