"""Forge v3 holdout runner: named scenarios in isolated temp copies.

Runs a list of named scenarios (each: name + command) in an isolated
temporary copy of the repository, with timeout and output caps. Records
per-scenario pass/fail + output. Overall gate PASS only if all scenarios pass.

This is NOT a security review and does NOT authorize merge or deploy.
"""

from __future__ import annotations

import os
import shlex
import shutil
import sqlite3
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
    _resolve_root,
)

logger = structlog.get_logger(__name__)

MAX_OUTPUT_BYTES = 64 * 1024
MAX_HOLDOUT_TIMEOUT_S = 300
MAX_REPO_BYTES = 50 * 1024 * 1024
MAX_SCENARIOS = 64

HoldoutGate = Literal["PASS", "FAIL"]
ScenarioGate = Literal["PASS", "FAIL", "TIMEOUT", "ERROR"]

CAVEATS: tuple[str, ...] = (
    "holdout_not_a_security_review",
    "not_a_security_review",
    "holdout_capture_only",
    "scenario_proof_not_certification",
    "release_not_authorized",
)


class HoldoutScenarioResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    command: tuple[str, ...] = ()
    gate: ScenarioGate
    returncode: int | None = None
    duration_s: float = Field(default=0.0, ge=0)
    stdout: str = ""
    stderr: str = ""


ScenarioResult = HoldoutScenarioResult


class HoldoutScenario(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    command: tuple[str, ...] = ()


class HoldoutReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    report_id: str = Field(min_length=1)
    created_at: datetime
    root: str
    gate: HoldoutGate
    scenarios: tuple[HoldoutScenarioResult, ...] = ()
    caveats: tuple[str, ...] = CAVEATS
    note: str = (
        "Bounded holdout scenario run; not a security review; does not authorize merge or deploy."
    )


_SCHEMA = """
CREATE TABLE IF NOT EXISTS forge_holdout_reports (
    report_id  TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    gate       TEXT NOT NULL,
    document   TEXT NOT NULL
);
"""


class HoldoutStore:
    def __init__(self, db_path: str | Path = "forge_holdout.db") -> None:
        self._db_path = Path(db_path)
        if str(self._db_path) != ":memory:":
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def save(self, report: HoldoutReport) -> str:
        self._conn.execute(
            "INSERT INTO forge_holdout_reports (report_id, created_at, gate, document) "
            "VALUES (?, ?, ?, ?)",
            (
                report.report_id,
                report.created_at.isoformat(),
                report.gate,
                report.model_dump_json(),
            ),
        )
        self._conn.commit()
        logger.info("forge.holdout_saved", report_id=report.report_id, gate=report.gate)
        return report.report_id

    def load(self, report_id: str) -> HoldoutReport | None:
        cursor = self._conn.execute(
            "SELECT document FROM forge_holdout_reports WHERE report_id = ?", (report_id,)
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return HoldoutReport.model_validate_json(row[0])

    def close(self) -> None:
        self._conn.commit()
        self._conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


HoldoutReportStore = HoldoutStore


def _dir_size(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file() and not p.is_symlink():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def _normalize_command(command: Any) -> list[str]:
    if isinstance(command, str):
        return shlex.split(command)
    if isinstance(command, (list, tuple)):
        return [str(x) for x in command]
    return [str(command)]


def _normalize_scenario(item: Any) -> tuple[str, list[str]]:
    if isinstance(item, HoldoutScenario):
        return item.name, list(item.command)
    if isinstance(item, dict):
        name = item.get("name") or item.get("scenario") or "scenario"
        cmd = item.get("command") or item.get("cmd") or item.get("commands") or []
        return str(name), _normalize_command(cmd)
    if isinstance(item, (list, tuple)):
        if len(item) == 0:
            raise ValueError("scenario must have a name")
        if len(item) == 1:
            return str(item[0]), []
        if len(item) == 2 and isinstance(item[1], (list, tuple, str)):
            return str(item[0]), _normalize_command(item[1])
        return str(item[0]), [str(x) for x in item[1:]]
    if hasattr(item, "name") and hasattr(item, "command"):
        return str(item.name), _normalize_command(item.command)
    raise ValueError(f"unsupported scenario: {item!r}")


def _run_one(root: Path, name: str, cmd: list[str], timeout_s: int) -> HoldoutScenarioResult:
    if _dir_size(root) > MAX_REPO_BYTES:
        return HoldoutScenarioResult(
            name=name, command=tuple(cmd), gate="ERROR", stderr="repository exceeds size cap"
        )
    tmpdir = Path(tempfile.mkdtemp(prefix="forge-holdout-"))
    workdir = tmpdir / "repo"
    try:
        shutil.copytree(
            root,
            workdir,
            symlinks=False,
            ignore=shutil.ignore_patterns("__pycache__", ".git", ".pytest_cache", "*.pyc"),
        )
    except Exception as exc:  # noqa: BLE001
        shutil.rmtree(tmpdir, ignore_errors=True)
        return HoldoutScenarioResult(
            name=name, command=tuple(cmd), gate="ERROR", stderr=f"copy failed: {type(exc).__name__}"
        )

    stdout = ""
    stderr = ""
    returncode: int | None = None
    duration = 0.0
    gate: ScenarioGate = "PASS"
    start = time.monotonic()
    try:
        env = dict(os.environ)
        src_dir = workdir / "src"
        if src_dir.is_dir():
            env["PYTHONPATH"] = str(src_dir) + (
                os.pathsep + env.get("PYTHONPATH", "") if env.get("PYTHONPATH") else ""
            )
        start = time.monotonic()
        proc = subprocess.run(
            cmd,
            cwd=str(workdir),
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
        gate = "PASS" if proc.returncode == 0 else "FAIL"
    except subprocess.TimeoutExpired as exc:
        duration = time.monotonic() - start
        raw_out: bytes | bytearray | str | None = exc.stdout
        raw_err: bytes | bytearray | str | None = exc.stderr
        if isinstance(raw_out, (bytes, bytearray)):
            raw_out = raw_out.decode("utf-8", errors="replace")
        if isinstance(raw_err, (bytes, bytearray)):
            raw_err = raw_err.decode("utf-8", errors="replace")
        stdout = (raw_out or "")[:MAX_OUTPUT_BYTES] if isinstance(raw_out, str) else ""
        stderr = (raw_err or "")[:MAX_OUTPUT_BYTES] if isinstance(raw_err, str) else ""
        gate = "TIMEOUT"
        returncode = None
    except Exception as exc:  # noqa: BLE001
        duration = time.monotonic() - start
        stderr = str(exc)[:MAX_OUTPUT_BYTES]
        gate = "ERROR"
        returncode = None
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    return HoldoutScenarioResult(
        name=name,
        command=tuple(cmd),
        gate=gate,
        returncode=returncode,
        duration_s=round(duration, 3),
        stdout=stdout,
        stderr=stderr,
    )


def run_holdout(
    root: str | Path,
    scenarios: Any,
    *,
    timeout_s: int = 120,
) -> HoldoutReport:
    real_root = _resolve_root(root)
    if not isinstance(scenarios, (list, tuple)):
        raise ValueError("scenarios must be a list")  # noqa: TRY004
    if len(scenarios) > MAX_SCENARIOS:
        raise ValueError(f"too many scenarios (max {MAX_SCENARIOS})")
    if timeout_s <= 0 or timeout_s > MAX_HOLDOUT_TIMEOUT_S:
        raise ValueError(f"timeout_s must be in (0, {MAX_HOLDOUT_TIMEOUT_S}]")

    report_id = str(uuid4())
    created_at = datetime.now(UTC)
    results: list[HoldoutScenarioResult] = []
    for item in scenarios:
        name, cmd = _normalize_scenario(item)
        results.append(_run_one(real_root, name, cmd, timeout_s))

    overall: HoldoutGate = "PASS" if all(r.gate == "PASS" for r in results) else "FAIL"
    # empty scenarios? overall PASS? Usually no scenarios -> PASS? Keep PASS.
    logger.info("forge.holdout", root=str(real_root), gate=overall, n=len(results))
    return HoldoutReport(
        report_id=report_id,
        created_at=created_at,
        root=str(real_root),
        gate=overall,
        scenarios=tuple(results),
        caveats=CAVEATS,
    )


run_holdout_scenarios = run_holdout
holdout_run = run_holdout
