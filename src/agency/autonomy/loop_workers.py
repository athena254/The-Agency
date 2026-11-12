"""Loop workers: prompt building and adapters that run inside the worktree.

The driver owns the loop, the isolated worktree, checkpoint/reset/commit, and
the gates. This module owns the workers that run *inside* that worktree and
report the paths they changed:

* :func:`build_task_prompt` renders a deterministic prompt from a
  :class:`~agency.autonomy.loop_state.LoopItem` (plus retry feedback).
* :class:`ExternalWorker` runs an operator-configured CLI in the worktree,
  feeds it the prompt on stdin, kills the process tree on timeout, and
  derives changed paths from git.
* :class:`EditingWorker` applies a deterministic list of file edits supplied
  by a provider callable (no subprocess).
* Preset factories (:func:`OpencodeWorker`, :func:`ClaudeCodeWorker`,
  :func:`CodexWorker`, :func:`PiWorker`, :func:`CursorWorker`) return
  :class:`ExternalWorker` instances with sensible default commands; the
  binaries must still be on ``PATH``.

Security: only operator-configured commands are executed. A worker never
authorizes release; the driver's gates remain the authority.
"""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import structlog

from agency.autonomy.loop_driver import WorkerGate, WorkerResult
from agency.autonomy.loop_state import LoopItem
from agency.forge.coding_agent import _normalize_op
from agency.forge.external_agent import (
    _is_batch_shim,
    _is_windows,
    _kill_process_tree,
    _resolve_executable,
)

logger = structlog.get_logger(__name__)

MIN_TIMEOUT_S = 1
MAX_TIMEOUT_S = 7200
MAX_STDIO_CHARS = 64 * 1024
GIT_TIMEOUT_S = 30

EditProvider = Callable[[LoopItem, Path, str], list[dict[str, Any]]]


def build_task_prompt(item: LoopItem, feedback: str) -> str:
    """Render the deterministic prompt for one loop item.

    The prompt carries the goal (item title), the description, and the
    acceptance criteria (test paths or holdout scenarios). Previous-attempt
    feedback is appended only when it is non-empty.
    """
    lines = [
        f"Goal: {item.title}",
        "",
        "Description:",
        item.description or "(none)",
        "",
        "Acceptance criteria:",
    ]
    if item.acceptance:
        lines.extend(f"- {criterion}" for criterion in item.acceptance)
    else:
        lines.append("(none provided)")
    if feedback:
        lines.extend(["", "Previous attempt feedback:", feedback])
    return "\n".join(lines) + "\n"


def _truncate(text: str, limit: int = MAX_STDIO_CHARS) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    return text[:half] + "\n...[truncated]...\n" + text[-half:]


def _spawn_agent(cmd: list[str], cwd: str, env: dict[str, str] | None) -> subprocess.Popen[str]:
    """Start the CLI in its own session/process group so it can be tree-killed."""
    kwargs: dict[str, Any] = {
        "cwd": cwd,
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "env": env,
    }
    if _is_windows():
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(cmd, **kwargs)


def _changed_paths(worktree: Path) -> tuple[str, ...]:
    """Return tracked + untracked paths changed relative to HEAD, sorted."""
    paths: set[str] = set()
    for git_args in (
        ["diff", "--name-only", "HEAD"],
        ["ls-files", "--others", "--exclude-standard"],
    ):
        try:
            proc = subprocess.run(
                ["git", "-C", str(worktree), *git_args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=GIT_TIMEOUT_S,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        for line in proc.stdout.splitlines():
            path = line.strip().replace("\\", "/")
            if path:
                paths.add(path)
    return tuple(sorted(paths))


class ExternalWorker:
    """Run an operator-configured CLI inside the driver's worktree.

    The command runs with ``cwd=worktree`` and receives
    :func:`build_task_prompt` on stdin. stdout/stderr are capped at
    ``MAX_STDIO_CHARS``. ``timeout_s`` is clamped to ``[1, 7200]``; on expiry
    the whole process tree is killed. ``changed_paths`` comes from git, and
    the gate is ``PASS`` only when the command exits 0 and changed something.
    """

    def __init__(
        self,
        command: list[str],
        *,
        timeout_s: int = MAX_TIMEOUT_S,
        env: dict[str, str] | None = None,
        cost_usd: float = 0.0,
    ) -> None:
        if not command or not str(command[0]).strip():
            raise ValueError("command must be a non-empty argv")
        self.command = tuple(str(part) for part in command)
        self.timeout_s = max(MIN_TIMEOUT_S, min(int(timeout_s), MAX_TIMEOUT_S))
        self.env = env
        self.cost_usd = float(cost_usd)
        self.name = Path(self.command[0]).name

    def run(self, item: LoopItem, worktree: Path, feedback: str) -> WorkerResult:
        start = time.monotonic()
        prompt = build_task_prompt(item, feedback)
        cmd = list(self.command)
        cmd[0] = _resolve_executable(cmd[0])
        child_env = None if self.env is None else {**os.environ, **self.env}
        root = Path(worktree)

        try:
            proc = _spawn_agent(cmd, str(root), child_env)
        except (OSError, ValueError) as exc:
            if not _is_batch_shim(cmd[0]):
                return self._result(
                    gate="FAIL",
                    changed_paths=(),
                    detail=f"failed to launch agent: {exc}",
                    start=start,
                )
            # Some Windows hosts cannot CreateProcess a .CMD/.BAT directly.
            fallback = ["cmd", "/c", "call", cmd[0], *cmd[1:]]
            try:
                proc = _spawn_agent(fallback, str(root), child_env)
            except (OSError, ValueError) as fallback_exc:
                return self._result(
                    gate="FAIL",
                    changed_paths=(),
                    detail=f"failed to launch agent: {fallback_exc}",
                    start=start,
                )

        timed_out = False
        try:
            stdout, stderr = proc.communicate(input=prompt, timeout=self.timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_process_tree(proc)
            stdout, stderr = proc.communicate()

        exit_code = 124 if timed_out else proc.returncode
        changed = _changed_paths(root)
        detail = f"exit_code={exit_code}"
        if timed_out:
            detail += "\ntimeout"
        if stderr:
            detail += f"\nstderr:\n{_truncate(stderr)}"
        if stdout:
            detail += f"\nstdout:\n{_truncate(stdout)}"
        gate: WorkerGate = "PASS" if exit_code == 0 and bool(changed) else "FAIL"
        return self._result(
            gate=gate,
            changed_paths=changed,
            detail=detail,
            start=start,
        )

    def _result(
        self,
        *,
        gate: WorkerGate,
        changed_paths: tuple[str, ...],
        detail: str,
        start: float,
    ) -> WorkerResult:
        result = WorkerResult(
            gate=gate,
            changed_paths=changed_paths,
            detail=detail,
            cost_usd=self.cost_usd,
            duration_s=time.monotonic() - start,
        )
        logger.info(
            "loop.worker_done",
            worker=self.name,
            gate=result.gate,
            changed=len(result.changed_paths),
            duration_s=round(result.duration_s, 3),
        )
        return result


def OpencodeWorker(model: str | None = None, **kw: Any) -> ExternalWorker:
    """opencode CLI preset.

    Assumed flags: ``run --auto --dir .`` plus an optional ``--model``.
    ``--dir .`` is intentional because the worker's cwd is already the
    isolated worktree. The prompt is fed on stdin.
    """
    command = ["opencode", "run", "--auto", "--dir", "."]
    if model is not None:
        command += ["--model", model]
    return ExternalWorker(command, **kw)


def ClaudeCodeWorker(**kw: Any) -> ExternalWorker:
    """Claude Code preset: ``claude -p --output-format text``.

    ``-p`` is print mode; the task prompt is fed on stdin.
    """
    return ExternalWorker(["claude", "-p", "--output-format", "text"], **kw)


def CodexWorker(**kw: Any) -> ExternalWorker:
    """Codex CLI preset: ``codex --full-auto -``.

    Best-effort: ``-`` reads the task prompt from stdin, but exact flag
    support depends on the installed codex version.
    """
    return ExternalWorker(["codex", "--full-auto", "-"], **kw)


def PiWorker(**kw: Any) -> ExternalWorker:
    """Pi preset: ``pi --prompt``.

    Best-effort: the repo has no authoritative Pi CLI docs, so the flag is
    assumed to read the task prompt from stdin. Verify against the installed
    Pi version before relying on it.
    """
    return ExternalWorker(["pi", "--prompt"], **kw)


def CursorWorker(**kw: Any) -> ExternalWorker:
    """Cursor Agent preset: ``cursor-agent -p``.

    Best-effort: ``-p`` is assumed to take the task prompt on stdin; verify
    against the installed cursor-agent version.
    """
    return ExternalWorker(["cursor-agent", "-p"], **kw)


def _edit_target(worktree: Path, raw_path: str) -> tuple[Path, str]:
    """Resolve an edit path inside *worktree*; reject traversal."""
    normalized = raw_path.replace("\\", "/")
    parts = [part for part in normalized.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        raise ValueError(f"invalid edit path: {raw_path!r}")
    return worktree.joinpath(*parts), "/".join(parts)


def _apply_edit(worktree: Path, op: str, raw_path: str, old: str, new: str) -> str:
    """Apply one normalized edit; return the display path touched."""
    target, display = _edit_target(worktree, raw_path)
    if op in ("create_file", "write_file"):
        if op == "create_file" and target.exists():
            raise ValueError(f"create_file on existing path: {display}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(new, encoding="utf-8")
        return display
    if not target.is_file():
        raise ValueError(f"patch target is missing: {display}")
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise ValueError(f"patch old occurs {count} times in {display} (expected exactly 1)")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")
    return display


class EditingWorker:
    """Apply deterministic file edits from a provider; no subprocess.

    ``edits_provider(item, worktree, feedback)`` returns a list of edit
    dicts. Supported ops: ``create_file`` (path/content), ``write_file``
    (path/content), and ``patch`` (path/old/new, where ``old`` must occur
    exactly once). A patch with a non-single occurrence or a ``create_file``
    on an existing path raises :class:`ValueError`.
    """

    def __init__(self, edits_provider: EditProvider, *, name: str = "editing") -> None:
        self.edits_provider = edits_provider
        self.name = name

    def run(self, item: LoopItem, worktree: Path, feedback: str) -> WorkerResult:
        start = time.monotonic()
        root = Path(worktree)
        edits = self.edits_provider(item, root, feedback)
        touched: list[str] = []
        for edit in edits:
            parsed = _normalize_op(edit)
            if parsed is None:
                raise ValueError(f"invalid edit shape: {edit!r}")
            op, raw_path, old, new = parsed
            display = _apply_edit(root, op, raw_path, old, new)
            if display not in touched:
                touched.append(display)
        changed = tuple(sorted(touched))
        result = WorkerResult(
            gate="PASS",
            changed_paths=changed,
            detail=f"applied {len(edits)} edit(s)",
            duration_s=time.monotonic() - start,
        )
        logger.info(
            "loop.worker_done",
            worker=self.name,
            gate=result.gate,
            changed=len(result.changed_paths),
            duration_s=round(result.duration_s, 3),
        )
        return result


class NoopWorker:
    """Worker that changes nothing; useful for tests and dry runs."""

    name = "noop"

    def run(self, item: LoopItem, worktree: Path, feedback: str) -> WorkerResult:
        return WorkerResult(gate="PASS", changed_paths=(), detail="noop")


__all__ = [
    "ClaudeCodeWorker",
    "CodexWorker",
    "CursorWorker",
    "EditProvider",
    "EditingWorker",
    "ExternalWorker",
    "NoopWorker",
    "OpencodeWorker",
    "PiWorker",
    "build_task_prompt",
]
