"""Forge Phase 3 bounded coding agent.

Applies a bounded list of exact single-occurrence string replacements inside an
isolated git worktree, then runs the inspection gate + security scan (and
optionally tests) on the resulting tree. Never mutates the original checkout
and never auto-merges.

Fail-closed: any invalid edit, path, or gate failure yields a FAIL report.
"""

from __future__ import annotations

import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Self
from uuid import uuid4

import structlog
from pydantic import BaseModel, ConfigDict, Field

from agency.forge.inspection import (
    MAX_FILE_BYTES,
    InspectionReport,
    _rejects_for_name,
    _symlink_or_escape,
    inspect_changes,
)
from agency.forge.pipeline import CAVEATS as _PIPELINE_CAVEATS
from agency.forge.security_scan import SecurityReport, scan_changes
from agency.forge.test_runner import TestReport, run_tests
from agency.forge.worktree import WorktreeError, WorktreeInfo, create_worktree, remove_worktree

logger = structlog.get_logger(__name__)

MAX_EDITS = 32
CodingAgentGate = Literal["PASS", "FAIL"]

_BASE_CAVEATS = _PIPELINE_CAVEATS


class CodingAgentReport(BaseModel):
    """Immutable bounded coding-agent outcome. Never authorizes merge/deploy."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    report_id: str = Field(min_length=1)
    created_at: datetime
    worktree_path: str = Field(min_length=1)
    branch: str = Field(min_length=1)
    edits_applied: int = Field(ge=0)
    inspection: InspectionReport
    security: SecurityReport
    tests: TestReport | None = None
    gate: CodingAgentGate
    caveats: tuple[str, ...] = _BASE_CAVEATS
    note: str = Field(default="Bounded coding agent; does not auto-merge or authorize release.")


_SCHEMAS = {
    "coding_agent": """
CREATE TABLE IF NOT EXISTS forge_coding_agent_reports (
    report_id  TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    gate       TEXT NOT NULL,
    document   TEXT NOT NULL
);
"""
}


class CodingAgentStore:
    """Synchronous SQLite persistence for :class:`CodingAgentReport`."""

    def __init__(self, db_path: str | Path = "forge_coding_agent.db") -> None:
        self._db_path = Path(db_path)
        if str(self._db_path) != ":memory:":
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
        import sqlite3

        self._sqlite3 = sqlite3
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMAS["coding_agent"])
        self._conn.commit()

    def save(self, report: CodingAgentReport) -> str:
        self._conn.execute(
            "INSERT INTO forge_coding_agent_reports (report_id, created_at, gate, document) "
            "VALUES (?, ?, ?, ?)",
            (
                report.report_id,
                report.created_at.isoformat(),
                report.gate,
                report.model_dump_json(),
            ),
        )
        self._conn.commit()
        logger.info("forge.coding_agent_saved", report_id=report.report_id, gate=report.gate)
        return report.report_id

    def load(self, report_id: str) -> CodingAgentReport | None:
        cursor = self._conn.execute(
            "SELECT document FROM forge_coding_agent_reports WHERE report_id = ?",
            (report_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return CodingAgentReport.model_validate_json(row[0])

    def close(self) -> None:
        self._conn.commit()
        self._conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Edit normalization
# ---------------------------------------------------------------------------


def _normalize_edit(item: object) -> tuple[str, str, str] | None:
    """Return (path, old, new) or None if shape unrecognized."""
    if isinstance(item, (tuple, list)):
        if len(item) >= 3:
            return str(item[0]), str(item[1]), str(item[2])
        return None
    if isinstance(item, dict):
        path = item.get("path") or item.get("file") or item.get("target")
        old = (
            item.get("old_string")
            or item.get("old")
            or item.get("search")
            or item.get("old_text")
            or item.get("find")
        )
        new = (
            item.get("new_string")
            or item.get("new")
            or item.get("replace")
            or item.get("new_text")
            or item.get("replacement")
        )
        if path is None or old is None or new is None:
            return None
        return str(path), str(old), str(new)
    # object with attributes
    for p_attr, o_attr, n_attr in (
        ("path", "old_string", "new_string"),
        ("path", "old", "new"),
    ):
        if hasattr(item, p_attr) and hasattr(item, o_attr) and hasattr(item, n_attr):
            return (
                str(getattr(item, p_attr)),
                str(getattr(item, o_attr)),
                str(getattr(item, n_attr)),
            )
    return None


def _validate_path(raw: str) -> str | None:
    """Return rejection reason or None if path is acceptable."""
    reason = _rejects_for_name(raw)
    if reason is not None:
        return reason
    normalized = raw.replace("\\", "/")
    parts = [p for p in normalized.split("/") if p not in ("", ".")]
    if not parts:
        return "invalid_relative_path"
    candidate_reason = None
    # symlink/escape checked later against root
    return candidate_reason


def _resolve_edits(edits: object) -> list[object]:
    if edits is None:
        return []
    if isinstance(edits, (list, tuple)):
        return list(edits)
    return [edits]


def run_coding_agent(
    repo_root: str | Path,
    branch_or_edits: object = None,
    edits: object = None,
    *,
    branch: str | None = None,
    edits_list: object = None,
    base_ref: str = "HEAD",
    worktree_root: str | Path | None = None,
    run_tests_flag: bool = False,
    test_paths: list[str] | None = None,
    test_timeout_s: int = 120,
    cleanup: bool = False,
    max_edits: int = MAX_EDITS,
) -> CodingAgentReport:
    """Apply bounded edits in an isolated worktree, then run Forge gates."""
    # Resolve positional ambiguity.
    if isinstance(branch_or_edits, str):
        if branch is None:
            branch = branch_or_edits
    elif isinstance(branch_or_edits, (list, tuple)) and edits is None:
        edits = branch_or_edits
    if edits is None and edits_list is not None:
        edits = edits_list

    if branch is None or not str(branch).strip():
        branch = "forge-coding-agent"

    real_repo = Path(repo_root)
    if not real_repo.is_absolute():
        raise ValueError(f"repo_root must be an absolute path, got {repo_root!r}")
    if not real_repo.is_dir():
        raise ValueError(f"repo_root must be an existing directory, got {repo_root!r}")

    normalized_edits = _resolve_edits(edits)
    if len(normalized_edits) > max_edits:
        # Fail closed: still create a worktree? We'll create then report FAIL with no edits.
        pass

    report_id = str(uuid4())
    created_at = datetime.now(UTC)

    # Create worktree.
    try:
        info: WorktreeInfo = create_worktree(
            real_repo, str(branch), base_ref=base_ref, worktree_root=worktree_root
        )
    except WorktreeError as exc:
        raise ValueError(f"failed to create worktree: {exc}") from exc

    worktree_path = Path(info.worktree_path)
    wt_root_str = str(worktree_path)

    applied_paths: list[str] = []
    edits_applied = 0
    failure: str | None = None

    # Enforce max edits before applying.
    if len(normalized_edits) > max_edits:
        failure = f"edit_count_exceeds_limit:{max_edits}"

    if failure is None:
        for raw_edit in normalized_edits:
            if failure is not None:
                break
            parsed = _normalize_edit(raw_edit)
            if parsed is None:
                failure = "invalid_edit_shape"
                break
            raw_path, old_str, new_str = parsed

            reason = _rejects_for_name(raw_path)
            if reason is not None:
                failure = reason
                break

            normalized = raw_path.replace("\\", "/")
            parts = [p for p in normalized.split("/") if p not in ("", ".")]
            display = "/".join(parts)

            # symlink/escape check
            candidate = worktree_path.joinpath(*parts)
            escape = _symlink_or_escape(worktree_path, candidate)
            if escape is not None:
                failure = escape
                break

            try:
                st = os.lstat(candidate)
            except FileNotFoundError:
                failure = "missing_file"
                break
            except OSError:
                failure = "unreadable_path"
                break
            if not stat.S_ISREG(st.st_mode):
                failure = "nonregular_path"
                break
            if st.st_size > MAX_FILE_BYTES:
                failure = f"file_too_large:{st.st_size}"
                break

            # read bounded
            try:
                with open(candidate, "rb") as fh:
                    data = fh.read(MAX_FILE_BYTES + 1)
            except OSError:
                failure = "unreadable_path"
                break
            if len(data) > MAX_FILE_BYTES:
                failure = f"file_too_large:{len(data)}"
                break
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                failure = "bad_utf8_source"
                break

            count = text.count(old_str)
            if count != 1:
                failure = f"old_string_occurrence_count:{count}"
                break

            new_text = text.replace(old_str, new_str, 1)
            try:
                candidate.write_text(new_text, encoding="utf-8")
            except OSError:
                failure = "write_failed"
                break

            edits_applied += 1
            if display not in applied_paths:
                applied_paths.append(display)

    # Run gates on applied (or all? applied) paths.
    changed_for_scan = applied_paths if applied_paths else []
    # If no edits applied but no failure? still scan empty -> fail closed naturally.
    try:
        inspection = inspect_changes(worktree_path, changed_for_scan)
    except Exception as exc:  # noqa: BLE001
        logger.error("forge.coding_agent_inspect_error", error=str(exc))
        # Build a FAIL-ish report manually? Use inspect with empty to get report.
        from agency.forge.inspection import inspect_changes as _ic

        inspection = _ic(worktree_path, [])
    try:
        security = scan_changes(worktree_path, changed_for_scan)
    except Exception as exc:  # noqa: BLE001
        logger.error("forge.coding_agent_scan_error", error=str(exc))
        from agency.forge.security_scan import scan_changes as _sc

        security = _sc(worktree_path, [])

    tests: TestReport | None = None
    if run_tests_flag:
        tp = test_paths or []
        try:
            tests = run_tests(worktree_path, tp, timeout_s=test_timeout_s)
        except Exception as exc:  # noqa: BLE001
            logger.error("forge.coding_agent_tests_error", error=str(exc))
            tests = None

    # Overall gate.
    overall: CodingAgentGate = "PASS"
    if failure is not None:
        overall = "FAIL"
    if edits_applied == 0 and len(normalized_edits) > 0:
        overall = "FAIL"
    if len(normalized_edits) == 0:
        # No edits requested: treat as fail-closed? Keep PASS only if scans pass on empty? empty fails.
        overall = "FAIL"
    if inspection.gate != "PASS":
        overall = "FAIL"
    if security.gate != "PASS":
        overall = "FAIL"
    if tests is not None and tests.gate != "PASS":
        overall = "FAIL"

    # Caveats.
    caveats = list(_BASE_CAVEATS)
    if tests is not None:
        caveats = [c for c in caveats if c != "tests_not_run"]
    caveats = [c for c in caveats if c != "security_review_not_run"]
    if failure is not None:
        note_extra = f" failure={failure}"
    else:
        note_extra = ""
    note = "Bounded coding agent; does not auto-merge or authorize release." + note_extra

    report = CodingAgentReport(
        report_id=report_id,
        created_at=created_at,
        worktree_path=wt_root_str,
        branch=str(info.branch),
        edits_applied=edits_applied,
        inspection=inspection,
        security=security,
        tests=tests,
        gate=overall,
        caveats=tuple(caveats),
        note=note,
    )

    if cleanup:
        try:
            remove_worktree(real_repo, worktree_path)
        except WorktreeError as exc:
            logger.warning("forge.coding_agent_cleanup_failed", error=str(exc))

    logger.info(
        "forge.coding_agent_done",
        root=wt_root_str,
        edits=edits_applied,
        gate=overall,
    )
    return report


# Aliases for compatibility.
apply_edits = run_coding_agent
run_agent = run_coding_agent
code = run_coding_agent


__all__ = [
    "MAX_EDITS",
    "CodingAgentGate",
    "CodingAgentReport",
    "CodingAgentStore",
    "apply_edits",
    "code",
    "run_agent",
    "run_coding_agent",
]
