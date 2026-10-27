"""Forge v3 evidence capture: before/after state for a declared change.

Captures sha256 hashes of a declared file set before and after an optional
command, run in an isolated temporary copy of the repository. The original
repository is never mutated. Output is bounded.

This is capture-only evidence; it is NOT a security review and does NOT
authorize merge or deploy.
"""

from __future__ import annotations

import hashlib
import os
import shlex
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Self
from uuid import uuid4

import structlog
from pydantic import BaseModel, ConfigDict, Field

from agency.forge.inspection import (
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_PATH_CHARS,
    _has_parent_traversal,
    _is_absolute_input,
    _resolve_root,
    _symlink_or_escape,
)

logger = structlog.get_logger(__name__)

MAX_OUTPUT_BYTES = 64 * 1024
MAX_EVIDENCE_TIMEOUT_S = 300
MAX_REPO_BYTES = 50 * 1024 * 1024

EvidenceGate = Literal["PASS", "FAIL"]

CAVEATS: tuple[str, ...] = (
    "evidence_not_a_security_review",
    "not_a_security_review",
    "evidence_capture_only",
    "capture_only",
    "release_not_authorized",
)


class EvidenceReport(BaseModel):
    """Immutable bounded evidence capture outcome."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    report_id: str = Field(min_length=1)
    created_at: datetime
    root: str
    before: dict[str, str | None] = Field(default_factory=dict)
    after: dict[str, str | None] = Field(default_factory=dict)
    command: tuple[str, ...] = ()
    returncode: int | None = None
    duration_s: float = Field(default=0.0, ge=0)
    stdout: str = ""
    stderr: str = ""
    workdir: str = ""
    gate: EvidenceGate = "PASS"
    caveats: tuple[str, ...] = CAVEATS
    note: str = (
        "Bounded evidence capture; not a security review; does not authorize merge or deploy."
    )

    @property
    def before_hashes(self) -> dict[str, str | None]:
        return self.before

    @property
    def after_hashes(self) -> dict[str, str | None]:
        return self.after


_SCHEMA = """
CREATE TABLE IF NOT EXISTS forge_evidence_reports (
    report_id  TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    gate       TEXT NOT NULL,
    document   TEXT NOT NULL
);
"""


class EvidenceStore:
    def __init__(self, db_path: str | Path = "forge_evidence.db") -> None:
        self._db_path = Path(db_path)
        if str(self._db_path) != ":memory:":
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def save(self, report: EvidenceReport) -> str:
        self._conn.execute(
            "INSERT INTO forge_evidence_reports (report_id, created_at, gate, document) "
            "VALUES (?, ?, ?, ?)",
            (
                report.report_id,
                report.created_at.isoformat(),
                report.gate,
                report.model_dump_json(),
            ),
        )
        self._conn.commit()
        logger.info("forge.evidence_saved", report_id=report.report_id, gate=report.gate)
        return report.report_id

    def load(self, report_id: str) -> EvidenceReport | None:
        cursor = self._conn.execute(
            "SELECT document FROM forge_evidence_reports WHERE report_id = ?", (report_id,)
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return EvidenceReport.model_validate_json(row[0])

    def close(self) -> None:
        self._conn.commit()
        self._conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


EvidenceReportStore = EvidenceStore


def _dir_size(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file() and not p.is_symlink():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def _normalize_command(command: Any) -> list[str] | None:
    if command is None:
        return None
    if isinstance(command, str):
        return shlex.split(command)
    if isinstance(command, (list, tuple)):
        out = [str(x) for x in command]
        return out if out else None
    return [str(command)]


def _normalize_paths(file_paths: Any) -> list[str]:
    if not isinstance(file_paths, (list, tuple)):
        raise ValueError("file_paths must be a list of strings")  # noqa: TRY004
    if any(not isinstance(p, str) for p in file_paths):
        raise ValueError("file_paths must be a list of strings")
    return list(file_paths)


def _hash_one(root: Path, raw: str) -> str | None:
    if len(raw) > MAX_PATH_CHARS or not raw.strip():
        return None
    if _is_absolute_input(raw):
        return None
    if _has_parent_traversal(raw):
        return None
    normalized = raw.replace("\\", "/")
    parts = [p for p in normalized.split("/") if p not in ("", ".")]
    if not parts or any(p in (".", "..") for p in parts):
        return None
    candidate = root.joinpath(*parts)
    escape = _symlink_or_escape(root, candidate)
    if escape is not None:
        return None
    try:
        st = os.lstat(candidate)
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode):
        return None
    if st.st_size > MAX_FILE_BYTES:
        return None
    try:
        h = hashlib.sha256()
        with open(candidate, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _hash_declared(root: Path, file_paths: list[str]) -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for raw in file_paths:
        normalized = raw.replace("\\", "/").strip("/")
        key = normalized if normalized else raw
        result[key] = _hash_one(root, raw)
    return result


def capture_evidence(
    root: str | Path,
    file_paths: list[str],
    *,
    command: Any = None,
    timeout_s: int = 120,
    before_root: str | Path | None = None,
    after_root: str | Path | None = None,
) -> EvidenceReport:
    real_root = _resolve_root(root)
    paths = _normalize_paths(file_paths)
    if len(paths) > MAX_FILES:
        return EvidenceReport(
            report_id=str(uuid4()),
            created_at=datetime.now(UTC),
            root=str(real_root),
            before={},
            after={},
            gate="FAIL",
            note=f"file count exceeds limit {MAX_FILES}",
        )
    if timeout_s <= 0 or timeout_s > MAX_EVIDENCE_TIMEOUT_S:
        raise ValueError(f"timeout_s must be in (0, {MAX_EVIDENCE_TIMEOUT_S}]")

    report_id = str(uuid4())
    created_at = datetime.now(UTC)
    cmd = _normalize_command(command)

    before_base = _resolve_root(before_root) if before_root is not None else real_root
    before_map = _hash_declared(before_base, paths)

    stdout = ""
    stderr = ""
    returncode: int | None = None
    duration = 0.0
    workdir = ""
    gate: EvidenceGate = "PASS"
    after_map: dict[str, str | None] = {}

    try:
        if cmd is not None:
            if _dir_size(real_root) > MAX_REPO_BYTES:
                raise RuntimeError("repository exceeds size cap")
            tmpdir = Path(tempfile.mkdtemp(prefix="forge-evidence-"))
            workdir_path = tmpdir / "repo"
            try:
                shutil.copytree(
                    real_root,
                    workdir_path,
                    symlinks=False,
                    ignore=shutil.ignore_patterns("__pycache__", ".git", ".pytest_cache", "*.pyc"),
                )
            except Exception as exc:
                shutil.rmtree(tmpdir, ignore_errors=True)
                raise RuntimeError(f"copy failed: {type(exc).__name__}") from exc

            workdir = str(workdir_path)
            env = dict(__import__("os").environ)
            src_dir = workdir_path / "src"
            if src_dir.is_dir():
                env["PYTHONPATH"] = str(src_dir) + (
                    os.pathsep + env.get("PYTHONPATH", "") if env.get("PYTHONPATH") else ""
                )
            start = time.monotonic()
            local_after: dict[str, str | None] = {}
            try:
                proc = subprocess.run(
                    cmd,
                    cwd=str(workdir_path),
                    capture_output=True,
                    text=True,
                    timeout=timeout_s,
                    check=False,
                    env=env,
                )
                duration = time.monotonic() - start
                stdout = (proc.stdout or "")[:MAX_OUTPUT_BYTES]
                stderr = (proc.stderr or "")[:MAX_OUTPUT_BYTES]
                returncode = proc.returncode
            except subprocess.TimeoutExpired as exc:
                duration = time.monotonic() - start
                raw_out = exc.stdout
                raw_err = exc.stderr
                if isinstance(raw_out, (bytes, bytearray)):
                    raw_out = raw_out.decode("utf-8", errors="replace")
                if isinstance(raw_err, (bytes, bytearray)):
                    raw_err = raw_err.decode("utf-8", errors="replace")
                stdout = (raw_out or "")[:MAX_OUTPUT_BYTES] if isinstance(raw_out, str) else ""
                stderr = (raw_err or "")[:MAX_OUTPUT_BYTES] if isinstance(raw_err, str) else ""
                returncode = None
                gate = "FAIL"
            except Exception as exc:  # noqa: BLE001
                duration = time.monotonic() - start
                stderr = str(exc)[:MAX_OUTPUT_BYTES]
                gate = "FAIL"
            finally:
                if workdir_path.is_dir():
                    local_after = _hash_declared(workdir_path, paths)
                shutil.rmtree(tmpdir, ignore_errors=True)
            after_map = local_after
        else:
            if after_root is not None:
                after_base = _resolve_root(after_root)
            else:
                after_base = real_root
            after_map = _hash_declared(after_base, paths)
    except Exception as exc:  # noqa: BLE001
        logger.warning("forge.evidence_failed", error=str(exc))
        gate = "FAIL"
        stderr = (stderr or str(exc))[:MAX_OUTPUT_BYTES]
        after_map = {}

    logger.info("forge.evidence_captured", root=str(real_root), gate=gate)
    return EvidenceReport(
        report_id=report_id,
        created_at=created_at,
        root=str(real_root),
        before=before_map,
        after=after_map,
        command=tuple(cmd or ()),
        returncode=returncode,
        duration_s=round(duration, 3),
        stdout=stdout,
        stderr=stderr,
        workdir=workdir,
        gate=gate,
        caveats=CAVEATS,
    )


run_evidence = capture_evidence
evidence_capture = capture_evidence
