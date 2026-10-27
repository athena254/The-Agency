"""Forge v2 pipeline: compose inspection + security scan + (optional) tests.

Produces a single durable verdict report. Never authorizes merge/deploy on
its own; caveats are preserved and extended.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Self
from uuid import uuid4

import structlog
from pydantic import BaseModel, ConfigDict, Field

from agency.forge.inspection import InspectionReport, inspect_changes
from agency.forge.security_scan import SecurityReport, scan_changes
from agency.forge.test_runner import TestReport, run_tests

logger = structlog.get_logger(__name__)

CAVEATS = (
    "tests_not_run",
    "security_review_not_run",
    "release_not_authorized",
)

PipelineGate = Literal["PASS", "FAIL"]


class ForgePipelineReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    report_id: str = Field(min_length=1)
    created_at: datetime
    root: str
    gate: PipelineGate
    inspection: InspectionReport
    security: SecurityReport
    tests: TestReport | None = None
    caveats: tuple[str, ...] = CAVEATS
    note: str = "Composed Forge pipeline verdict; does not authorize merge or deploy."


_SCHEMA = """
CREATE TABLE IF NOT EXISTS forge_pipeline_reports (
    report_id  TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    gate       TEXT NOT NULL,
    document   TEXT NOT NULL
);
"""


class ForgePipelineStore:
    def __init__(self, db_path: str | Path = "forge_pipeline.db") -> None:
        self._db_path = Path(db_path)
        if str(self._db_path) != ":memory:":
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def save(self, report: ForgePipelineReport) -> str:
        self._conn.execute(
            "INSERT INTO forge_pipeline_reports (report_id, created_at, gate, document) VALUES (?, ?, ?, ?)",
            (
                report.report_id,
                report.created_at.isoformat(),
                report.gate,
                report.model_dump_json(),
            ),
        )
        self._conn.commit()
        return report.report_id

    def load(self, report_id: str) -> ForgePipelineReport | None:
        cursor = self._conn.execute(
            "SELECT document FROM forge_pipeline_reports WHERE report_id = ?", (report_id,)
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return ForgePipelineReport.model_validate_json(row[0])

    def close(self) -> None:
        self._conn.commit()
        self._conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def run_pipeline(
    root: str | Path,
    changed_paths: list[str],
    *,
    run_tests_flag: bool = False,
    test_paths: list[str] | None = None,
    test_timeout_s: int = 120,
) -> ForgePipelineReport:
    real_root = Path(root)
    if not real_root.is_absolute():
        raise ValueError("root must be an absolute path")

    inspection = inspect_changes(real_root, changed_paths)
    security = scan_changes(real_root, changed_paths)

    tests: TestReport | None = None
    if run_tests_flag:
        tests = run_tests(real_root, test_paths or [], timeout_s=test_timeout_s)

    overall = "PASS"
    if inspection.gate != "PASS":
        overall = "FAIL"
    if security.gate != "PASS":
        overall = "FAIL"
    if tests is not None and tests.gate != "PASS":
        overall = "FAIL"

    # Update caveats based on what actually ran.
    caveats = list(CAVEATS)
    if tests is not None:
        caveats = [c for c in caveats if c != "tests_not_run"]
    # security scan ran, so drop that caveat
    caveats = [c for c in caveats if c != "security_review_not_run"]

    report = ForgePipelineReport(
        report_id=str(uuid4()),
        created_at=datetime.now(UTC),
        root=str(real_root),
        gate=overall,
        inspection=inspection,
        security=security,
        tests=tests,
        caveats=tuple(caveats),
    )
    logger.info("forge.pipeline", root=str(real_root), gate=overall)
    return report
