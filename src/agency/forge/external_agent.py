"""Forge external coding-agent runner.

Orchestrates an external coding agent (opencode, claude code, codex, chimera,
...) inside an isolated git worktree, then runs the Forge inspection +
security (+ optional test) gates on whatever it changed.

The Forge itself still does not author code: the external agent does. Forge
owns isolation, change detection, and verification. Never auto-merges or
authorizes release.
"""

from __future__ import annotations

import shlex
import shutil
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Self
from uuid import uuid4

import structlog
from pydantic import BaseModel, ConfigDict, Field

from agency.forge.inspection import InspectionReport, inspect_changes
from agency.forge.pipeline import CAVEATS as _PIPELINE_CAVEATS
from agency.forge.security_scan import SecurityReport, scan_changes
from agency.forge.test_runner import TestReport, run_tests
from agency.forge.worktree import WorktreeError, WorktreeInfo, create_worktree, remove_worktree

logger = structlog.get_logger(__name__)

MAX_STDIO_CHARS = 4000
MAX_CHANGED_PATHS = 256

ExternalAgentGate = Literal["PASS", "FAIL"]

_BASE_CAVEATS = ("agent_output_unverified", *_PIPELINE_CAVEATS)


class ExternalAgentReport(BaseModel):
    """Immutable outcome of one external-agent run + Forge gates."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    report_id: str = Field(min_length=1)
    created_at: datetime
    worktree_path: str = Field(min_length=1)
    branch: str = Field(min_length=1)
    command: tuple[str, ...] = ()
    task: str = ""
    exit_code: int | None = None
    changed_paths: tuple[str, ...] = ()
    stdout: str = ""
    stderr: str = ""
    inspection: InspectionReport
    security: SecurityReport
    tests: TestReport | None = None
    gate: ExternalAgentGate
    caveats: tuple[str, ...] = _BASE_CAVEATS
    note: str = "External coding agent run; Forge does not auto-merge or authorize release."


_SCHEMA = """
CREATE TABLE IF NOT EXISTS forge_external_agent_reports (
    report_id  TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    gate       TEXT NOT NULL,
    document   TEXT NOT NULL
);
"""


class ExternalAgentStore:
    """Synchronous SQLite persistence for :class:`ExternalAgentReport`."""

    def __init__(self, db_path: str | Path = "forge_external_agent.db") -> None:
        self._db_path = Path(db_path)
        if str(self._db_path) != ":memory:":
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def save(self, report: ExternalAgentReport) -> str:
        self._conn.execute(
            "INSERT INTO forge_external_agent_reports (report_id, created_at, gate, document) "
            "VALUES (?, ?, ?, ?)",
            (
                report.report_id,
                report.created_at.isoformat(),
                report.gate,
                report.model_dump_json(),
            ),
        )
        self._conn.commit()
        logger.info("forge.external_agent_saved", report_id=report.report_id, gate=report.gate)
        return report.report_id

    def load(self, report_id: str) -> ExternalAgentReport | None:
        cursor = self._conn.execute(
            "SELECT document FROM forge_external_agent_reports WHERE report_id = ?",
            (report_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return ExternalAgentReport.model_validate_json(row[0])

    def close(self) -> None:
        self._conn.commit()
        self._conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def _truncate(text: str, limit: int = MAX_STDIO_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[: limit // 2] + "\n...[truncated]...\n" + text[-limit // 2 :]


def _changed_paths(worktree: Path) -> list[str]:
    """Return files changed relative to HEAD inside ``worktree``."""
    paths: set[str] = set()
    try:
        out = subprocess.run(
            ["git", "-C", str(worktree), "diff", "--name-only", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        for line in out.stdout.splitlines():
            line = line.strip()
            if line:
                paths.add(line.replace("\\", "/"))
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        out = subprocess.run(
            ["git", "-C", str(worktree), "ls-files", "--others", "--exclude-standard"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        for line in out.stdout.splitlines():
            line = line.strip()
            if line:
                paths.add(line.replace("\\", "/"))
    except (OSError, subprocess.SubprocessError):
        pass
    return sorted(paths)[:MAX_CHANGED_PATHS]


def run_external_agent(
    repo_root: str | Path,
    command: str | list[str],
    task: str,
    *,
    branch: str | None = None,
    worktree_root: str | Path | None = None,
    base_ref: str = "HEAD",
    timeout_s: int = 600,
    run_tests_flag: bool = False,
    test_paths: list[str] | None = None,
    test_timeout_s: int = 120,
    cleanup: bool = False,
) -> ExternalAgentReport:
    """Run an external coding agent in an isolated worktree, then Forge gates."""
    real_repo = Path(repo_root)
    if not real_repo.is_absolute():
        raise ValueError(f"repo_root must be an absolute path, got {repo_root!r}")
    if not real_repo.is_dir():
        raise ValueError(f"repo_root must be an existing directory, got {repo_root!r}")

    if branch is None or not str(branch).strip():
        branch = "forge-external-agent"

    if isinstance(command, str):
        base_cmd = shlex.split(command)
    else:
        base_cmd = [str(x) for x in command]

    # Resolve a bare executable name to a full path on Windows (e.g. opencode.cmd).
    if base_cmd and not Path(base_cmd[0]).is_absolute():
        resolved = shutil.which(base_cmd[0])
        if resolved:
            base_cmd[0] = resolved
    has_task_placeholder = any("{task}" in str(x) for x in base_cmd)
    cmd_list = [str(x).replace("{task}", task) for x in base_cmd]
    if not has_task_placeholder:
        cmd_list = cmd_list + [task]

    report_id = str(uuid4())
    created_at = datetime.now(UTC)

    try:
        info: WorktreeInfo = create_worktree(
            real_repo, str(branch), base_ref=base_ref, worktree_root=worktree_root
        )
    except WorktreeError as exc:
        raise ValueError(f"failed to create worktree: {exc}") from exc

    worktree_path = Path(info.worktree_path)
    cmd_list = [str(x).replace("{workdir}", str(worktree_path)) for x in cmd_list]
    exit_code: int | None = None
    stdout = ""
    stderr = ""
    try:
        proc = subprocess.run(
            cmd_list,
            cwd=str(worktree_path),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        exit_code = proc.returncode
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
    except subprocess.TimeoutExpired as exc:
        exit_code = 124
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        stderr = (stderr + "\n[timeout]").strip()
    except (OSError, ValueError) as exc:
        exit_code = 127
        stderr = f"failed to launch agent: {exc}"

    changed = _changed_paths(worktree_path)

    try:
        inspection = inspect_changes(worktree_path, changed)
    except Exception as exc:  # noqa: BLE001
        logger.error("forge.external_agent_inspect_error", error=str(exc))
        inspection = inspect_changes(worktree_path, [])

    try:
        security = scan_changes(worktree_path, changed)
    except Exception as exc:  # noqa: BLE001
        logger.error("forge.external_agent_scan_error", error=str(exc))
        security = scan_changes(worktree_path, [])

    tests: TestReport | None = None
    if run_tests_flag:
        tp = test_paths or []
        try:
            tests = run_tests(worktree_path, tp, timeout_s=test_timeout_s)
        except Exception as exc:  # noqa: BLE001
            logger.error("forge.external_agent_tests_error", error=str(exc))
            tests = None

    overall: ExternalAgentGate = "PASS"
    if exit_code not in (0, None):
        overall = "FAIL"
    if not changed:
        overall = "FAIL"
    if inspection.gate != "PASS":
        overall = "FAIL"
    if security.gate != "PASS":
        overall = "FAIL"
    if tests is not None and tests.gate != "PASS":
        overall = "FAIL"

    caveats = list(_BASE_CAVEATS)
    if tests is not None:
        caveats = [c for c in caveats if c != "tests_not_run"]
    caveats = [c for c in caveats if c != "security_review_not_run"]

    report = ExternalAgentReport(
        report_id=report_id,
        created_at=created_at,
        worktree_path=str(worktree_path),
        branch=str(info.branch),
        command=tuple(cmd_list),
        task=task,
        exit_code=exit_code,
        changed_paths=tuple(changed),
        stdout=_truncate(stdout),
        stderr=_truncate(stderr),
        inspection=inspection,
        security=security,
        tests=tests,
        gate=overall,
        caveats=tuple(caveats),
        note="External coding agent run; Forge does not auto-merge or authorize release.",
    )

    if cleanup:
        try:
            remove_worktree(real_repo, worktree_path)
        except WorktreeError as exc:
            logger.warning("forge.external_agent_cleanup_failed", error=str(exc))

    logger.info(
        "forge.external_agent_done",
        root=str(worktree_path),
        changed=len(changed),
        gate=overall,
        exit_code=exit_code,
    )
    return report


# Convenience aliases.
run_agent = run_external_agent
external = run_external_agent

__all__ = [
    "ExternalAgentGate",
    "ExternalAgentReport",
    "ExternalAgentStore",
    "external",
    "run_agent",
    "run_external_agent",
]
