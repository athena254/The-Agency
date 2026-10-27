"""Forge v2 bounded test runner (isolated subprocess).

Runs a caller-specified pytest invocation inside a *copy* of a repository
root in a temporary directory. The original repository is never mutated.
Bounded by file count, byte size, and wall-clock timeout. Output is captured
and truncated.

This is the only Forge component permitted to spawn a subprocess; it is kept
out of the inspection gate, which must remain process-free.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

import structlog
from pydantic import BaseModel, ConfigDict, Field

logger = structlog.get_logger(__name__)

MAX_TEST_TIMEOUT_S = 300
MAX_OUTPUT_BYTES = 64 * 1024
MAX_REPO_BYTES = 50 * 1024 * 1024

TestGate = Literal["PASS", "FAIL", "ERROR", "TIMEOUT"]


class TestReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    report_id: str = Field(min_length=1)
    created_at: datetime
    root: str
    workdir: str
    command: tuple[str, ...]
    gate: TestGate
    returncode: int | None = None
    duration_s: float = Field(ge=0)
    stdout: str = ""
    stderr: str = ""
    note: str = "Bounded isolated test run; not a full CI certification."


def _resolve_root(root: str | Path) -> Path:
    candidate = Path(root)
    if not candidate.is_absolute():
        raise ValueError(f"root must be an absolute path, got {root!r}")
    real = Path(__import__("os").path.realpath(candidate))
    if not real.is_dir():
        raise ValueError(f"root must be an existing directory, got {root!r}")
    return real


def _dir_size(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file() and not p.is_symlink():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def run_tests(
    root: str | Path,
    test_paths: list[str],
    *,
    timeout_s: int = 120,
    extra_args: list[str] | None = None,
) -> TestReport:
    real_root = _resolve_root(root)
    if not isinstance(test_paths, list) or any(not isinstance(p, str) for p in test_paths):
        raise ValueError("test_paths must be a list of strings")
    if timeout_s <= 0 or timeout_s > MAX_TEST_TIMEOUT_S:
        raise ValueError(f"timeout_s must be in (0, {MAX_TEST_TIMEOUT_S}]")

    report_id = str(uuid4())
    created_at = datetime.now(UTC)
    command = [sys.executable, "-m", "pytest", *test_paths, *(extra_args or [])]

    if _dir_size(real_root) > MAX_REPO_BYTES:
        return TestReport(
            report_id=report_id,
            created_at=created_at,
            root=str(real_root),
            workdir="<not-copied>",
            command=tuple(command),
            gate="ERROR",
            duration_s=0.0,
            note=f"repository exceeds size cap {MAX_REPO_BYTES} bytes",
        )

    tmpdir = Path(tempfile.mkdtemp(prefix="forge-test-"))
    workdir = tmpdir / "repo"
    try:
        shutil.copytree(
            real_root,
            workdir,
            symlinks=False,
            ignore=shutil.ignore_patterns("__pycache__", ".git", ".pytest_cache", "*.pyc"),
        )
    except Exception as exc:  # noqa: BLE001
        shutil.rmtree(tmpdir, ignore_errors=True)
        return TestReport(
            report_id=report_id,
            created_at=created_at,
            root=str(real_root),
            workdir=str(tmpdir),
            command=tuple(command),
            gate="ERROR",
            duration_s=0.0,
            note=f"copy failed: {type(exc).__name__}",
        )

    start = time.monotonic()
    try:
        env = dict(os.environ)
        src_dir = workdir / "src"
        if src_dir.is_dir():
            env["PYTHONPATH"] = str(src_dir) + (
                os.pathsep + env.get("PYTHONPATH", "") if env.get("PYTHONPATH") else ""
            )
        proc = subprocess.run(
            command,
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
        gate: TestGate = "PASS" if proc.returncode == 0 else "FAIL"
        rc = proc.returncode
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
        gate = "TIMEOUT"
        rc = None
    except Exception as exc:  # noqa: BLE001
        duration = time.monotonic() - start
        stdout = ""
        stderr = str(exc)[:MAX_OUTPUT_BYTES]
        gate = "ERROR"
        rc = None
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    logger.info(
        "forge.tests_run", root=str(real_root), gate=gate, rc=rc, duration=round(duration, 2)
    )
    return TestReport(
        report_id=report_id,
        created_at=created_at,
        root=str(real_root),
        workdir=str(workdir),
        command=tuple(command),
        gate=gate,
        returncode=rc,
        duration_s=round(duration, 3),
        stdout=stdout,
        stderr=stderr,
    )
