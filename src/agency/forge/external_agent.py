"""Forge external coding-agent runner.

Orchestrates an external coding agent (opencode, claude code, codex, chimera,
...) inside an isolated git worktree, then runs the Forge inspection +
security (+ optional test) gates on whatever it changed.

The Forge itself still does not author code: the external agent does. Forge
owns isolation, change detection, and verification. Never auto-merges or
authorizes release.

Reliability hardening (Windows + CI):
- Bare executable names are resolved through ``PATH`` so ``.CMD``/``.BAT``
  shims (npm installs) launch instead of failing with ``WinError 2``.
- The agent runs with an explicit timeout and is killed as a process tree, so
  a hung agent cannot hold the worktree open or block the runner.
- Worktrees, and branches this runner created, are removed in a ``finally``
  block when ``cleanup=True``; a failed worktree create prunes stale metadata.
- Tracked lockfiles (``uv.lock``) and the original checkout are guarded: an
  agent that changes them fails the gate instead of passing silently.
- JSON serialization never goes through Rich, whose 80-column default wrap
  would corrupt machine-readable output when stdout is piped.
"""

from __future__ import annotations

import os
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Self, TextIO
from uuid import uuid4

import structlog
from pydantic import BaseModel, ConfigDict, Field

from agency.forge.inspection import InspectionReport, inspect_changes
from agency.forge.pipeline import CAVEATS as _PIPELINE_CAVEATS
from agency.forge.security_scan import SecurityReport, scan_changes
from agency.forge.test_runner import MAX_TEST_TIMEOUT_S, TestReport, run_tests
from agency.forge.worktree import WorktreeError, WorktreeInfo, create_worktree, remove_worktree

logger = structlog.get_logger(__name__)

MAX_STDIO_CHARS = 4000
MAX_CHANGED_PATHS = 256

DEFAULT_BRANCH = "forge-external-agent"
DEFAULT_TIMEOUT_S = 600
MAX_AGENT_TIMEOUT_S = 7_200
DEFAULT_TEST_TIMEOUT_S = 120
GIT_TIMEOUT_S = 30
KILL_GRACE_TIMEOUT_S = 15

PROTECTED_TRACKED_PATHS: tuple[str, ...] = ("uv.lock",)
"""Tracked files an external agent must not modify; the gate fails if it does."""

_WINDOWS_BATCH_SUFFIXES = frozenset({".bat", ".cmd"})
_IGNORED_CHANGED_PREFIXES: tuple[str, ...] = (
    ".venv/",
    "node_modules/",
    "__pycache__/",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
)

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
    protected_paths: tuple[str, ...] = ()
    main_repo_changes: tuple[str, ...] = ()
    stdout: str = ""
    stderr: str = ""
    inspection: InspectionReport
    security: SecurityReport
    tests: TestReport | None = None
    gate: ExternalAgentGate
    caveats: tuple[str, ...] = _BASE_CAVEATS
    note: str = "External coding agent run; Forge does not auto-merge or authorize release."


def render_report_json(report: ExternalAgentReport, *, indent: int | None = None) -> str:
    """Serialize *report* to JSON without Rich/terminal-width wrapping.

    Rich's ``Console`` hard-wraps at the terminal width (80 columns when stdout
    is piped), which corrupts JSON routed through it. This emits plain JSON so
    CI consumers parse it regardless of ``COLUMNS`` or tty state.
    """
    return report.model_dump_json(indent=indent)


def write_report_json(
    report: ExternalAgentReport, path: str | Path, *, indent: int | None = None
) -> Path:
    """Atomically write *report* as JSON to *path*; no Rich involvement."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f"{target.name}.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(render_report_json(report, indent=indent))
            handle.write("\n")
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return target


def emit_report_json(
    report: ExternalAgentReport,
    *,
    stream: TextIO | None = None,
    indent: int | None = None,
) -> None:
    """Write *report* JSON to *stream* (default stdout) without Rich wrapping."""
    out = sys.stdout if stream is None else stream
    out.write(render_report_json(report, indent=indent))
    out.write("\n")


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


def _decode_stream(value: bytes | bytearray | str | None) -> str:
    """Decode subprocess output defensively across platforms."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", errors="replace")
    return str(value)


def _is_windows() -> bool:
    return sys.platform == "win32"


def _resolve_executable(exe: str) -> str:
    """Resolve a bare executable name through ``PATH``.

    On Windows a bare name such as ``opencode`` is not found by
    ``CreateProcess`` even when an ``opencode.cmd`` shim is on ``PATH``;
    ``shutil.which`` applies ``PATHEXT`` and finds the shim.
    """
    if not exe or Path(exe).is_absolute():
        return exe
    return shutil.which(exe) or exe


def _is_batch_shim(exe: str) -> bool:
    """True for Windows ``.CMD``/``.BAT`` shims that may need ``cmd.exe``."""
    return _is_windows() and Path(exe).suffix.lower() in _WINDOWS_BATCH_SUFFIXES


def _spawn(cmd_list: list[str], cwd: str) -> subprocess.Popen[str]:
    """Start the agent in its own session/process group for tree kills."""
    kwargs: dict[str, Any] = {
        "cwd": cwd,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if _is_windows():
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(cmd_list, **kwargs)


def _kill_process_tree(proc: subprocess.Popen[str]) -> None:
    """Best-effort terminate of the agent and any children it spawned."""
    if _is_windows():
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=KILL_GRACE_TIMEOUT_S,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            pass
        return
    import signal

    # POSIX-only APIs are looked up dynamically so this module also type-checks
    # and imports on Windows.
    killpg = getattr(os, "killpg", None)
    getpgid = getattr(os, "getpgid", None)
    sigkill = getattr(signal, "SIGKILL", signal.SIGTERM)
    if killpg is not None and getpgid is not None:
        try:
            killpg(getpgid(proc.pid), sigkill)
        except OSError:
            pass
    try:
        proc.kill()
    except OSError:
        pass


def _run_agent(cmd_list: list[str], cwd: str, timeout_s: int) -> tuple[int | None, str, str]:
    """Run the agent with an explicit timeout and process-tree cleanup."""
    try:
        proc = _spawn(cmd_list, cwd)
    except (OSError, ValueError) as exc:
        if not _is_batch_shim(cmd_list[0]):
            return 127, "", f"failed to launch agent: {exc}"
        # Some Windows hosts cannot CreateProcess a .CMD/.BAT directly; retry
        # through cmd.exe. `call` handles shim paths containing spaces.
        fallback = ["cmd", "/c", "call", cmd_list[0], *cmd_list[1:]]
        try:
            proc = _spawn(fallback, cwd)
        except (OSError, ValueError) as fallback_exc:
            return 127, "", f"failed to launch agent: {fallback_exc}"

    timed_out = False
    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_process_tree(proc)
        stdout, stderr = proc.communicate()
    exit_code = 124 if timed_out else proc.returncode
    stderr_text = _decode_stream(stderr)
    if timed_out:
        stderr_text = (stderr_text + "\n[timeout]").strip()
    return exit_code, _decode_stream(stdout), stderr_text


def _changed_paths(worktree: Path) -> list[str]:
    """Return files changed relative to HEAD inside ``worktree``."""
    paths: set[str] = set()
    for git_args in (
        ["diff", "--name-only", "-z", "HEAD"],
        ["ls-files", "--others", "--exclude-standard", "-z"],
    ):
        try:
            out = subprocess.run(
                ["git", "-C", str(worktree), *git_args],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=GIT_TIMEOUT_S,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        for raw in out.stdout.split("\0"):
            path = raw.strip().replace("\\", "/")
            if not path or path.startswith(_IGNORED_CHANGED_PREFIXES):
                continue
            paths.add(path)
    return sorted(paths)[:MAX_CHANGED_PATHS]


def _tracked_changes(repo: Path) -> tuple[str, ...]:
    """Return tracked-file changes in ``repo``; untracked files are excluded."""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain", "-z", "--untracked-files=no"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return ()
    paths: set[str] = set()
    for chunk in out.stdout.split("\0"):
        if len(chunk) <= 3:
            continue
        path = chunk[3:].strip().replace("\\", "/")
        if path:
            paths.add(path)
    return tuple(sorted(paths))


def _branch_exists(repo: Path, branch: str) -> bool:
    """True if *branch* exists; defaults to True when git cannot answer."""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", branch],
            check=False,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return True
    return out.returncode == 0


def _prune_worktrees(repo: Path) -> None:
    """Best-effort removal of stale worktree metadata after a failed create."""
    try:
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "prune"],
            check=False,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def _remove_worktree_quiet(repo: Path, worktree_path: Path) -> bool:
    """Remove *worktree_path*, logging (not raising) on failure."""
    try:
        remove_worktree(repo, worktree_path)
    except WorktreeError as exc:
        logger.warning("forge.external_agent_cleanup_failed", error=str(exc))
        return False
    return True


def _delete_branch_quiet(repo: Path, branch: str) -> None:
    """Delete a branch this runner created; never raises."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "branch", "-D", branch],
            check=False,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("forge.external_agent_branch_delete_failed", branch=branch, error=str(exc))
        return
    if proc.returncode != 0:
        logger.warning(
            "forge.external_agent_branch_delete_failed",
            branch=branch,
            error=proc.stderr.strip(),
        )


def run_external_agent(
    repo_root: str | Path,
    command: str | list[str],
    task: str,
    *,
    branch: str | None = None,
    worktree_root: str | Path | None = None,
    base_ref: str = "HEAD",
    timeout_s: int = DEFAULT_TIMEOUT_S,
    run_tests_flag: bool = False,
    test_paths: list[str] | None = None,
    test_timeout_s: int = DEFAULT_TEST_TIMEOUT_S,
    cleanup: bool = False,
    protected_paths: Iterable[str] | None = None,
) -> ExternalAgentReport:
    """Run an external coding agent in an isolated worktree, then Forge gates."""
    real_repo = Path(repo_root)
    if not real_repo.is_absolute():
        raise ValueError(f"repo_root must be an absolute path, got {repo_root!r}")
    if not real_repo.is_dir():
        raise ValueError(f"repo_root must be an existing directory, got {repo_root!r}")
    if timeout_s < 1 or timeout_s > MAX_AGENT_TIMEOUT_S:
        raise ValueError(f"timeout_s must be in [1, {MAX_AGENT_TIMEOUT_S}], got {timeout_s}")
    if test_timeout_s < 1 or test_timeout_s > MAX_TEST_TIMEOUT_S:
        raise ValueError(
            f"test_timeout_s must be in [1, {MAX_TEST_TIMEOUT_S}], got {test_timeout_s}"
        )

    if branch is None or not str(branch).strip():
        branch = DEFAULT_BRANCH

    if isinstance(command, str):
        base_cmd = shlex.split(command)
    else:
        base_cmd = [str(x) for x in command]
    if not base_cmd:
        raise ValueError("command must not be empty")

    # Resolve a bare executable name to a full path on Windows (e.g. opencode.cmd).
    base_cmd[0] = _resolve_executable(base_cmd[0])
    has_task_placeholder = any("{task}" in str(x) for x in base_cmd)
    cmd_list = [str(x).replace("{task}", task) for x in base_cmd]
    if not has_task_placeholder:
        cmd_list = cmd_list + [task]

    protected = (
        tuple(str(p).replace("\\", "/") for p in protected_paths)
        if protected_paths is not None
        else PROTECTED_TRACKED_PATHS
    )

    report_id = str(uuid4())
    created_at = datetime.now(UTC)
    branch_preexisted = _branch_exists(real_repo, str(branch))

    try:
        info: WorktreeInfo = create_worktree(
            real_repo, str(branch), base_ref=base_ref, worktree_root=worktree_root
        )
    except WorktreeError as exc:
        _prune_worktrees(real_repo)
        raise ValueError(f"failed to create worktree: {exc}") from exc

    worktree_path = Path(info.worktree_path)
    # Baseline after worktree creation so its own git activity is not flagged.
    pre_repo_state = _tracked_changes(real_repo)
    cmd_list = [str(x).replace("{workdir}", str(worktree_path)) for x in cmd_list]

    try:
        exit_code, stdout, stderr = _run_agent(cmd_list, str(worktree_path), timeout_s)
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

        post_repo_state = _tracked_changes(real_repo)
        baseline = set(pre_repo_state)
        main_repo_changes = tuple(p for p in post_repo_state if p not in baseline)
        protected_hits = tuple(p for p in changed if p in protected)

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
        if protected_hits:
            overall = "FAIL"
            logger.warning(
                "forge.external_agent_protected_paths_changed",
                paths=list(protected_hits),
            )
        if main_repo_changes:
            overall = "FAIL"
            logger.warning(
                "forge.external_agent_main_repo_changed",
                paths=list(main_repo_changes),
            )

        caveats: list[str] = list(_BASE_CAVEATS)
        if protected_hits:
            caveats.append("protected_file_modified")
        if main_repo_changes:
            caveats.append("main_repo_modified")
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
            protected_paths=protected_hits,
            main_repo_changes=main_repo_changes,
            stdout=_truncate(stdout),
            stderr=_truncate(stderr),
            inspection=inspection,
            security=security,
            tests=tests,
            gate=overall,
            caveats=tuple(caveats),
            note="External coding agent run; Forge does not auto-merge or authorize release.",
        )

        logger.info(
            "forge.external_agent_done",
            root=str(worktree_path),
            changed=len(changed),
            gate=overall,
            exit_code=exit_code,
        )
        return report
    finally:
        if cleanup and _remove_worktree_quiet(real_repo, worktree_path) and not branch_preexisted:
            _delete_branch_quiet(real_repo, str(info.branch))


# Convenience aliases.
run_agent = run_external_agent
external = run_external_agent

__all__ = [
    "ExternalAgentGate",
    "ExternalAgentReport",
    "ExternalAgentStore",
    "emit_report_json",
    "external",
    "render_report_json",
    "run_agent",
    "run_external_agent",
    "write_report_json",
]
