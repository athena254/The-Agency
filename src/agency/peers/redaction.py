"""Redaction helpers for peer traffic, evidence, and the durable log.

The protocol persists digests, signatures, and protocol metadata. It must never
persist raw prompts, message content, search terms, raw model output,
credentials, or PII (§9, §16). These helpers are the boundary that keeps that
promise: they are conservative — when a key or value looks sensitive, it is
replaced, never passed through on a judgement call.

Redaction is *not* authorization and not evidence of anything. A redacted
finding is citable by digest; the original stays out of the log entirely.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Final

from agency.peers.canonical import digest_hex, validate_mapping

__all__ = [
    "REDACTED",
    "SECRET_PATTERNS",
    "SENSITIVE_KEY_SUFFIXES",
    "contains_secret",
    "finding_digest",
    "is_sensitive_key",
    "redact_mapping",
    "redact_structure",
    "redact_text",
    "redacted_finding",
    "sanitize_log_payload",
]

REDACTED: Final = "[REDACTED]"

# Key fragments that mark a value as content rather than protocol metadata.
# Matching is case-insensitive on the normalized key.
SENSITIVE_KEY_SUFFIXES: Final = (
    "prompt",
    "completion",
    "content",
    "message",
    "messages",
    "query",
    "search",
    "token",
    "secret",
    "credential",
    "credentials",
    "password",
    "passwd",
    "apikey",
    "api_key",
    "accesskey",
    "access_key",
    "privatekey",
    "private_key",
    "authorization",
    "auth",
    "cookie",
    "session",
    "email",
    "phone",
    "pii",
    "body",
    "text",
    "input",
    "output",
    "reasoning",
    "transcript",
)

# Value patterns for credential-shaped strings. Deliberately specific: a
# generic "long random string" rule would redact the SHA-256 digests the
# protocol is required to retain.
SECRET_PATTERNS: Final = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
        r"eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}",
        r"Bearer\s+[A-Za-z0-9._~+/=-]{8,}",
        r"\bAKIA[0-9A-Z]{16}\b",
        r"\b(?:sk|pk|api|key|tok|secret)[_-][A-Za-z0-9]{16,}\b",
        r"\b\d{8,}:[A-Za-z0-9_-]{30,}\b",
        r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
        r"\+\d{1,3}[\s.-]?\d{3,4}[\s.-]?\d{3,4}[\s.-]?\d{3,4}",
    )
)


def is_sensitive_key(key: str) -> bool:
    """Whether a mapping key marks its value as content to redact."""

    if type(key) is not str:
        return True
    normalized = re.sub(r"[^a-z0-9]", "", key.lower())
    if not normalized:
        return False
    return any(fragment in normalized for fragment in SENSITIVE_KEY_SUFFIXES)


def contains_secret(value: str) -> bool:
    """Whether a string contains a credential- or contact-shaped pattern."""

    if type(value) is not str:
        return False
    return any(pattern.search(value) is not None for pattern in SECRET_PATTERNS)


def redact_text(value: str, *, replacement: str = REDACTED) -> str:
    """Replace credential/contact patterns in a string, conservatively."""

    if type(value) is not str:
        raise TypeError("redact_text expects a string")
    if type(replacement) is not str or not replacement:
        raise ValueError("replacement must be a non-empty string")
    redacted = value
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def redact_structure(value: Any, *, replacement: str = REDACTED) -> Any:
    """Recursively redact a JSON-like structure.

    Mapping keys that look sensitive have their value replaced; other values
    are recursed. Strings are pattern-redacted. Scalars pass through.
    """

    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if is_sensitive_key(key):
                result[key] = replacement
            else:
                result[key] = redact_structure(item, replacement=replacement)
        return result
    if type(value) is list:
        return [redact_structure(item, replacement=replacement) for item in value]
    if type(value) is tuple:
        return [redact_structure(item, replacement=replacement) for item in value]
    if type(value) is str:
        return redact_text(value, replacement=replacement)
    if value is None or type(value) in (bool, int):
        return value
    # Unknown types are redacted rather than serialized: they are not part of
    # the protocol's canonical data model.
    return replacement


def redact_mapping(value: Mapping[str, Any], *, replacement: str = REDACTED) -> dict[str, Any]:
    """Redact a mapping, preserving its keys and shape."""

    validate_mapping(value, "value")
    return dict(redact_structure(dict(value), replacement=replacement))


def sanitize_log_payload(payload: Any, *, replacement: str = REDACTED) -> Any:
    """Redact a payload before it is written to the durable log.

    Digests, peer IDs, epochs, and roots survive; content-shaped values do not.
    """

    return redact_structure(payload, replacement=replacement)


def finding_digest(redacted_text: str) -> str:
    """Digest of a redacted finding, citable by a ballot (§9)."""

    if type(redacted_text) is not str:
        raise TypeError("finding_digest expects a string")
    return digest_hex(redacted_text.encode("utf-8", errors="strict"))


def redacted_finding(raw_text: str, *, replacement: str = REDACTED) -> tuple[str, str]:
    """Redact model-derived text and return ``(redacted_text, digest)``.

    The caller cites the digest; the redacted text is what may be retained as
    evidence. The raw text is never returned.
    """

    if type(raw_text) is not str:
        raise TypeError("raw_text must be a string")
    redacted = redact_text(raw_text, replacement=replacement)
    return redacted, finding_digest(redacted)
