"""Shared pytest fixtures for the Agency test suite."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from agency.agents.registry import AgentRegistry as RuntimeAgentRegistry
from agency.bridges.base import Bridge, BridgeConfig, BridgeResult
from agency.bridges.coordinator import ExternalCoordinator
from agency.evidence.store.store import EvidenceStore
from agency.kernel.audit import AuditLog
from agency.kernel.identity import Agent, Capability, TrustLevel
from agency.kernel.policies import ActionClass, Permission, PolicyEngine
from agency.kernel.registry import AgentRegistry
from agency.kernel.tasks import TaskManager
from agency.memory.sms.lifecycle import TieredMemoryEngine
from agency.memory.sms.models import MemoryItem
from agency.memory.sms.secrets import SecretsStore
from agency.memory.sms.store import MemoryStore
from agency.risk.engine.engine import RiskEngine
from agency.security.blue.defender import BlueTeam
from agency.security.purple.validator import PurpleTeam
from agency.security.red.executor import RedTeamExecutor
from agency.security.red.planner import RedTeamPlanner
from agency.security.sandbox.config import SandboxBackend, SandboxConfig
from agency.security.sandbox.manager import ExecutionResult as SandboxExecutionResult
from agency.security.sandbox.manager import Sandbox, SandboxManager, SandboxStatus


# ------------------------------------------------------------------ #
# Kernel
# ------------------------------------------------------------------ #

@pytest.fixture
def test_agent() -> Agent:
    return Agent(
        id="agent-1",
        name="test-agent",
        domain="security",
        trust_level=TrustLevel.TRUSTED,
        capabilities=[
            Capability(name="inspect", max_level=ActionClass.L0_OBSERVATION),
            Capability(name="simulate", max_level=ActionClass.L2_CONTROLLED_TESTING),
        ],
    )


@pytest.fixture
def test_agent_registry(test_agent: Agent) -> AgentRegistry:
    reg = AgentRegistry()
    reg.register(test_agent)
    return reg


@pytest.fixture
def test_policy_engine() -> PolicyEngine:
    return PolicyEngine()


@pytest.fixture
def test_task_manager() -> TaskManager:
    return TaskManager()


@pytest.fixture
def test_audit_log(tmp_path) -> AuditLog:
    return AuditLog(tmp_path / "audit.db")


@pytest_asyncio.fixture
async def initialized_audit_log(tmp_path) -> AsyncGenerator[AuditLog, None]:
    log = AuditLog(tmp_path / "audit.db")
    await log.initialize()
    try:
        yield log
    finally:
        await log.close()


# ------------------------------------------------------------------ #
# Memory / Evidence / Risk
# ------------------------------------------------------------------ #

@pytest_asyncio.fixture
async def test_memory_store() -> AsyncGenerator[MemoryStore, None]:
    store = MemoryStore(":memory:")
    await store.initialize()
    try:
        yield store
    finally:
        await store.close()


@pytest_asyncio.fixture
async def test_memory_engine(test_memory_store: MemoryStore) -> TieredMemoryEngine:
    return TieredMemoryEngine(test_memory_store)


@pytest_asyncio.fixture
async def test_secrets_store() -> AsyncGenerator[SecretsStore, None]:
    from cryptography.fernet import Fernet

    store = SecretsStore(":memory:", encryption_key=Fernet.generate_key())
    await store.initialize()
    try:
        yield store
    finally:
        await store.close()


@pytest_asyncio.fixture
async def test_evidence_store() -> AsyncGenerator[EvidenceStore, None]:
    store = EvidenceStore(":memory:")
    await store.initialize()
    try:
        yield store
    finally:
        await store.close()


@pytest.fixture
def test_risk_engine() -> RiskEngine:
    return RiskEngine()


@pytest.fixture
def sample_memory_item() -> MemoryItem:
    return MemoryItem(agent_id="agent-1", content="probe result: open port 443", tags=["scan"])


# ------------------------------------------------------------------ #
# Sandbox (mocked backend — no docker required)
# ------------------------------------------------------------------ #

class FakeSandboxBackend:
    """In-memory stand-in for DockerSandboxBackend."""

    def __init__(self, exit_code: int = 0, stdout: str = "ok") -> None:
        self.exit_code = exit_code
        self.stdout = stdout
        self.created: list[str] = []
        self.removed: list[str] = []

    def create_container(self, sandbox_id: str, config: SandboxConfig, workspace) -> str:
        self.created.append(sandbox_id)
        return f"container-{sandbox_id}"

    def exec_command(self, container_id: str, command, timeout: int) -> SandboxExecutionResult:
        return SandboxExecutionResult(
            sandbox_id="",
            command=list(command),
            stdout=self.stdout,
            stderr="",
            exit_code=self.exit_code,
            duration_ms=1.0,
        )

    def inspect_status(self, container_id: str) -> str:
        return "running"

    def remove_container(self, container_id: str) -> None:
        self.removed.append(container_id)


@pytest.fixture
def fake_backend() -> FakeSandboxBackend:
    return FakeSandboxBackend()


@pytest.fixture
def test_sandbox_manager(fake_backend: FakeSandboxBackend, tmp_path) -> SandboxManager:
    return SandboxManager(backend=fake_backend, base_workspace_dir=tmp_path / "sbx")


@pytest.fixture
def test_sandbox(test_sandbox_manager: SandboxManager) -> Sandbox:
    return test_sandbox_manager.create_sandbox("agent-1")


# ------------------------------------------------------------------ #
# Red / Blue / Purple
# ------------------------------------------------------------------ #

@pytest.fixture
def test_red_planner() -> RedTeamPlanner:
    return RedTeamPlanner()


@pytest.fixture
def test_red_executor(
    test_red_planner: RedTeamPlanner, test_sandbox_manager: SandboxManager
) -> RedTeamExecutor:
    return RedTeamExecutor(test_red_planner, test_sandbox_manager)


@pytest.fixture
def test_blue_team() -> BlueTeam:
    return BlueTeam()


@pytest.fixture
def test_purple_team(test_red_executor: RedTeamExecutor, test_blue_team: BlueTeam) -> PurpleTeam:
    return PurpleTeam(test_red_executor, test_blue_team)


# ------------------------------------------------------------------ #
# Bridges (mocked)
# ------------------------------------------------------------------ #

class FakeBridge(Bridge):
    def __init__(self, name: str = "fake", output: str = "done", healthy: bool = True) -> None:
        super().__init__(BridgeConfig(name=name))
        self._output = output
        self._healthy = healthy
        self.calls: list[str] = []

    async def execute(self, task: str, context: dict[str, Any] | None = None) -> BridgeResult:
        self.calls.append(task)
        return BridgeResult(output=self._output)

    async def stream(self, task: str, context: dict[str, Any] | None = None):
        yield self._output

    def capabilities(self) -> dict[str, Any]:
        return {"streaming": True, "tools": ["exec"], "notes": "fake"}

    async def health_check(self) -> bool:
        return self._healthy


@pytest.fixture
def fake_bridge() -> FakeBridge:
    return FakeBridge()


@pytest_asyncio.fixture
async def test_coordinator(fake_bridge: FakeBridge) -> ExternalCoordinator:
    coord = ExternalCoordinator()
    await coord.register_bridge(fake_bridge)
    return coord


# ------------------------------------------------------------------ #
# Agents (runtime)
# ------------------------------------------------------------------ #

@pytest.fixture
def test_runtime_registry() -> RuntimeAgentRegistry:
    reg = RuntimeAgentRegistry()
    reg.register(Agent(id="planner-1", name="planner", capabilities=["inspect"]))
    reg.register(Agent(id="worker-1", name="worker", capabilities=["simulate"]))
    return reg


@pytest.fixture
def test_permission() -> Permission:
    from datetime import timedelta

    return Permission(
        agent_id="agent-1",
        target_scope="staging/*",
        capabilities=["simulate"],
        time_limit=timedelta(minutes=30),
    )
