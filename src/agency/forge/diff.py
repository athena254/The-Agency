"""Forge diff bundle: bounded, risk-ranked change content for the review gate.

The LLM review gate reviews *code*, not metadata: this module renders the
actual change set (tracked diffs + untracked file bodies) as one bounded
text bundle. Truncation is risk-ranked: protected/critical paths are emitted
first and get a doubled per-file budget, so when the bundle must be cut the
safe bulk is truncated before the risky files. Every omitted or cut path is
recorded so the reviewer can name what it did not see.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from agency.forge.protected_paths import is_protected

GIT_TIMEOUT_S = 30
MAX_PER_FILE_BYTES = 8_000
MAX_PRIORITY_PER_FILE_BYTES = 16_000
MAX_TOTAL_BYTES = 48_000

FILE_TRUNCATION_MARK = "\n...[file diff truncated for review]\n"
BUNDLE_EXHAUSTED_MARK = "\n...[diff bundle budget exhausted for review]\n"

_PSEUDO_DIFF_HEADER = "--- /dev/null\n+++ b/{path}\n@@ -0,0 +1,{n} @@\n"


class DiffBundle(BaseModel):
    """Immutable bounded change-content bundle for one review round."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = ""
    included_paths: tuple[str, ...] = ()
    omitted_paths: tuple[str, ...] = ()
    truncated_paths: tuple[str, ...] = ()
    total_bytes: int = Field(default=0, ge=0)


def head_sha(root: str | Path) -> str:
    """Return the current HEAD sha of *root*, or "" when git cannot answer."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def delta_paths(root: str | Path, base_sha: str) -> tuple[str, ...] | None:
    """Return paths changed between *base_sha* and HEAD, or None on failure.

    None means "the delta could not be computed" (unknown base, not a repo,
    git error); callers must fall back to the full change set and say so.
    """
    if not base_sha or not base_sha.strip():
        return None
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "diff", "--name-only", "-z", base_sha.strip(), "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    paths: list[str] = []
    for raw in proc.stdout.split("\0"):
        path = raw.strip().replace("\\", "/")
        if path:
            paths.append(path)
    return tuple(sorted(set(paths)))


def _tracked_diff(root: Path, path: str) -> str | None:
    """Unified diff of one tracked path vs HEAD; None when git cannot answer."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "diff", "HEAD", "--", path],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout or ""


def _untracked_body(root: Path, path: str) -> str | None:
    """Pseudo-diff (all-added-lines) for one untracked path; None if unreadable."""
    candidate = root.joinpath(*[p for p in path.split("/") if p not in ("", ".")])
    try:
        if not candidate.is_file():
            return None
        data = candidate.read_bytes()[:MAX_PRIORITY_PER_FILE_BYTES]
        text = data.decode("utf-8", errors="replace")
    except OSError:
        return None
    lines = text.splitlines()
    header = _PSEUDO_DIFF_HEADER.format(path=path, n=max(1, len(lines)))
    body = "".join(f"+{line}\n" for line in lines)
    return header + body


def _content_for(root: Path, path: str, untracked: set[str]) -> str | None:
    if path in untracked:
        return _untracked_body(root, path)
    return _tracked_diff(root, path)


def _is_safe_relative(path: str) -> bool:
    """True for a plain relative path: no traversal, no absolute root."""
    parts = path.split("/")
    return not path.startswith("/") and ".." not in parts


def build_diff_bundle(root: str | Path, changed_paths: list[str]) -> DiffBundle:
    """Render *changed_paths* as one bounded diff bundle, risk-ranked.

    Protected paths are emitted first with a doubled per-file budget; the
    rest follow in sorted order under the normal budget. The bundle stops at
    MAX_TOTAL_BYTES; everything not fully emitted is reported.
    """
    real_root = Path(root)
    normalized = sorted(
        {
            p.replace("\\", "/")
            for p in changed_paths
            if p and p.strip() and _is_safe_relative(p.replace("\\", "/"))
        }
    )
    if not normalized:
        return DiffBundle()

    untracked: set[str] = set()
    try:
        ls = subprocess.run(
            ["git", "-C", str(real_root), "ls-files", "--others", "--exclude-standard"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_TIMEOUT_S,
        )
        if ls.returncode == 0:
            untracked = {p.strip().replace("\\", "/") for p in ls.stdout.splitlines() if p.strip()}
    except (OSError, subprocess.SubprocessError):
        untracked = set()

    priority = [p for p in normalized if is_protected(p)]
    rest = [p for p in normalized if not is_protected(p)]

    parts: list[str] = []
    included: list[str] = []
    truncated: list[str] = []
    omitted: list[str] = []
    budget = MAX_TOTAL_BYTES

    for path in [*priority, *rest]:
        if budget <= 0:
            omitted.append(path)
            continue
        per_file_cap = MAX_PRIORITY_PER_FILE_BYTES if is_protected(path) else MAX_PER_FILE_BYTES
        content = _content_for(real_root, path, untracked)
        if content is None or not content.strip():
            omitted.append(path)
            continue
        encoded = content.encode("utf-8")
        if len(encoded) <= per_file_cap:
            chunk = content
        else:
            cut = content.encode("utf-8")[:per_file_cap].decode("utf-8", errors="ignore")
            chunk = cut + FILE_TRUNCATION_MARK
            truncated.append(path)
        rendered = f"--- path: {path} ---\n{chunk}"
        cost = len(rendered.encode("utf-8"))
        if cost > budget:
            omitted.append(path)
            continue
        budget -= cost
        parts.append(rendered)
        included.append(path)

    text = "\n".join(parts)
    if omitted:
        text += BUNDLE_EXHAUSTED_MARK + "paths not shown: " + ", ".join(omitted) + "\n"
    return DiffBundle(
        text=text,
        included_paths=tuple(included),
        omitted_paths=tuple(omitted),
        truncated_paths=tuple(truncated),
        total_bytes=len(text.encode("utf-8")),
    )


__all__ = [
    "BUNDLE_EXHAUSTED_MARK",
    "FILE_TRUNCATION_MARK",
    "MAX_PER_FILE_BYTES",
    "MAX_PRIORITY_PER_FILE_BYTES",
    "MAX_TOTAL_BYTES",
    "DiffBundle",
    "build_diff_bundle",
    "delta_paths",
    "head_sha",
]
