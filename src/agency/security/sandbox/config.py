from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class NetworkPolicy(str, Enum):
    NONE = "none"
    BRIDGE = "bridge"
    HOST = "host"
    CUSTOM = "custom"


class FilesystemPolicy(str, Enum):
    READ_ONLY = "read_only"
    TMPFS = "tmpfs"
    PERSISTENT = "persistent"
    FULL = "full"


class SandboxBackend(str, Enum):
    DOCKER = "docker"
    FIRECRACKER = "firecracker"
    CHROOT = "chroot"


class ResourceLimits(BaseModel):
    cpu: float = Field(default=1.0, gt=0.0, le=16.0)
    memory_mb: int = Field(default=512, gt=0)
    max_pids: int = Field(default=64, gt=0)
    max_open_files: int = Field(default=1024, gt=0)
    disk_mb: int = Field(default=1024, gt=0)


class SandboxConfig(BaseModel):
    cpu_limit: float = Field(default=1.0, gt=0.0, le=16.0)
    memory_limit: int = Field(default=512, gt=0)
    network_policy: NetworkPolicy = NetworkPolicy.NONE
    filesystem_policy: FilesystemPolicy = FilesystemPolicy.READ_ONLY
    timeout: int = Field(default=300, gt=0)
    env_vars: dict[str, str] = Field(default_factory=dict)
    backend: SandboxBackend = SandboxBackend.DOCKER
    image: str = Field(default="theagency-sandbox-base:latest")
    workspace_dir: str | None = Field(default=None)
    workspace_mount: str = Field(default="/workspace")
    max_pids: int = Field(default=64, gt=0)
    max_open_files: int = Field(default=1024, gt=0)

    def to_resource_limits(self) -> ResourceLimits:
        return ResourceLimits(
            cpu=self.cpu_limit,
            memory_mb=self.memory_limit,
            max_pids=self.max_pids,
            max_open_files=self.max_open_files,
        )