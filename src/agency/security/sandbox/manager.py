from __future__ import annotations

import shlex
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from pydantic import BaseModel, Field

from agency.security.sandbox.config import (
    FilesystemPolicy,
    NetworkPolicy,
    ResourceLimits,
    SandboxBackend,
    SandboxConfig,
)

if TYPE_CHECKING:
    from agency.security.sandbox.process import ProcessSandboxBackend

logger = structlog.get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class SandboxError(Exception):
    pass


class SandboxStatus(str, Enum):
    CREATING = "creating"
    RUNNING = "running"
    ERROR = "error"
    STOPPED = "stopped"
    DESTROYED = "destroyed"


class Sandbox(BaseModel):
    id: str
    agent_id: str
    container_id: str | None = None
    status: SandboxStatus = SandboxStatus.CREATING
    created_at: datetime = Field(default_factory=_utcnow)
    resource_limits: ResourceLimits
    network_policy: NetworkPolicy
    config: SandboxConfig


class ExecutionResult(BaseModel):
    sandbox_id: str
    command: list[str]
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    duration_ms: float = 0.0
    timed_out: bool = False
    error: str | None = None

    @property
    def success(self) -> bool:
        return not self.timed_out and self.error is None and self.exit_code == 0


class DockerSandboxBackend:
    def __init__(self, command_timeout: int = 120) -> None:
        binary = shutil.which("docker")
        if binary is None:
            raise SandboxError("docker CLI not found on PATH")
        self._binary = binary
        self._command_timeout = command_timeout

    def _run(self, args: Sequence[str], timeout: int | None = None) -> tuple[int, str, str]:
        process = subprocess.run(
            [self._binary, *args],
            capture_output=True,
            text=True,
            timeout=timeout or self._command_timeout,
            check=False,
        )
        return process.returncode, process.stdout, process.stderr

    def create_container(self, sandbox_id: str, config: SandboxConfig, workspace: Path) -> str:
        args = ["run", "-d", "--name", f"agency-sbx-{sandbox_id}"]
        args += ["--cpus", str(config.cpu_limit)]
        args += ["--memory", f"{config.memory_limit}m"]
        args += ["--pids-limit", str(config.max_pids)]
        args += ["--ulimit", f"nofile={config.max_open_files}:{config.max_open_files}"]
        args += ["--network", config.network_policy.value]
        if config.filesystem_policy in (FilesystemPolicy.READ_ONLY, FilesystemPolicy.TMPFS):
            args += ["--read-only"]
        if config.filesystem_policy is FilesystemPolicy.TMPFS:
            args += ["--tmpfs", f"/tmp:rw,size={max(config.memory_limit // 2, 16)}m"]
        args += ["--cap-drop", "ALL", "--security-opt", "no-new-privileges"]
        args += ["-v", f"{workspace}:{config.workspace_mount}"]
        for key, value in config.env_vars.items():
            args += ["-e", f"{key}={value}"]
        args += [config.image, "sleep", "infinity"]
        code, stdout, stderr = self._run(args)
        if code != 0:
            raise SandboxError(f"failed to create container: {stderr.strip() or stdout.strip()}")
        container_id = stdout.strip()
        if not container_id:
            raise SandboxError("docker returned an empty container id")
        return container_id

    def exec_command(self, container_id: str, command: Sequence[str], timeout: int) -> ExecutionResult:
        start = time.perf_counter()
        exit_code: int | None = None
        stdout = ""
        stderr = ""
        timed_out = False
        error: str | None = None
        try:
            process = subprocess.run(
                [self._binary, "exec", container_id, *command],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            stdout, stderr = process.stdout, process.stderr
            exit_code = process.returncode
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            stdout = str(exc.stdout or "")
            stderr = str(exc.stderr or "")
            error = f"command timed out after {timeout}s"
        duration_ms = (time.perf_counter() - start) * 1000.0
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
        code, stdout, stderr = self._run(
            ["inspect", "-f", "{{.State.Status}}", container_id]
        )
        if code != 0:
            raise SandboxError(f"failed to inspect container: {stderr.strip()}")
        return stdout.strip()

    def remove_container(self, container_id: str) -> None:
        code, _stdout, stderr = self._run(["rm", "-f", container_id])
        if code != 0:
            raise SandboxError(f"failed to remove container: {stderr.strip()}")


class SandboxManager:
    def __init__(
        self,
        backend: DockerSandboxBackend | ProcessSandboxBackend | None = None,
        base_workspace_dir: Path | None = None,
        default_config: SandboxConfig | None = None,
    ) -> None:
        self._backend: DockerSandboxBackend | ProcessSandboxBackend | None = backend
        self._base_workspace_dir = base_workspace_dir or (
            Path(tempfile.gettempdir()) / "agency-sandboxes"
        )
        self._base_workspace_dir.mkdir(parents=True, exist_ok=True)
        self._default_config = default_config or SandboxConfig()
        self._sandboxes: dict[str, Sandbox] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _new_id() -> str:
        return f"sbx-{uuid.uuid4().hex[:12]}"

    def _get_backend(self) -> DockerSandboxBackend | ProcessSandboxBackend:
        if self._backend is None:
            with self._lock:
                if self._backend is None:
                    # Lazy import: process.py imports manager's types,
                    # so importing it at module level would be circular.
                    from agency.security.sandbox.process import ProcessSandboxBackend

                    if self._default_config.backend is SandboxBackend.PROCESS:
                        self._backend = ProcessSandboxBackend()
                    else:
                        self._backend = DockerSandboxBackend()
        return self._backend

    def create_sandbox(self, agent_id: str, config: SandboxConfig | None = None) -> Sandbox:
        sandbox_id = self._new_id()
        cfg = config or self._default_config
        workspace = (
            Path(cfg.workspace_dir)
            if cfg.workspace_dir
            else self._base_workspace_dir / sandbox_id
        )
        try:
            workspace.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise SandboxError(
                f"failed to create workspace {workspace}: {exc}"
            ) from exc

        sandbox = Sandbox(
            id=sandbox_id,
            agent_id=agent_id,
            resource_limits=cfg.to_resource_limits(),
            network_policy=cfg.network_policy,
            config=cfg,
        )
        with self._lock:
            self._sandboxes[sandbox_id] = sandbox

        container_id: str | None = None
        status = SandboxStatus.CREATING
        error: str | None = None

        if cfg.backend is not SandboxBackend.DOCKER:
            error = (
                f"backend {cfg.backend.value!r} is not implemented; "
                "only SandboxBackend.DOCKER is supported"
            )
            status = SandboxStatus.ERROR
            logger.warning(
                "sandbox_backend_unsupported",
                sandbox_id=sandbox_id,
                agent_id=agent_id,
                backend=cfg.backend.value,
            )
        else:
            try:
                backend = self._get_backend()
                container_id = backend.create_container(sandbox_id, cfg, workspace)
                state = backend.inspect_status(container_id)
                status = SandboxStatus.RUNNING if state == "running" else SandboxStatus.STOPPED
            except SandboxError as exc:
                error = str(exc)
                status = SandboxStatus.ERROR
                logger.warning(
                    "sandbox_create_failed",
                    sandbox_id=sandbox_id,
                    agent_id=agent_id,
                    error=error,
                )

        finalized = sandbox.model_copy(
            update={"status": status, "container_id": container_id}
        )
        with self._lock:
            self._sandboxes[sandbox_id] = finalized

        if status is SandboxStatus.RUNNING:
            logger.info(
                "sandbox_created",
                sandbox_id=sandbox_id,
                agent_id=agent_id,
                container_id=container_id,
            )
        if error is not None:
            raise SandboxError(error)
        return finalized

    def execute(self, sandbox_id: str, command: str | Sequence[str]) -> ExecutionResult:
        with self._lock:
            sandbox = self._sandboxes.get(sandbox_id)
        if sandbox is None:
            raise SandboxError(f"unknown sandbox {sandbox_id}")
        if sandbox.status is not SandboxStatus.RUNNING:
            raise SandboxError(
                f"sandbox {sandbox_id} is not running (status={sandbox.status.value})"
            )
        if sandbox.container_id is None:
            raise SandboxError(f"sandbox {sandbox_id} has no container")
        cmd = shlex.split(command) if isinstance(command, str) else list(command)
        backend = self._get_backend()
        result = backend.exec_command(
            sandbox.container_id, cmd, sandbox.config.timeout
        )
        completed = result.model_copy(
            update={"sandbox_id": sandbox_id, "command": cmd}
        )
        logger.info(
            "sandbox_executed",
            sandbox_id=sandbox_id,
            command=cmd[:64],
            exit_code=completed.exit_code,
            duration_ms=round(completed.duration_ms, 2),
            timed_out=completed.timed_out,
        )
        return completed

    def destroy_sandbox(self, sandbox_id: str) -> None:
        with self._lock:
            sandbox = self._sandboxes.get(sandbox_id)
        if sandbox is None:
            raise SandboxError(f"unknown sandbox {sandbox_id}")
        if sandbox.status is SandboxStatus.DESTROYED:
            return
        if sandbox.container_id is not None:
            backend = self._get_backend()
            backend.remove_container(sandbox.container_id)
        destroyed = sandbox.model_copy(update={"status": SandboxStatus.DESTROYED})
        with self._lock:
            self._sandboxes[sandbox_id] = destroyed
        logger.info("sandbox_destroyed", sandbox_id=sandbox_id, agent_id=sandbox.agent_id)

    def list_sandboxes(self, agent_id: str | None = None) -> list[Sandbox]:
        with self._lock:
            sandboxes = [
                sbx
                for sbx in self._sandboxes.values()
                if agent_id is None or sbx.agent_id == agent_id
            ]
        return sorted(sandboxes, key=lambda sbx: sbx.created_at, reverse=True)

    def get_sandbox(self, sandbox_id: str) -> Sandbox | None:
        with self._lock:
            return self._sandboxes.get(sandbox_id)