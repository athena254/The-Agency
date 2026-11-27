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

from agency.forge.project_adapters import prepare_dependencies

logger = structlog.get_logger(__name__)

MAX_TEST_TIMEOUT_S = 300
MAX_OUTPUT_BYTES = 64 * 1024
MAX_REPO_BYTES = 50 * 1024 * 1024

_IGNORED_NAMES = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "ENV",
        "node_modules",
        "venv",
    }
)

TestGate = Literal["PASS", "FAIL", "ERROR", "TIMEOUT", "SKIP"]

Language = Literal["python", "node", "go", "rust"]

_MARKERS: tuple[tuple[str, Language], ...] = (
    ("pyproject.toml", "python"),
    ("package.json", "node"),
    ("go.mod", "go"),
    ("Cargo.toml", "rust"),
)


def detect_language(root: str | Path) -> Language:
    """Return language from manifest file; default to python (legacy)."""
    base = Path(root)
    for marker, language in _MARKERS:
        if (base / marker).is_file():
            return language
    return "python"


def build_test_command(
    language: str,
    test_paths: list[str],
    extra_args: list[str] | None = None,
) -> list[str] | None:
    """Return test command for language, or None if the tool is missing."""
    args = list(extra_args or [])
    if language == "node":
        npm = shutil.which("npm")
        if npm is None:
            return None
        return [npm, "test", *args]
    if language == "go":
        go = shutil.which("go")
        if go is None:
            return None
        return [go, "test", "./...", *args]
    if language == "rust":
        cargo = shutil.which("cargo")
        if cargo is None:
            return None
        return [cargo, "test", *args]
    return [sys.executable, "-m", "pytest", *test_paths, *args]


class TestReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    report_id: str = Field(min_length=1)
    created_at: datetime
    root: str
    workdir: str
    command: tuple[str, ...]
    gate: TestGate
    language: str = "python"
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
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [name for name in dirnames if name not in _IGNORED_NAMES]
        for name in filenames:
            if name.endswith(".pyc"):
                continue
            candidate = Path(dirpath) / name
            try:
                if candidate.is_file() and not candidate.is_symlink():
                    total += candidate.stat().st_size
            except OSError:
                continue
    return total


def _git_source_files(root: Path) -> list[str] | None:
    """Tracked plus untracked-but-not-ignored files, or None outside a git repo."""
    try:
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return [
        part.replace("\\", "/")
        for part in proc.stdout.decode("utf-8", "replace").split("\0")
        if part
    ]


def _copy_git_source(root: Path, workdir: Path, cap: int) -> tuple[int, int] | str:
    """Copy only source files into the isolated workdir, never caches or venvs."""
    files = _git_source_files(root)
    if files is None:
        return "not a git repository"
    total = 0
    copied = 0
    for relative in files:
        parts = relative.split("/")
        if not relative or relative.startswith("/") or ".." in parts or "." in parts:
            continue
        source = root.joinpath(*parts)
        target = workdir.joinpath(*parts)
        try:
            if not source.is_file() or source.is_symlink():
                continue
            size = source.stat().st_size
            if total + size > cap:
                return f"source tree exceeds size cap {cap} bytes"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            total += size
            copied += 1
        except OSError as exc:
            return f"copy failed: {type(exc).__name__}: {exc}"
    return copied, total


def _prepare_workdir(root: Path) -> tuple[Path, Path, str | None]:
    """Build the isolated test workdir; return (tmpdir, workdir, error_note)."""
    tmpdir = Path(tempfile.mkdtemp(prefix="forge-test-"))
    workdir = tmpdir / "repo"
    files = _git_source_files(root)
    if files is not None:
        result = _copy_git_source(root, workdir, MAX_REPO_BYTES)
        if isinstance(result, str):
            return tmpdir, workdir, result
        return tmpdir, workdir, None
    if _dir_size(root) > MAX_REPO_BYTES:
        return tmpdir, workdir, f"repository exceeds size cap {MAX_REPO_BYTES} bytes"
    try:
        shutil.copytree(
            root,
            workdir,
            symlinks=False,
            ignore=shutil.ignore_patterns(*sorted(_IGNORED_NAMES), "*.pyc"),
        )
    except Exception as exc:  # noqa: BLE001
        return tmpdir, workdir, f"copy failed: {type(exc).__name__}"
    return tmpdir, workdir, None


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
    language = detect_language(real_root)
    built = build_test_command(language, list(test_paths), extra_args)
    if built is None:
        tool = {"node": "npm", "go": "go", "rust": "cargo"}.get(language, language)
        return TestReport(
            report_id=report_id,
            created_at=created_at,
            root=str(real_root),
            workdir="<not-copied>",
            command=(tool,),
            gate="SKIP",
            language=language,
            duration_s=0.0,
            note=f"{tool} not available; tests skipped",
        )
    command = built

    tmpdir, workdir, copy_error = _prepare_workdir(real_root)
    if copy_error is None:
        adapter_note = prepare_dependencies(real_root, workdir, language)
        if adapter_note.startswith("node_modules junction failed"):
            copy_error = adapter_note
    if copy_error is not None:
        shutil.rmtree(tmpdir, ignore_errors=True)
        return TestReport(
            report_id=report_id,
            created_at=created_at,
            root=str(real_root),
            workdir="<not-copied>",
            command=tuple(command),
            gate="ERROR",
            language=language,
            duration_s=0.0,
            note=copy_error,
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
        raw_out: bytes | bytearray | str | None = exc.stdout
        raw_err: bytes | bytearray | str | None = exc.stderr
        if isinstance(raw_out, (bytes, bytearray)):
            raw_out = raw_out.decode("utf-8", errors="replace")
        if isinstance(raw_err, (bytes, bytearray)):
            raw_err = raw_err.decode("utf-8", errors="replace")
        stdout = (raw_out or "")[:MAX_OUTPUT_BYTES] if isinstance(raw_out, str) else ""
        stderr = (raw_err or "")[:MAX_OUTPUT_BYTES] if isinstance(raw_err, str) else ""
        gate = "TIMEOUT"
        rc = None
    except FileNotFoundError as exc:
        duration = time.monotonic() - start
        stdout = ""
        stderr = str(exc)[:MAX_OUTPUT_BYTES]
        gate = "SKIP"
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
        language=language,
        returncode=rc,
        duration_s=round(duration, 3),
        stdout=stdout,
        stderr=stderr,
    )
