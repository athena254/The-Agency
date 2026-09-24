"""Forge v1 read-only inspection gate.

Bounded, deterministic, non-executing inspector. It inventories a list of
caller-supplied relative paths under an absolute repository root, hashes each
accepted regular file, and runs a syntax-only ``ast.parse`` check for ``.py``
files. It never executes, imports, shells out, or mutates the repository.

This gate is NOT full Forge and NOT a security review. A ``PASS`` means only
bounded inventory + Python syntax held; it never authorizes merge or deploy.
"""

from __future__ import annotations

import ast
import hashlib
import os
import re
import sqlite3
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Self
from uuid import uuid4

import structlog
from pydantic import BaseModel, ConfigDict, Field

logger = structlog.get_logger(__name__)

MAX_FILES = 64
MAX_PATH_CHARS = 512
MAX_FILE_BYTES = 1_048_576  # 1 MiB per file; larger files are rejected, never read fully.

CAVEATS: tuple[str, ...] = ("tests_not_run", "security_review_not_run", "release_not_authorized")

GateState = Literal["PASS", "FAIL"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS forge_inspection_reports (
    report_id  TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    gate       TEXT NOT NULL,
    document   TEXT NOT NULL
);
"""

_ABSOLUTE_WINDOWS_RE = re.compile(r"^[A-Za-z]:[\\/]")
_ABSOLUTE_UNC_RE = re.compile(r"^(\\\\|//)")


class FileRecord(BaseModel):
    """Inventory entry for one accepted regular file. No source content stored."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    kind: Literal["python", "other"]
    syntax_ok: bool | None = Field(default=None)
    syntax_error: str | None = Field(default=None)
    security_reviewed: bool = Field(default=False)


class RejectedRecord(BaseModel):
    """Evidence entry for one rejected input path."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    reason: str = Field(min_length=1)


class InspectionReport(BaseModel):
    """Immutable bounded inspection outcome. Never authorizes merge/deploy."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    report_id: str = Field(min_length=1)
    created_at: datetime
    root: str
    gate: GateState
    caveats: tuple[str, ...] = CAVEATS
    files: tuple[FileRecord, ...] = ()
    rejected: tuple[RejectedRecord, ...] = ()
    note: str = Field(
        default="Bounded inventory/syntax gate only; not a security review; "
        "does not authorize merge or deploy."
    )


InspectChangesResult = InspectionReport


def _is_absolute_input(value: str) -> bool:
    if not value:
        return True
    if "\x00" in value:
        return True
    if value.startswith(("/", "\\")):
        return True
    if _ABSOLUTE_WINDOWS_RE.match(value):
        return True
    if _ABSOLUTE_UNC_RE.match(value):
        return True
    # Path.is_absolute covers the running platform's flavour as a backstop.
    try:
        if Path(value).is_absolute():
            return True
    except (OSError, ValueError):
        return True
    return False


def _has_parent_traversal(value: str) -> bool:
    normalized = value.replace("\\", "/")
    return any(part == ".." for part in normalized.split("/"))


def _resolve_root(root: str | Path) -> Path:
    candidate = Path(root)
    if not candidate.is_absolute():
        raise ValueError(f"root must be an absolute path, got {root!r}")
    # os.path.realpath resolves symlinks without requiring existence checks
    # beyond what we do next; no inspected source is imported or executed.
    real = Path(os.path.realpath(candidate))
    if not real.is_dir():
        raise ValueError(f"root must be an existing directory, got {root!r}")
    return real


def _rejects_for_name(value: str) -> str | None:
    if len(value) > MAX_PATH_CHARS:
        return "path_too_long"
    if not value or not value.strip():
        return "empty_path"
    if _is_absolute_input(value):
        return "absolute_path_not_allowed"
    if _has_parent_traversal(value):
        return "path_traversal_not_allowed"
    normalized = value.replace("\\", "/")
    if normalized.startswith("./"):
        return "dot_prefix_not_allowed"
    parts = [p for p in normalized.split("/") if p not in ("", ".")]
    if not parts or any(p in (".", "..") for p in parts):
        return "invalid_relative_path"
    return None


def _symlink_or_escape(real_root: Path, candidate: Path) -> str | None:
    """Reject symlinks on the walk and any resolved escape outside the root."""
    if not _is_within(candidate, real_root):
        return "path_escapes_root"
    # Walk every component before opening. On Windows this remains a best-effort
    # check: os.open lacks dir_fd/O_NOFOLLOW, so a racing parent replacement
    # cannot be made atomic. Forge is not a sandbox for hostile concurrent writes.
    probe = real_root
    for part in candidate.relative_to(real_root).parts:
        probe = probe / part
        try:
            if os.path.islink(probe):
                return "symlink_not_allowed"
        except OSError:
            return "unreadable_path"

    try:
        resolved = Path(os.path.realpath(candidate))
    except OSError:
        return "unreadable_path"
    if resolved != real_root and real_root not in resolved.parents:
        return "path_escapes_root"
    # Final component itself must not be a symlink (covered above, kept as backstop).
    try:
        if os.path.islink(candidate):
            return "symlink_not_allowed"
    except OSError:
        return "unreadable_path"
    return None


def _is_within(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def _inspect_one(real_root: Path, raw: str) -> tuple[FileRecord | None, RejectedRecord | None]:
    reason = _rejects_for_name(raw)
    if reason is not None:
        return None, RejectedRecord(path=raw[:MAX_PATH_CHARS], reason=reason)

    normalized = raw.replace("\\", "/")
    parts = [p for p in normalized.split("/") if p not in ("", ".")]
    candidate = real_root.joinpath(*parts)

    escape = _symlink_or_escape(real_root, candidate)
    if escape is not None:
        return None, RejectedRecord(path=raw, reason=escape)

    try:
        st = os.lstat(candidate)
    except FileNotFoundError:
        return None, RejectedRecord(path=raw, reason="missing_file")
    except OSError:
        return None, RejectedRecord(path=raw, reason="unreadable_path")

    if not stat.S_ISREG(st.st_mode):
        return None, RejectedRecord(path=raw, reason="nonregular_path")
    if st.st_size > MAX_FILE_BYTES:
        return None, RejectedRecord(path=raw, reason=f"file_too_large:{st.st_size}")

    try:
        # No unbounded read even if the file grows after lstat. O_NOFOLLOW
        # protects the final component on platforms that expose it; identity
        # and path checks catch swaps on platforms (notably Windows) that do not.
        fd = os.open(
            candidate, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        )
        try:
            opened = os.fstat(fd)
            if not stat.S_ISREG(opened.st_mode) or (st.st_dev, st.st_ino) != (
                opened.st_dev,
                opened.st_ino,
            ):
                return None, RejectedRecord(path=raw, reason="path_changed_during_read")
            data = bytearray()
            while len(data) <= MAX_FILE_BYTES:
                chunk = os.read(fd, min(64 * 1024, MAX_FILE_BYTES + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
            if _symlink_or_escape(real_root, candidate) is not None:
                return None, RejectedRecord(path=raw, reason="path_changed_during_read")
            current = os.lstat(candidate)
            if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
                return None, RejectedRecord(path=raw, reason="path_changed_during_read")
        finally:
            os.close(fd)
    except OSError:
        return None, RejectedRecord(path=raw, reason="unreadable_path")

    # st_size and len(data) can differ on concurrent mutation; enforce the cap on both.
    if len(data) > MAX_FILE_BYTES:
        return None, RejectedRecord(path=raw, reason=f"file_too_large:{len(data)}")
    payload = bytes(data)

    digest = hashlib.sha256(payload).hexdigest()
    display = "/".join(parts)
    is_python = candidate.suffix.lower() == ".py"
    if not is_python:
        return FileRecord(
            path=display,
            sha256=digest,
            size_bytes=len(payload),
            kind="other",
            syntax_ok=None,
            syntax_error=None,
            security_reviewed=False,
        ), None

    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        return None, RejectedRecord(path=raw, reason=f"bad_utf8_python_source:{exc.reason}")
    try:
        ast.parse(text, filename=display)
    except SyntaxError as exc:
        detail = f"{exc.msg} (line {exc.lineno})" if exc.lineno else str(exc.msg)
        return FileRecord(
            path=display,
            sha256=digest,
            size_bytes=len(payload),
            kind="python",
            syntax_ok=False,
            syntax_error=detail,
            security_reviewed=False,
        ), None
    return FileRecord(
        path=display,
        sha256=digest,
        size_bytes=len(payload),
        kind="python",
        syntax_ok=True,
        syntax_error=None,
        security_reviewed=False,
    ), None


def inspect_changes(root: str | Path, changed_paths: list[str]) -> InspectionReport:
    """Inspect caller-supplied relative paths under ``root`` without executing them.

    Returns a ``FAIL`` report (never raises) for rejected paths, syntax
    failures, duplicates, or cap violations. Raises ``ValueError`` only for an
    invalid ``root`` or a non-list ``changed_paths`` from the trusted caller.
    """
    real_root = _resolve_root(root)
    if not isinstance(changed_paths, list) or any(not isinstance(p, str) for p in changed_paths):
        raise ValueError("changed_paths must be a list of strings")

    report_id = str(uuid4())
    created_at = datetime.now(UTC)

    if len(changed_paths) > MAX_FILES:
        cap_rejections = (
            RejectedRecord(path="<batch>", reason=f"file_count_exceeds_limit:{MAX_FILES}"),
        )
        logger.info("forge.inspect_cap", root=str(real_root), count=len(changed_paths))
        return InspectionReport(
            report_id=report_id,
            created_at=created_at,
            root=str(real_root),
            gate="FAIL",
            files=(),
            rejected=cap_rejections,
        )

    if not changed_paths:
        return InspectionReport(
            report_id=report_id,
            created_at=created_at,
            root=str(real_root),
            gate="FAIL",
            rejected=(RejectedRecord(path="<batch>", reason="empty_change_set"),),
        )

    files: list[FileRecord] = []
    rejected: list[RejectedRecord] = []
    seen: set[str] = set()
    for raw in changed_paths:
        normalized = raw.replace("\\", "/")
        canonical = "/".join(p for p in normalized.split("/") if p not in ("", "."))
        # Deduplicate the same lexical target even with repeated separators or
        # Windows separators; never collapse traversal into a safe path.
        key = os.path.normcase(canonical) if os.name == "nt" else canonical
        if key in seen:
            rejected.append(RejectedRecord(path=raw[:MAX_PATH_CHARS], reason="duplicate_path"))
            continue
        seen.add(key)
        record, denial = _inspect_one(real_root, raw)
        if record is not None:
            files.append(record)
        elif denial is not None:
            rejected.append(denial)

    syntax_failed = any(f.kind == "python" and f.syntax_ok is False for f in files)
    gate: GateState = "FAIL" if (rejected or syntax_failed) else "PASS"
    logger.info(
        "forge.inspected",
        root=str(real_root),
        files=len(files),
        rejected=len(rejected),
        gate=gate,
    )
    return InspectionReport(
        report_id=report_id,
        created_at=created_at,
        root=str(real_root),
        gate=gate,
        files=tuple(files),
        rejected=tuple(rejected),
    )


class InspectionStore:
    """Synchronous SQLite persistence for :class:`InspectionReport`.

    Stores only inventory metadata (paths, hashes, sizes, syntax verdicts).
    Source content is never written. Keyed by ``report_id`` so reports survive
    process restart when backed by a file path.
    """

    def __init__(self, db_path: str | Path = "forge_inspection.db") -> None:
        self._db_path = Path(db_path)
        if str(self._db_path) != ":memory:":
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def save(self, report: InspectionReport) -> str:
        """Persist ``report``; returns its ``report_id``. Reports are write-once."""
        self._conn.execute(
            "INSERT INTO forge_inspection_reports (report_id, created_at, gate, document)"
            " VALUES (?, ?, ?, ?)",
            (
                report.report_id,
                report.created_at.isoformat(),
                report.gate,
                report.model_dump_json(),
            ),
        )
        self._conn.commit()
        logger.info("forge.report_saved", report_id=report.report_id, gate=report.gate)
        return report.report_id

    def load(self, report_id: str) -> InspectionReport | None:
        """Return the stored report, or ``None`` when unknown."""
        cursor = self._conn.execute(
            "SELECT document FROM forge_inspection_reports WHERE report_id = ?",
            (report_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return InspectionReport.model_validate_json(row[0])

    def close(self) -> None:
        """Flush and close the underlying connection."""
        self._conn.commit()
        self._conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


__all__ = [
    "CAVEATS",
    "MAX_FILES",
    "MAX_FILE_BYTES",
    "FileRecord",
    "InspectChangesResult",
    "InspectionReport",
    "InspectionStore",
    "RejectedRecord",
    "inspect_changes",
]
