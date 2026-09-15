"""Sandbox configuration: enums, dataclasses, and policy types for the Nexus sandbox addon."""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


class SandboxBackend(str, enum.Enum):
    """Supported sandbox execution backends."""

    DOCKER = "docker"
    PROCESS = "process"
    RESTRICTED = "restricted"


class SandboxType(str, enum.Enum):
    """Sandbox operational mode."""

    CLEAN_ROOM = "clean_room"
    ATHENA_MIRROR = "athena_mirror"


class NetworkAccess(str, enum.Enum):
    """Network access level for a sandbox."""

    NONE = "none"
    LOCAL = "local"
    FULL = "full"


class SandboxState(str, enum.Enum):
    """Lifecycle state of a sandbox."""

    PENDING = "pending"
    CREATING = "creating"
    READY = "ready"
    RUNNING = "running"
    SNAPSHOTTING = "snapshotting"
    DESTROYING = "destroying"
    DESTROYED = "destroyed"
    ERROR = "error"


class SandboxPolicy(str, enum.Enum):
    """Policy enforcement level for sandbox code."""

    STRICT = "strict"
    MODERATE = "moderate"
    PERMISSIVE = "permissive"


# ---------------------------------------------------------------------------
# Resource Limits
# ---------------------------------------------------------------------------

DEFAULT_CPU_LIMIT: float = 1.0
DEFAULT_MEMORY_LIMIT_MB: int = 512
DEFAULT_TIMEOUT_SECONDS: int = 30
DEFAULT_MAX_PROCESSES: int = 64
DEFAULT_DISK_LIMIT_MB: int = 1024
DEFAULT_NETWORK_TIMEOUT: int = 10


@dataclass(frozen=True)
class ResourceLimits:
    """Resource limits for a sandbox execution environment."""

    cpu: float = DEFAULT_CPU_LIMIT
    memory_mb: int = DEFAULT_MEMORY_LIMIT_MB
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    max_processes: int = DEFAULT_MAX_PROCESSES
    disk_mb: int = DEFAULT_DISK_LIMIT_MB
    network_timeout: int = DEFAULT_NETWORK_TIMEOUT

    def to_dict(self) -> dict[str, Any]:
        return {
            "cpu": self.cpu,
            "memory_mb": self.memory_mb,
            "timeout_seconds": self.timeout_seconds,
            "max_processes": self.max_processes,
            "disk_mb": self.disk_mb,
            "network_timeout": self.network_timeout,
        }


# ---------------------------------------------------------------------------
# Athena Mirror Configuration
# ---------------------------------------------------------------------------


@dataclass
class AthenaMirrorConfig:
    """Configuration for Mode 2: Athena Mirror (Gemini)."""

    nexus_repo_path: str = "/workspace/nexus"
    install_editable: bool = True
    init_lattice: bool = True
    run_tests: bool = True
    test_pattern: str = "tests/"
    extra_mounts: list[tuple[str, str]] = field(default_factory=list)
    pip_requirements: list[str] = field(default_factory=list)
    env_vars: dict[str, str] = field(default_factory=dict)
    pre_run_commands: list[str] = field(default_factory=list)
    post_run_commands: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "nexus_repo_path": self.nexus_repo_path,
            "install_editable": self.install_editable,
            "init_lattice": self.init_lattice,
            "run_tests": self.run_tests,
            "test_pattern": self.test_pattern,
            "extra_mounts": list(self.extra_mounts),
            "pip_requirements": list(self.pip_requirements),
            "env_vars": dict(self.env_vars),
            "pre_run_commands": list(self.pre_run_commands),
            "post_run_commands": list(self.post_run_commands),
        }


# ---------------------------------------------------------------------------
# Sandbox Configuration
# ---------------------------------------------------------------------------


@dataclass
class SandboxConfig:
    """Full configuration for creating and running a sandbox."""

    sandbox_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "default"
    sandbox_type: SandboxType = SandboxType.CLEAN_ROOM
    backend: SandboxBackend = SandboxBackend.DOCKER
    network_access: NetworkAccess = NetworkAccess.NONE
    resource_limits: ResourceLimits = field(default_factory=ResourceLimits)
    policy: SandboxPolicy = SandboxPolicy.STRICT
    env_vars: dict[str, str] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    athena_mirror: AthenaMirrorConfig | None = None
    auto_destroy_seconds: int = 3600
    tags: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.sandbox_type == SandboxType.ATHENA_MIRROR and self.athena_mirror is None:
            self.athena_mirror = AthenaMirrorConfig()

    def to_dict(self) -> dict[str, Any]:
        return {
            "sandbox_id": self.sandbox_id,
            "name": self.name,
            "sandbox_type": self.sandbox_type.value,
            "backend": self.backend.value,
            "network_access": self.network_access.value,
            "resource_limits": self.resource_limits.to_dict(),
            "policy": self.policy.value,
            "env_vars": dict(self.env_vars),
            "labels": dict(self.labels),
            "athena_mirror": self.athena_mirror.to_dict() if self.athena_mirror else None,
            "auto_destroy_seconds": self.auto_destroy_seconds,
            "tags": list(self.tags),
        }


# ---------------------------------------------------------------------------
# Sandbox Results & Stats
# ---------------------------------------------------------------------------


@dataclass
class SandboxResult:
    """Result from a sandbox code execution."""

    sandbox_id: str
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    memory_peak_mb: float = 0.0
    timestamp: datetime = field(default_factory=datetime.utcnow)
    timed_out: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def to_dict(self) -> dict[str, Any]:
        return {
            "sandbox_id": self.sandbox_id,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_seconds": self.duration_seconds,
            "memory_peak_mb": self.memory_peak_mb,
            "timestamp": self.timestamp.isoformat(),
            "timed_out": self.timed_out,
            "metadata": dict(self.metadata),
        }


@dataclass
class SandboxStats:
    """Aggregated statistics for a sandbox."""

    sandbox_id: str
    state: SandboxState = SandboxState.PENDING
    created_at: datetime = field(default_factory=datetime.utcnow)
    last_activity: datetime = field(default_factory=datetime.utcnow)
    total_runs: int = 0
    total_duration_seconds: float = 0.0
    total_failures: int = 0
    total_timeouts: int = 0
    average_duration_seconds: float = 0.0
    peak_memory_mb: float = 0.0
    current_memory_mb: float = 0.0
    cpu_percent: float = 0.0

    def record_run(self, result: SandboxResult) -> None:
        self.total_runs += 1
        self.total_duration_seconds += result.duration_seconds
        if not result.success:
            self.total_failures += 1
        if result.timed_out:
            self.total_timeouts += 1
        self.peak_memory_mb = max(self.peak_memory_mb, result.memory_peak_mb)
        self.average_duration_seconds = self.total_duration_seconds / self.total_runs
        self.last_activity = datetime.utcnow()

    def to_dict(self) -> dict[str, Any]:
        return {
            "sandbox_id": self.sandbox_id,
            "state": self.state.value,
            "created_at": self.created_at.isoformat(),
            "last_activity": self.last_activity.isoformat(),
            "total_runs": self.total_runs,
            "total_duration_seconds": self.total_duration_seconds,
            "total_failures": self.total_failures,
            "total_timeouts": self.total_timeouts,
            "average_duration_seconds": self.average_duration_seconds,
            "peak_memory_mb": self.peak_memory_mb,
            "current_memory_mb": self.current_memory_mb,
            "cpu_percent": self.cpu_percent,
        }
