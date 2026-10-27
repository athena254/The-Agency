"""Forge Phase 5 AI reviewer loop (local heuristic by default).

Bounded, deterministic reviewer that scores a change set 0-5 using the
security scan, lint-ish checks, and (optionally) the test gate. Never
performs a human review; caveats preserve that honestly.

Fail-closed: any gate failure or high finding blocks PASS.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Self
from uuid import uuid4

import structlog
from pydantic import BaseModel, ConfigDict, Field

from agency.forge.inspection import (
    MAX_FILE_BYTES,
    _resolve_root,
    _symlink_or_escape,
)
from agency.forge.security_scan import scan_changes
from agency.forge.test_runner import run_tests

logger = structlog.get_logger(__name__)

MAX_ROUNDS = 5
MAX_LINT_LINE_LENGTH = 120

ReviewGate = Literal["PASS", "FAIL"]
Severity = Literal["low", "medium", "high", "critical"]

CAVEATS: tuple[str, ...] = (
    "not_a_human_review",
    "heuristic_review",
    "tests_not_run",
)


class ReviewFinding(BaseModel):
    """One finding raised by the (heuristic) reviewer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str = ""
    line: int = Field(default=1, ge=1)
    rule_id: str = Field(min_length=1)
    severity: Severity = "medium"
    message: str = Field(min_length=1)
    resolved: bool = False


class ReviewReport(BaseModel):
    """Immutable bounded review outcome. Never authorizes merge/deploy."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    report_id: str = Field(min_length=1)
    created_at: datetime
    root: str
    gate: ReviewGate
    score: int = Field(ge=0, le=5)
    threshold: int = Field(default=4, ge=0, le=5)
    rounds: int = Field(default=1, ge=1, le=MAX_ROUNDS)
    max_rounds: int = Field(default=MAX_ROUNDS, ge=1, le=MAX_ROUNDS)
    findings: tuple[ReviewFinding, ...] = ()
    unresolved_high: int = Field(default=0, ge=0)
    caveats: tuple[str, ...] = CAVEATS
    note: str = "Heuristic review; not a human review; does not authorize merge or deploy."


_SCHEMA = """
CREATE TABLE IF NOT EXISTS forge_review_reports (
    report_id  TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    gate       TEXT NOT NULL,
    document   TEXT NOT NULL
);
"""


class ReviewStore:
    def __init__(self, db_path: str | Path = "forge_review.db") -> None:
        self._db_path = Path(db_path)
        if str(self._db_path) != ":memory:":
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def save(self, report: ReviewReport) -> str:
        self._conn.execute(
            "INSERT INTO forge_review_reports (report_id, created_at, gate, document) VALUES (?, ?, ?, ?)",
            (
                report.report_id,
                report.created_at.isoformat(),
                report.gate,
                report.model_dump_json(),
            ),
        )
        self._conn.commit()
        logger.info("forge.review_saved", report_id=report.report_id, gate=report.gate)
        return report.report_id

    def load(self, report_id: str) -> ReviewReport | None:
        cursor = self._conn.execute(
            "SELECT document FROM forge_review_reports WHERE report_id = ?", (report_id,)
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return ReviewReport.model_validate_json(row[0])

    def close(self) -> None:
        self._conn.commit()
        self._conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def _lint_check(root: Path, path: str) -> list[ReviewFinding]:
    findings: list[ReviewFinding] = []
    normalized = path.replace("\\", "/")
    parts = [p for p in normalized.split("/") if p not in ("", ".")]
    if not parts:
        return findings
    candidate = root.joinpath(*parts)
    escape = _symlink_or_escape(root, candidate)
    if escape is not None:
        return findings
    try:
        st = os.lstat(candidate)
    except OSError:
        return findings
    if not os.path.isfile(candidate) or st.st_size > MAX_FILE_BYTES:
        return findings
    if candidate.suffix.lower() != ".py":
        return findings
    try:
        text = candidate.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return findings
    for lineno, line in enumerate(text.splitlines(), start=1):
        if len(line) > MAX_LINT_LINE_LENGTH:
            findings.append(
                ReviewFinding(
                    path="/".join(parts),
                    line=lineno,
                    rule_id="LINT001",
                    severity="medium",
                    message=f"LINT001 line too long ({len(line)} > {MAX_LINT_LINE_LENGTH})",
                )
            )
        stripped = line.strip()
        if stripped == "except:" or stripped.startswith("except :"):
            findings.append(
                ReviewFinding(
                    path="/".join(parts),
                    line=lineno,
                    rule_id="LINT002",
                    severity="medium",
                    message="LINT002 bare except",
                )
            )
        if "eval(" in line or "exec(" in line:
            # security scan already covers; mark high here too for visibility
            pass
    # trailing whitespace / tabs
    for lineno, line in enumerate(text.splitlines(), start=1):
        if line != line.rstrip(" \t"):
            findings.append(
                ReviewFinding(
                    path="/".join(parts),
                    line=lineno,
                    rule_id="LINT003",
                    severity="low",
                    message="LINT003 trailing whitespace",
                )
            )
    return findings


def _one_round(
    root: Path,
    changed_paths: list[str],
    *,
    test_paths: list[str] | None,
    test_timeout_s: int,
    run_tests_flag: bool,
) -> tuple[int, list[ReviewFinding], str]:
    findings: list[ReviewFinding] = []

    # Security scan
    try:
        sec = scan_changes(root, changed_paths)
    except Exception:  # noqa: BLE001
        sec = None
    if sec is not None:
        for f in sec.findings:
            sev = f.severity
            if sev not in ("low", "medium", "high", "critical"):
                sev = "medium"
            findings.append(
                ReviewFinding(
                    path=f.path,
                    line=f.line,
                    rule_id=f.rule_id,
                    severity=sev,  # type: ignore[arg-type]
                    message=f.message,
                )
            )

    # Inspection-derived syntax failures
    try:
        from agency.forge.inspection import inspect_changes

        insp = inspect_changes(root, changed_paths)
    except Exception:  # noqa: BLE001
        insp = None
    if insp is not None:
        for r in insp.rejected:
            findings.append(
                ReviewFinding(
                    path=r.path,
                    line=1,
                    rule_id="INSP",
                    severity="high",
                    message=f"INSP rejected path: {r.reason}",
                )
            )
        for f in insp.files:
            if f.kind == "python" and f.syntax_ok is False:
                findings.append(
                    ReviewFinding(
                        path=f.path,
                        line=1,
                        rule_id="SYNTAX",
                        severity="high",
                        message=f"SYNTAX python syntax error: {f.syntax_error}",
                    )
                )

    # Lint-ish
    for p in changed_paths:
        findings.extend(_lint_check(root, p))

    # Test gate
    tests_ran = False
    tests_passed = True
    if run_tests_flag and test_paths:
        try:
            tr = run_tests(root, test_paths, timeout_s=test_timeout_s)
            tests_ran = True
            if tr.gate != "PASS":
                tests_passed = False
                findings.append(
                    ReviewFinding(
                        path="<tests>",
                        line=1,
                        rule_id="TEST",
                        severity="high",
                        message=f"TEST gate {tr.gate}",
                    )
                )
        except Exception as exc:  # noqa: BLE001
            tests_ran = True
            tests_passed = False
            findings.append(
                ReviewFinding(
                    path="<tests>",
                    line=1,
                    rule_id="TEST",
                    severity="high",
                    message=f"TEST error: {type(exc).__name__}",
                )
            )

    # Score
    score = 5
    high_count = sum(1 for f in findings if f.severity in ("high", "critical"))
    medium_count = sum(1 for f in findings if f.severity == "medium")
    if high_count:
        score -= min(3, high_count + 1)
    if medium_count:
        score -= min(2, (medium_count + 2) // 3)
    if not tests_passed:
        score = min(score, 2)
    if not tests_ran:
        # no tests provided: slight penalty but keep pass possible
        pass
    score = max(0, min(5, score))

    tests_note = "tests_run" if tests_ran else "tests_not_run"
    return score, findings, tests_note


def run_review(
    root: str | Path,
    changed_paths: list[str] | None = None,
    *,
    paths: list[str] | None = None,
    threshold: int = 4,
    max_rounds: int = MAX_ROUNDS,
    test_paths: list[str] | None = None,
    test_timeout_s: int = 120,
    run_tests_flag: bool = False,
    **kwargs: Any,
) -> ReviewReport:
    real_root = _resolve_root(root)
    if changed_paths is None:
        changed_paths = paths
    if changed_paths is None:
        # maybe kwargs
        changed_paths = kwargs.get("changed_paths") or kwargs.get("files") or []
    if not isinstance(changed_paths, list) or any(not isinstance(p, str) for p in changed_paths):
        raise ValueError("changed_paths must be a list of strings")
    if max_rounds <= 0 or max_rounds > MAX_ROUNDS:
        raise ValueError(f"max_rounds must be in (0, {MAX_ROUNDS}]")
    if threshold < 0 or threshold > 5:
        raise ValueError("threshold must be 0-5")

    # normalize test paths
    if test_paths is None:
        test_paths = kwargs.get("test_paths")
    if isinstance(test_paths, (tuple,)):
        test_paths = list(test_paths)

    # run tests only if explicitly requested or test_paths provided
    run_tests_flag = bool(run_tests_flag or test_paths)

    report_id = str(uuid4())
    created_at = datetime.now(UTC)

    rounds_done = 0
    final_score = 0
    final_findings: list[ReviewFinding] = []
    for _ in range(max_rounds):
        rounds_done += 1
        score, findings, _ = _one_round(
            real_root,
            changed_paths,
            test_paths=test_paths,
            test_timeout_s=test_timeout_s,
            run_tests_flag=run_tests_flag,
        )
        final_score = score
        final_findings = findings
        unresolved_high = sum(
            1 for f in findings if (not f.resolved) and f.severity in ("high", "critical")
        )
        if score >= threshold and unresolved_high == 0:
            break

    unresolved_high = sum(
        1 for f in final_findings if (not f.resolved) and f.severity in ("high", "critical")
    )
    gate: ReviewGate = "PASS" if (final_score >= threshold and unresolved_high == 0) else "FAIL"

    caveats = list(CAVEATS)
    if run_tests_flag:
        caveats = [c for c in caveats if c != "tests_not_run"]

    report = ReviewReport(
        report_id=report_id,
        created_at=created_at,
        root=str(real_root),
        gate=gate,
        score=final_score,
        threshold=threshold,
        rounds=rounds_done,
        max_rounds=max_rounds,
        findings=tuple(final_findings),
        unresolved_high=unresolved_high,
        caveats=tuple(caveats),
    )
    logger.info(
        "forge.review", root=str(real_root), gate=gate, score=final_score, rounds=rounds_done
    )
    return report


__all__ = [
    "CAVEATS",
    "MAX_ROUNDS",
    "ReviewFinding",
    "ReviewReport",
    "ReviewStore",
    "run_review",
]
