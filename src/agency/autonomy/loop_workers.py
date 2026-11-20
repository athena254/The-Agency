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
import re
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
FAILURE_TAIL_CHARS = 400
GIT_TIMEOUT_S = 30

# Conservative credential shapes redacted from worker output before it is
# stored in the loop state or proof.
_SECRET_RE = re.compile(
    r"(?:sk|pk|api|key|tok|secret)[_-][A-Za-z0-9]{16,}"
    r"|ghp_[A-Za-z0-9]{16,}"
    r"|github_pat_[A-Za-z0-9]{16,}"
    r"|xox[baprs]-[A-Za-z0-9\-]{16,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|Bearer\s+[A-Za-z0-9._~+/=-]{8,}"
    r"|(?:API_KEY|SECRET|TOKEN|PASSWORD|PASSWD)\s*[=:]\s*\S+",
    re.IGNORECASE,
)

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


def _failure_tail(stderr: str, stdout: str, limit: int = FAILURE_TAIL_CHARS) -> str:
    """Return a bounded tail of stderr, falling back to stdout.

    Provider errors (``Free usage limit reached``, ``ECONNREFUSED``, ...) are
    usually at the end of the stream, so the tail is what matters. The result
    is capped at *limit* characters and never contains secrets beyond what the
    worker itself printed.
    """
    text = (stderr or "").strip() or (stdout or "").strip()
    if not text:
        return ""
    text = _SECRET_RE.sub("[redacted]", text)
    if len(text) <= limit:
        return text
    return "...[truncated]...\n" + text[-limit:]


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
    :func:`build_task_prompt` on stdin. A bounded tail of stderr (falling back
    to stdout) is kept in the failure detail. ``timeout_s`` is clamped to
    ``[1, 7200]``; on expiry the whole process tree is killed.
    ``changed_paths`` comes from git, and the gate is ``PASS`` only when the
    command exits 0 and changed something.
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
        root = Path(worktree)
        # ``{workdir}`` is replaced with the absolute worktree path so agents
        # that resolve a relative ``--dir`` against their own detected root
        # are still confined to the isolated worktree.
        cmd = [str(part).replace("{workdir}", str(root)) for part in self.command]
        cmd[0] = _resolve_executable(cmd[0])
        child_env = None if self.env is None else {**os.environ, **self.env}

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
        tail = _failure_tail(stderr, stdout)
        if tail:
            detail += f"\noutput_tail:\n{tail}"
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

    Assumed flags: ``run --auto --dir {workdir}`` plus an optional ``--model``.
    ``{workdir}`` is replaced with the absolute isolated worktree path at run
    time. An absolute ``--dir`` is deliberate: opencode resolves a relative
    ``--dir .`` against its own detected project root, which was observed to
    escape the worktree and edit the original checkout (or an unrelated repo).
    The prompt is fed on stdin.
    """
    command = ["opencode", "run", "--auto", "--dir", "{workdir}"]
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


def striker_label(worker: ExternalWorker) -> str:
    """Role-prefixed worker label that keeps the real executable identity.

    External CLI workers are the Forge's *strikers* (the blacksmith's
    sledge-swinging assistants). Evidence must still name what actually ran,
    so the label is ``striker:<executable>`` — never a bare role name.
    """
    return f"striker:{worker.name}"


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
    "striker_label",
]
