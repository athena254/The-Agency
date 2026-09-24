from __future__ import annotations

import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import structlog

from agency.security.sandbox.config import SandboxConfig
from agency.security.sandbox.manager import ExecutionResult, SandboxError

logger = structlog.get_logger(__name__)

_OUTPUT_CAP = 64 * 1024


class ProcessSandboxBackend:
    """Subprocess-based sandbox backend (no container isolation).

    Provides process-level separation only: each sandbox gets its own
    workspace directory used as the subprocess ``cwd``. On Windows there
    are no CPU/memory rlimits (resource.setrlimit is POSIX-only); the only
    bounds enforced are the command timeout and the 64 KB stdout/stderr
    output caps. Suitable for trusted code and demos, NOT for hostile
    code -- hostile code needs the Docker backend.
    """

    def __init__(self, command_timeout: int = 120) -> None:
        self._command_timeout = command_timeout
        self._workspaces: dict[str, Path] = {}
        self._lock = threading.Lock()

    def create_container(
        self, sandbox_id: str, config: SandboxConfig, workspace: Path
    ) -> str:
        container_id = f"proc-{sandbox_id}"
        workspace.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._workspaces[container_id] = workspace
        logger.info(
            "process_sandbox_created",
            sandbox_id=sandbox_id,
            container_id=container_id,
            workspace=str(workspace),
        )
        return container_id

    def exec_command(
        self, container_id: str, command: Sequence[str], timeout: int
    ) -> ExecutionResult:
        with self._lock:
            workspace = self._workspaces.get(container_id)
        if workspace is None:
            raise SandboxError(f"unknown container {container_id}")

        start = time.perf_counter()
        exit_code: int | None = None
        stdout = ""
        stderr = ""
        timed_out = False
        error: str | None = None
        try:
            kwargs: dict[str, Any] = {
                "cwd": workspace,
                "capture_output": True,
                "timeout": timeout,
                "text": True,
                "encoding": "utf-8",
                "errors": "replace",
            }
            if sys.platform == "win32":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                kwargs["start_new_session"] = True
            process = subprocess.run(list(command), check=False, **kwargs)
            stdout, stderr = process.stdout, process.stderr
            exit_code = process.returncode
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            stdout = str(exc.stdout or "")
            stderr = f"timed out after {timeout}s"
            exit_code = -1
            error = f"command timed out after {timeout}s"
        duration_ms = (time.perf_counter() - start) * 1000.0
        stdout = stdout[:_OUTPUT_CAP]
        stderr = stderr[:_OUTPUT_CAP]
        return ExecutionResult(
            sandbox_id="",
            command=list(command),
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            duration_ms=duration_ms,
            timed_out=timed_out,
            error=error,
        )

    def inspect_status(self, container_id: str) -> str:
        with self._lock:
            registered = container_id in self._workspaces
        return "running" if registered else "stopped"

    def remove_container(self, container_id: str) -> None:
        with self._lock:
            workspace = self._workspaces.pop(container_id, None)
        if workspace is not None:
            try:
                shutil.rmtree(workspace, ignore_errors=True)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "process_sandbox_remove_failed",
                    container_id=container_id,
                    workspace=str(workspace),
                )
