"""Forge Phase 5 merge queue + protected paths.

Reads required gates, changed paths, and protected paths; seals a plan of
actions. Never auto-merges: a human must approve the final merge.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self
from uuid import uuid4

import structlog
from pydantic import BaseModel, ConfigDict, Field

logger = structlog.get_logger(__name__)

CAVEATS: tuple[str, ...] = ("never_auto_merges", "human_approval_required")


def _gate_value(obj: Any) -> str:
    if obj is None:
        return "FAIL"
    if isinstance(obj, bool):
        return "PASS" if obj else "FAIL"
    if isinstance(obj, str):
        return obj.upper()
    gate = getattr(obj, "gate", None)
    if gate is not None:
        return str(gate).upper()
    # mapping
    if isinstance(obj, dict):
        if "gate" in obj:
            return str(obj["gate"]).upper()
        if "status" in obj:
            return str(obj["status"]).upper()
    return str(obj).upper()


def _normalize_paths(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [str(x) for x in value]
    return [str(value)]


def _is_protected(path: str, protected: list[str]) -> bool:
    norm = path.replace("\\", "/").lstrip("./")
    # remove leading /
    while norm.startswith("/"):
        norm = norm[1:]
    for prot in protected:
        p = str(prot).replace("\\", "/").rstrip("/")
        if not p:
            continue
        if norm == p or norm.startswith(p + "/"):
            return True
        # protected may be a glob-ish prefix like "infra/"
        if p.endswith("/") and norm.startswith(p):
            return True
    return False


class MergeQueueEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    entry_id: str = Field(min_length=1)
    created_at: datetime
    changed_paths: tuple[str, ...] = ()
    protected_paths: tuple[str, ...] = ()
    touched_protected: tuple[str, ...] = ()
    approved: bool = False


class MergePlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    actions: tuple[str, ...] = ()
    sealed: bool = True
    merge_allowed: bool = False


class MergeQueueReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    report_id: str = Field(min_length=1)
    created_at: datetime
    root: str = ""
    merge_allowed: bool = False
    plan: MergePlan
    entries: tuple[MergeQueueEntry, ...] = ()
    blocked_reasons: tuple[str, ...] = ()
    caveats: tuple[str, ...] = CAVEATS
    note: str = "Merge queue plan; never auto-merges; human approval required."


_SCHEMA = """
CREATE TABLE IF NOT EXISTS forge_merge_queue_reports (
    report_id  TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    gate       TEXT NOT NULL,
    document   TEXT NOT NULL
);
"""


class MergeQueueStore:
    def __init__(self, db_path: str | Path = "forge_merge_queue.db") -> None:
        self._db_path = Path(db_path)
        if str(self._db_path) != ":memory:":
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def save(self, report: MergeQueueReport) -> str:
        self._conn.execute(
            "INSERT INTO forge_merge_queue_reports (report_id, created_at, gate, document) VALUES (?, ?, ?, ?)",
            (
                report.report_id,
                report.created_at.isoformat(),
                "PASS" if report.merge_allowed else "FAIL",
                report.model_dump_json(),
            ),
        )
        self._conn.commit()
        logger.info(
            "forge.merge_queue_saved", report_id=report.report_id, allowed=report.merge_allowed
        )
        return report.report_id

    def load(self, report_id: str) -> MergeQueueReport | None:
        cursor = self._conn.execute(
            "SELECT document FROM forge_merge_queue_reports WHERE report_id = ?", (report_id,)
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return MergeQueueReport.model_validate_json(row[0])

    def close(self) -> None:
        self._conn.commit()
        self._conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def evaluate_merge(
    inspection: Any = None,
    security: Any = None,
    tests: Any = None,
    review: Any = None,
    changed_paths: Any = None,
    protected_paths: Any = None,
    *,
    root: str | Path | None = None,
    approved: bool = False,
    approval: bool = False,
    approved_protected: bool = False,
    protected_approved: bool = False,
    paths: Any = None,
    protected: Any = None,
    **kwargs: Any,
) -> MergeQueueReport:
    # Resolve kwargs for alternative names
    if changed_paths is None:
        changed_paths = kwargs.get("changed_paths", kwargs.get("paths", paths))
    if protected_paths is None:
        protected_paths = kwargs.get("protected_paths", kwargs.get("protected", protected))
    if root is None:
        root = kwargs.get("root", "")

    # If called with gate strings via kwargs
    if inspection is None:
        inspection = kwargs.get("inspection_gate", kwargs.get("inspection"))
    if security is None:
        security = kwargs.get("security_gate", kwargs.get("security"))
    if tests is None:
        tests = kwargs.get("test_gate", kwargs.get("tests"))
    if review is None:
        review = kwargs.get("review_gate", kwargs.get("review"))

    # approval flags
    approved = bool(
        approved
        or approval
        or approved_protected
        or protected_approved
        or kwargs.get("approved", False)
        or kwargs.get("approval", False)
    )

    changed = _normalize_paths(changed_paths)
    protected = _normalize_paths(protected_paths)

    # If positional args shifted: detect if inspection is a list (changed paths) etc.
    # Handle case evaluate_merge(changed_paths, protected_paths) with gates in kwargs already handled.

    ig = _gate_value(inspection)
    sg = _gate_value(security)
    tg = _gate_value(tests)
    rg = _gate_value(review)

    blocked: list[str] = []
    if ig != "PASS":
        blocked.append(f"inspection_gate_not_pass:{ig}")
    if sg != "PASS":
        blocked.append(f"security_gate_not_pass:{sg}")
    if tg != "PASS":
        blocked.append(f"test_gate_not_pass:{tg}")
    if rg != "PASS":
        blocked.append(f"review_gate_not_pass:{rg}")

    touched = [p for p in changed if _is_protected(p, protected)]
    if touched and not approved:
        blocked.append("protected_path_touched_without_approval:" + ",".join(touched))

    merge_allowed = len(blocked) == 0

    actions: list[str] = [
        "verify_inspection_gate",
        "verify_security_gate",
        "verify_test_gate",
        "verify_review_gate",
        "check_protected_paths",
    ]
    if merge_allowed:
        actions.append("queue_for_human_merge_approval")
    else:
        actions.append("block_merge")
    actions.append("never_auto_merge")

    entry = MergeQueueEntry(
        entry_id=str(uuid4()),
        created_at=datetime.now(UTC),
        changed_paths=tuple(changed),
        protected_paths=tuple(protected),
        touched_protected=tuple(touched),
        approved=approved,
    )

    plan = MergePlan(actions=tuple(actions), sealed=True, merge_allowed=merge_allowed)
    report = MergeQueueReport(
        report_id=str(uuid4()),
        created_at=datetime.now(UTC),
        root=str(root) if root is not None else "",
        merge_allowed=merge_allowed,
        plan=plan,
        entries=(entry,),
        blocked_reasons=tuple(blocked),
    )
    logger.info("forge.merge_queue", allowed=merge_allowed, blocked=len(blocked))
    return report


__all__ = [
    "CAVEATS",
    "MergePlan",
    "MergeQueueEntry",
    "MergeQueueReport",
    "MergeQueueStore",
    "evaluate_merge",
]
