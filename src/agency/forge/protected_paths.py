"""Forge protected-paths enforcement.

A small, deterministic helper used by the Forge to decide whether a path
touches protected territory (secrets, infrastructure, migrations, env files).
Protected paths require explicit human approval before the Forge may modify
them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

PROTECTED_PATHS: list[str] = [".env", "secrets", "infra", "migrations"]


def _normalize(path: object) -> list[str]:
    text = str(path).replace("\\", "/")
    # Drop leading "./" segments.
    while text.startswith("./"):
        text = text[2:]
    # Drop a leading drive letter (e.g. "C:/...") is unnecessary; keep segments.
    text = text.lstrip("/")
    if not text:
        return []
    return [seg for seg in text.split("/") if seg not in ("", ".")]


def is_protected(path: object) -> bool:
    """Return True if ``path`` is or lives under a protected path.

    Matching is component-based so that ``src/secrets_helper.py`` is not
    treated as protected while ``secrets/api.key`` is. Environment files
    (``.env``, ``.env.local``, ...) are protected wherever they appear.
    """
    parts = _normalize(path)
    if not parts:
        return False

    for part in parts:
        if part in PROTECTED_PATHS:
            return True

    basename = parts[-1]
    if basename == ".env" or basename.startswith(".env.") or basename.startswith(".env_"):
        return True

    # Also honour protected entries supplied as nested-ish prefixes such as
    # "infra/" by checking any path prefix.
    joined = "/".join(parts)
    for prot in PROTECTED_PATHS:
        p = prot.strip().rstrip("/")
        if not p:
            continue
        if joined == p or joined.startswith(p + "/"):
            return True

    return False


def is_protected_path(path: object) -> bool:
    """Alias for :func:`is_protected`."""
    return is_protected(path)


def protected_paths() -> list[str]:
    return list(PROTECTED_PATHS)


__all__ = [
    "PROTECTED_PATHS",
    "is_protected",
    "is_protected_path",
    "protected_paths",
]
