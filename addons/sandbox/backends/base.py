"""Abstract base class for sandbox execution backends."""

from __future__ import annotations

import abc
from typing import Any

from config import SandboxConfig, SandboxResult, SandboxStats


class SandboxBackend(abc.ABC):
    """Abstract base class defining the sandbox execution interface."""

    @property
    @abc.abstractmethod
    def backend_type(self) -> str:
        """Return the backend identifier string."""
        ...

    @abc.abstractmethod
    async def create(self, config: SandboxConfig) -> str:
        """Create and initialize the sandbox environment."""
        ...

    @abc.abstractmethod
    async def run_code(
        self,
        sandbox_id: str,
        code: str,
        timeout: int | None = None,
        env_vars: dict[str, str] | None = None,
    ) -> SandboxResult:
        """Execute arbitrary code inside the sandbox."""
        ...

    @abc.abstractmethod
    async def run_tests(
        self,
        sandbox_id: str,
        test_pattern: str = "tests/",
        timeout: int | None = None,
    ) -> SandboxResult:
        """Run test suite inside the sandbox."""
        ...

    @abc.abstractmethod
    async def install_packages(
        self,
        sandbox_id: str,
        packages: list[str],
        timeout: int | None = None,
    ) -> SandboxResult:
        """Install packages inside the sandbox."""
        ...

    @abc.abstractmethod
    async def copy_to_sandbox(
        self,
        sandbox_id: str,
        source_path: str,
        dest_path: str,
    ) -> None:
        """Copy a file into the sandbox."""
        ...

    @abc.abstractmethod
    async def copy_from_sandbox(
        self,
        sandbox_id: str,
        source_path: str,
        dest_path: str,
    ) -> None:
        """Copy a file out of the sandbox."""
        ...

    @abc.abstractmethod
    async def snapshot(self, sandbox_id: str, snapshot_id: str | None = None) -> str:
        """Create a snapshot of the sandbox state."""
        ...

    @abc.abstractmethod
    async def restore(self, sandbox_id: str, snapshot_id: str) -> None:
        """Restore sandbox from a snapshot."""
        ...

    @abc.abstractmethod
    async def destroy(self, sandbox_id: str) -> None:
        """Destroy the sandbox and free all resources."""
        ...

    @abc.abstractmethod
    async def get_metrics(self, sandbox_id: str) -> dict[str, Any]:
        """Return real-time resource usage metrics."""
        ...

    @abc.abstractmethod
    async def get_stats(self, sandbox_id: str) -> SandboxStats:
        """Return aggregate statistics for the sandbox."""
        ...

    @abc.abstractmethod
    async def health_check(self, sandbox_id: str) -> bool:
        """Check whether the sandbox is healthy and responsive."""
        ...
