"""Tests for SandboxExecTool (real local process backend, no docker needed)."""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import pytest

from agency.security.sandbox.config import SandboxConfig
from agency.security.sandbox.manager import ExecutionResult, SandboxManager, SandboxStatus
from agency.tools.base import ToolContext
from agency.tools.builtin.sandbox import SandboxExecTool


class LocalProcessBackend:
    """Minimal process backend with the DockerSandboxBackend interface."""

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir
        self._workspaces: dict[str, Path] = {}

    def create_container(self, sandbox_id: str, config: SandboxConfig, workspace: Path) -> str:
        path = Path(workspace)
        path.mkdir(parents=True, exist_ok=True)
        container_id = f"local-{sandbox_id}"
        self._workspaces[container_id] = path
        return container_id

    def exec_command(self, container_id: str, command, timeout: int) -> ExecutionResult:
        start = time.perf_counter()
        try:
            process = subprocess.run(
                list(command),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            stdout, stderr, exit_code = process.stdout, process.stderr, process.returncode
            timed_out, error = False, None
        except subprocess.TimeoutExpired as exc:
            stdout = str(exc.stdout or "")
            stderr = str(exc.stderr or "")
            exit_code, timed_out = None, True
            error = f"command timed out after {timeout}s"
        return ExecutionResult(
            sandbox_id="",
            command=list(command),
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            duration_ms=(time.perf_counter() - start) * 1000.0,
            timed_out=timed_out,
            error=error,
        )

    def inspect_status(self, container_id: str) -> str:
        return "running"

    def remove_container(self, container_id: str) -> None:
        workspace = self._workspaces.pop(container_id, None)
        if workspace is not None:
            shutil.rmtree(workspace, ignore_errors=True)


@pytest.fixture
def process_manager(tmp_path: Path) -> SandboxManager:
    backend = LocalProcessBackend(tmp_path / "workspaces")
    return SandboxManager(
        backend=backend,  # type: ignore[arg-type]
        base_workspace_dir=tmp_path / "sbx",
    )


def _ctx(manager: SandboxManager | None) -> ToolContext:
    return ToolContext(agent_id="agent-1", task_id="task-1", sandbox_manager=manager)


async def test_exec_echo_code_returns_stdout(process_manager: SandboxManager):
    tool = SandboxExecTool()
    result = await tool.run({"code": "print('hello sandbox')"}, _ctx(process_manager))
    assert result.ok is True
    assert result.output["stdout"].strip() == "hello sandbox"
    assert result.output["exit_code"] == 0
    assert result.evidence["language"] == "python"


async def test_exec_failing_code_returns_exit_code_and_stderr(process_manager: SandboxManager):
    tool = SandboxExecTool()
    result = await tool.run({"code": "raise RuntimeError('boom')"}, _ctx(process_manager))
    assert result.ok is True  # execution itself succeeded
    assert result.output["exit_code"] != 0
    assert "boom" in result.output["stderr"]
    assert result.evidence["exit_code"] == result.output["exit_code"]


async def test_exec_with_no_manager_is_not_ok():
    tool = SandboxExecTool()
    result = await tool.run({"code": "print('hi')"}, _ctx(None))
    assert result.ok is False
    assert "no sandbox manager" in (result.error or "")


async def test_exec_truncates_large_stdout(process_manager: SandboxManager):
    tool = SandboxExecTool()
    result = await tool.run({"code": "print('x' * 9000)"}, _ctx(process_manager))
    assert result.ok is True
    assert len(result.output["stdout"]) == 4000


async def test_exec_destroys_sandbox_it_created(process_manager: SandboxManager):
    tool = SandboxExecTool()
    before = {s.id for s in process_manager.list_sandboxes()}
    result = await tool.run({"code": "print('bye')"}, _ctx(process_manager))
    assert result.ok is True
    after = [s for s in process_manager.list_sandboxes() if s.id not in before]
    assert len(after) == 1
    assert after[0].status is SandboxStatus.DESTROYED


async def test_exec_rejects_unsupported_language(process_manager: SandboxManager):
    tool = SandboxExecTool()
    result = await tool.run({"code": "x", "language": "cobol"}, _ctx(process_manager))
    assert result.ok is False
    assert "unsupported language" in (result.error or "")
