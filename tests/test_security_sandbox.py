"""Tests for sandbox creation, execution, and destruction (mocked backend)."""

import pytest

from agency.security.sandbox.config import (
    FilesystemPolicy,
    NetworkPolicy,
    SandboxBackend,
    SandboxConfig,
)
from agency.security.sandbox.manager import (
    ExecutionResult,
    SandboxError,
    SandboxManager,
    SandboxStatus,
)


def test_config_defaults_and_resource_limits():
    cfg = SandboxConfig()
    assert cfg.cpu_limit == 1.0
    assert cfg.network_policy is NetworkPolicy.NONE
    assert cfg.backend is SandboxBackend.DOCKER
    limits = cfg.to_resource_limits()
    assert limits.cpu == cfg.cpu_limit
    assert limits.memory_mb == cfg.memory_limit


def test_create_sandbox_running(test_sandbox_manager: SandboxManager, fake_backend):
    sbx = test_sandbox_manager.create_sandbox("agent-1")
    assert sbx.agent_id == "agent-1"
    assert sbx.status is SandboxStatus.RUNNING
    assert sbx.container_id == f"container-{sbx.id}"
    assert sbx.id in fake_backend.created
    assert test_sandbox_manager.get_sandbox(sbx.id) is not None
    assert test_sandbox_manager.get_sandbox("missing") is None


def test_create_sandbox_custom_config(test_sandbox_manager: SandboxManager):
    cfg = SandboxConfig(cpu_limit=0.5, memory_limit=256, timeout=60, env_vars={"FOO": "bar"})
    sbx = test_sandbox_manager.create_sandbox("agent-9", config=cfg)
    assert sbx.config.cpu_limit == 0.5
    assert sbx.resource_limits.memory_mb == 256


def test_create_sandbox_unsupported_backend(test_sandbox_manager: SandboxManager):
    cfg = SandboxConfig(backend=SandboxBackend.FIRECRACKER)
    with pytest.raises(SandboxError, match="not implemented"):
        test_sandbox_manager.create_sandbox("agent-1", config=cfg)


def test_execute_success(test_sandbox: object, test_sandbox_manager: SandboxManager):
    sbx = test_sandbox  # type: ignore[assignment]
    result = test_sandbox_manager.execute(sbx.id, ["echo", "hi"])
    assert isinstance(result, ExecutionResult)
    assert result.exit_code == 0
    assert result.success is True
    assert result.stdout == "ok"
    assert result.sandbox_id == sbx.id


def test_execute_string_command_split(test_sandbox_manager: SandboxManager, test_sandbox):
    result = test_sandbox_manager.execute(test_sandbox.id, "echo hello world")
    assert result.command == ["echo", "hello", "world"]


def test_execute_unknown_sandbox(test_sandbox_manager: SandboxManager):
    with pytest.raises(SandboxError, match="unknown sandbox"):
        test_sandbox_manager.execute("sbx-missing", ["echo"])


def test_execute_non_running_rejected(test_sandbox_manager: SandboxManager, fake_backend):
    fake_backend.exit_code = 1
    # force ERROR status by using unsupported backend path is complex;
    # instead destroy then execute
    sbx = test_sandbox_manager.create_sandbox("agent-1")
    test_sandbox_manager.destroy_sandbox(sbx.id)
    with pytest.raises(SandboxError):
        test_sandbox_manager.execute(sbx.id, ["echo"])


def test_execute_failure_status(test_sandbox_manager: SandboxManager, fake_backend):
    fake_backend.exit_code = 1
    fake_backend.stdout = ""
    sbx = test_sandbox_manager.create_sandbox("agent-1")
    result = test_sandbox_manager.execute(sbx.id, ["false"])
    assert result.success is False
    assert result.exit_code == 1


def test_destroy_sandbox(test_sandbox_manager: SandboxManager, fake_backend, test_sandbox):
    sbx_id = test_sandbox.id
    container = test_sandbox.container_id
    test_sandbox_manager.destroy_sandbox(sbx_id)
    assert test_sandbox_manager.get_sandbox(sbx_id).status is SandboxStatus.DESTROYED
    assert container in fake_backend.removed
    # double-destroy is idempotent
    test_sandbox_manager.destroy_sandbox(sbx_id)


def test_destroy_unknown_raises(test_sandbox_manager: SandboxManager):
    with pytest.raises(SandboxError, match="unknown sandbox"):
        test_sandbox_manager.destroy_sandbox("sbx-nope")


def test_list_sandboxes_filtered(test_sandbox_manager: SandboxManager):
    test_sandbox_manager.create_sandbox("agent-1")
    test_sandbox_manager.create_sandbox("agent-2")
    assert len(test_sandbox_manager.list_sandboxes()) == 2
    assert len(test_sandbox_manager.list_sandboxes("agent-1")) == 1
    assert test_sandbox_manager.list_sandboxes("nobody") == []


def test_execution_result_success_property():
    ok = ExecutionResult(sandbox_id="s", command=["a"], exit_code=0)
    assert ok.success is True
    assert ExecutionResult(sandbox_id="s", command=["a"], exit_code=1).success is False
    assert (
        ExecutionResult(sandbox_id="s", command=["a"], exit_code=0, timed_out=True).success is False
    )
    assert ExecutionResult(sandbox_id="s", command=["a"], exit_code=0, error="x").success is False


def test_filesystem_and_network_policies_enumerated():
    assert FilesystemPolicy.READ_ONLY.value == "read_only"
    assert NetworkPolicy.NONE.value == "none"
