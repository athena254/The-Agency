"""Markdown + YAML frontmatter splitting and document-schema validation.

A neutral core primitive shared by the factory importer and the Butler
intelligence document loader (same pattern as ``agency.core.hashing``,
gaps.md #1). Both parse untrusted Markdown headers as inert data: nothing
here executes content, follows links, or treats metadata as authorization.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import yaml  # type: ignore[import-untyped]

MAX_FRONTMATTER_BYTES = 16 * 1024

_UNTRUSTED_KEY = "untrusted"


class FrontmatterError(ValueError):
    """Invalid, unsafe, or oversized YAML frontmatter."""


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split ``---``-delimited YAML frontmatter from a Markdown body.

    Documents without a leading frontmatter block return ``({}, text)``.
    An unterminated block, a header above 16 KiB, invalid YAML, or a
    non-string-keyed mapping raise :class:`FrontmatterError`.
    """
    if not text.startswith("---\n") and not text.startswith("---\r\n"):
        return {}, text
    lines = text.splitlines(keepends=True)
    closing = next((i for i in range(1, min(len(lines), 202)) if lines[i].strip() == "---"), None)
    if closing is None:
        raise FrontmatterError("unterminated YAML frontmatter")
    header = "".join(lines[1:closing])
    if len(header.encode("utf-8")) > MAX_FRONTMATTER_BYTES:
        raise FrontmatterError("frontmatter exceeds 16 KiB")
    try:
        raw = yaml.safe_load(header) or {}
    except yaml.YAMLError as exc:
        raise FrontmatterError(f"invalid YAML frontmatter: {exc}") from exc
    if not isinstance(raw, dict) or any(not isinstance(key, str) for key in raw):
        raise FrontmatterError("frontmatter must be a string-keyed mapping")
    body = "".join(lines[closing + 1 :])
    return raw, body


@dataclass(frozen=True)
class DocumentSchema:
    """Declarative shape for frontmatter-validated Markdown documents.

    ``required_fields`` must be present (an explicit null counts as missing);
    ``optional_enum_fields`` map a field to its allowed string values and are
    validated whenever present; ``schema_version`` must be an integer listed
    in ``known_versions`` (unknown versions fail closed); ``timestamp_fields``
    must be ISO-8601 strings carrying an explicit UTC-compatible offset.
    """

    required_fields: tuple[str, ...] = ()
    optional_enum_fields: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    known_versions: tuple[int, ...] = ()
    timestamp_fields: tuple[str, ...] = ()


def validate_document(raw: dict[str, Any], schema: DocumentSchema) -> dict[str, Any]:
    """Validate raw frontmatter against ``schema`` and return a sanitized copy.

    The returned dict keeps required and enum-checked fields at the top level,
    parses timestamp fields into aware UTC datetimes, and nests every unknown
    extra key under a dedicated ``"untrusted"`` key so consumers can never
    mistake owner-supplied metadata for framework material.
    """
    if not isinstance(raw, dict) or any(not isinstance(key, str) for key in raw):
        raise FrontmatterError("document frontmatter must be a string-keyed mapping")
    for key in schema.required_fields:
        if key not in raw or raw[key] is None:
            raise FrontmatterError(f"document frontmatter is missing required field {key!r}")
    validated: dict[str, Any] = {key: raw[key] for key in schema.required_fields}

    if "schema_version" in raw:
        version = raw["schema_version"]
        if isinstance(version, bool) or not isinstance(version, int):
            raise FrontmatterError("schema_version must be an integer")
        if version not in schema.known_versions:
            raise FrontmatterError(f"unknown schema_version {version!r}")
        validated["schema_version"] = version

    for key, allowed in schema.optional_enum_fields.items():
        value = raw.get(key)
        if value is None:
            continue
        if not isinstance(value, str) or value not in allowed:
            raise FrontmatterError(f"field {key!r} must be one of: {', '.join(sorted(allowed))}")
        validated[key] = value

    for key in schema.timestamp_fields:
        value = raw.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            raise FrontmatterError(f"field {key!r} must be an ISO-8601 timestamp string")
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise FrontmatterError(
                f"field {key!r} is not a valid ISO-8601 timestamp: {exc}"
            ) from exc
        if parsed.tzinfo is None:
            raise FrontmatterError(f"field {key!r} must carry an explicit UTC offset")
        validated[key] = parsed.astimezone(UTC)

    known = (
        set(schema.required_fields)
        | set(schema.optional_enum_fields)
        | set(schema.timestamp_fields)
        | {"schema_version"}
    )
    untrusted = {key: value for key, value in raw.items() if key not in known}
    if untrusted:
        validated[_UNTRUSTED_KEY] = untrusted
    return validated


__all__ = ["DocumentSchema", "FrontmatterError", "split_frontmatter", "validate_document"]
