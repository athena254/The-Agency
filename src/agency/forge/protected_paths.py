"""Forge protected-paths enforcement.

A small, deterministic helper used by the Forge to decide whether a path
touches protected territory (secrets, infrastructure, migrations, env files,
CI configuration and docs/SPEC*.md).
Protected paths require explicit human approval before the Forge may modify
them.
"""

from __future__ import annotations

from itertools import pairwise

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
    # Policy matching is case-insensitive and honours Win32 trailing-dot/space
    # aliases. This is not path validation: callers must reject '..' first.
    return [seg.rstrip(" .").casefold() for seg in text.split("/") if seg not in ("", ".")]


def is_protected(path: object) -> bool:
    """Return True if ``path`` is or lives under a protected path.

    Matching is component-based so that ``src/secrets_helper.py`` is not
    treated as protected while ``secrets/api.key`` is. Environment files
    (``.env``, ``.env.local``, ...) are protected wherever they appear.

    The bounded automatic writer also refuses every colon or tilde spelling:
    streams and Windows short-name aliases are outside its supported relative
    namespace. This intentionally excludes even benign names containing these
    characters; it is a conservative refusal, not a filesystem canonicalizer.
    Callers must retain raw shape/traversal validation before this policy.
    """
    if ":" in str(path) or "~" in str(path):
        return True
    parts = _normalize(path)
    if not parts:
        return False

    for part in parts:
        if part in PROTECTED_PATHS:
            return True

    basename = parts[-1]
    if basename == ".env" or basename.startswith((".env.", ".env_")):
        return True

    # CI configuration and normative specs are never automatic edit targets.
    if any(part in {".circleci", ".buildkite"} for part in parts):
        return True
    if basename in {
        ".gitlab-ci.yml",
        ".travis.yml",
        "azure-pipelines.yml",
        "jenkinsfile",
        "bitbucket-pipelines.yml",
        "appveyor.yml",
        ".drone.yml",
    }:
        return True
    for parent, child in pairwise(parts):
        if parent == ".github" and child == "workflows":
            return True
        if parent == "docs" and child.startswith("spec") and child.endswith(".md"):
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
