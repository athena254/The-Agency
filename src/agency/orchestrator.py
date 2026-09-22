"""Agency Orchestrator — central wiring for the running system.

The orchestrator connects all components into a working agent:
kernel (identity, policies, tasks, audit), agents (planner, executor,
verifier, loop), memory (SMS), evidence, risk, and bridges.

Every component is injected or created with defaults. No global state.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog

from agency.agents.executor import AgentExecutor, ExecutionResult, ExecutionStatus
from agency.agents.loop import AgentLoop
from agency.agents.planner import AgentPlanner
from agency.agents.registry import AgentRegistry as RuntimeAgentRegistry
from agency.agents.verifier import AgentVerifier, VerificationStatus
from agency.bridges.coordinator import ExternalCoordinator
from agency.evidence.store.models import (
    Finding,
    Severity,
    VerificationState,
)
from agency.evidence.store.store import EvidenceStore
from agency.kernel.audit import AuditEntry, AuditLog
from agency.kernel.identity import Agent, Capability, TrustLevel
from agency.kernel.policies import ActionClass, Permission, PolicyEngine
from agency.kernel.registry import AgentRegistry
from agency.kernel.tasks import Task, TaskManager, TaskMessage, TaskStatus
from agency.lattice import get_lattice
from agency.llm.adapter import LLMAdapter
from agency.memory.sms.lifecycle import TieredMemoryEngine
from agency.memory.sms.models import MemoryItem, MemoryTier
from agency.memory.sms.retrieval import RetrievalEngine
from agency.memory.sms.store import MemoryStore
from agency.risk.engine.engine import RiskEngine

logger = structlog.get_logger(__name__)


class AgencyOrchestrator:
    """Central orchestrator that wires all Agency components together.

    Usage::

        orch = AgencyOrchestrator()
        await orch.start()
        agent = await orch.register_agent("scout", "security", ["inspect"])
        task = await orch.submit_task("Scan target", "Run reconnaissance", agent.id)
        result = await orch.execute_task(task.id)
        await orch.stop()
    """

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        llm: LLMAdapter | None = None,
    ) -> None:
        self._config = config or {}
        self._log = structlog.get_logger(__name__)

        # LLM — shared adapter for all agent execution. Falls back to
        # deterministic echo mode when no API keys are configured, so
        # tests and offline runs never require network access.
        llm_config = self._config.get("llm_config")
        if llm is not None:
            self._llm_adapter = llm
        elif llm_config is not None:
            from agency.llm.config import LLMConfig as _LLMConfig

            if isinstance(llm_config, _LLMConfig):
                self._llm_adapter = LLMAdapter(config=llm_config)
            elif isinstance(llm_config, dict):
                from agency.llm.config import LLMConfig as _LC

                self._llm_adapter = LLMAdapter(config=_LC(**llm_config))
            else:
                self._llm_adapter = LLMAdapter()
        else:
            self._llm_adapter = LLMAdapter()

        # Kernel
        self._identity_registry = AgentRegistry()
        self._policy_engine = PolicyEngine()
        self._task_manager = TaskManager()
        self._audit_log = AuditLog(":memory:")

        # Agents
        self._runtime_registry = RuntimeAgentRegistry()
        self._planner = AgentPlanner(self._runtime_registry)
        self._executor = AgentExecutor(llm=self._llm_adapter)
        self._verifier = AgentVerifier()
        self._loop = AgentLoop()

        # Memory
        self._memory_store = MemoryStore()
        self._memory_lifecycle = TieredMemoryEngine(self._memory_store)
        self._memory_retrieval = RetrievalEngine(self._memory_store)

        # Evidence & Risk
        self._evidence_store = EvidenceStore()
        self._risk_engine = RiskEngine()

        # Bridges
        self._bridge_coordinator = ExternalCoordinator()

        # Lattice
        self._lattice = None
        self._spawned_proposals = set()

        self._started = False

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def start(self) -> None:
        """Start background services."""
        await self._memory_store.initialize()
        await self._evidence_store.initialize()
        await self._audit_log.initialize()
        self._lattice = await get_lattice()
        self._started = True
        self._log.info("orchestrator_started")

    async def stop(self) -> None:
        """Stop background services."""
        await self._memory_lifecycle.stop_periodic()
        if self._lattice is not None:
            await self._lattice.close()
            self._lattice = None
        self._started = False
        self._log.info("orchestrator_stopped")

    # ------------------------------------------------------------------ #
    # Agent management
    # ------------------------------------------------------------------ #

    async def register_agent(
        self,
        name: str,
        domain: str,
        capabilities: list[str],
        trust_level: TrustLevel = TrustLevel.OBSERVED,
    ) -> Agent:
        """Register a new agent with identity + runtime record."""
        agent = Agent(
            name=name,
            domain=domain,
            capabilities=[Capability(name=c, max_level=ActionClass.L1_SAFE_ANALYSIS) for c in capabilities],
            trust_level=trust_level,
        )
        self._identity_registry.register(agent)
        self._runtime_registry.register(agent)

        # Register in Lattice if available
        if self._lattice is not None:
            try:
                await self._lattice.register_agent(
                    agent_id=agent.id,
                    agent_type=domain,
                    capabilities=capabilities,
                )
            except Exception:
                self._log.warning("lattice.agent_register_failed", agent_id=agent.id, exc_info=True)

        # Grant default permission for L0-L1 actions
        perm = Permission(
            agent_id=agent.id,
            target_scope="*",
            capabilities=capabilities,
        )
        self._policy_engine.issue(perm)

        self._log.info("agent_registered", agent_id=agent.id, name=name, domain=domain)
        return agent

    async def resolve_agent_proposal(
        self,
        proposal_id: str,
        voter_id: str,
        decision: str,
        evidence: list[str] | None = None,
    ) -> dict[str, Any]:
        """Cast a vote on a spawn-agent proposal and auto-spawn if quorum reached."""
        if self._lattice is None:
            raise RuntimeError("Lattice is not available")

        # Get proposal details before voting so we have the payload
        try:
            proposal = await self._lattice.get_proposal_status(proposal_id)
            # Also get payload from events if available
            events = await self._lattice.get_events(target_id=proposal_id, event_type="proposal_submitted")
            proposal_payload = events[-1].payload if events else {}
        except Exception:
            proposal = None
            proposal_payload = {}

        result = await self._lattice.cast_vote(
            voter_id=voter_id,
            proposal_id=proposal_id,
            decision=decision,
            evidence=evidence,
        )

        # Auto-spawn only once per proposal
        if result.get("status") == "passed" and proposal_id not in self._spawned_proposals:
            self._spawned_proposals.add(proposal_id)
            await self._spawn_from_proposal(proposal_id, proposal_payload)

        return result

    async def _spawn_from_proposal(self, proposal_id: str, proposal_payload: dict | None = None) -> None:
        """Spawn an agent from a passed governance proposal."""
        try:
            proposal = await self._lattice.get_proposal_status(proposal_id)
        except Exception as exc:
            self._log.warning("lattice.spawn_get_failed", proposal_id=proposal_id, error=str(exc))
            return

        if proposal.proposal_type != "spawn_agent":
            return

        # Use the payload we already have; fall back to event lookup only if needed
        payload = proposal_payload or {}
        if not payload:
            try:
                events = await self._lattice.get_events(target_id=proposal_id, event_type="proposal_submitted")
                if events:
                    payload = events[-1].payload
            except Exception:
                pass

        name = payload.get("name", f"agent-{proposal_id[:8]}")
        if not name:
            name = f"agent-{proposal_id[:8]}"
        
        domain = payload.get("domain", "general")
        capabilities = payload.get("capabilities", [domain, "respond"])
        
        # Validate: Agent requires non-empty name
        if len(name) < 1:
            self._log.warning("lattice.spawn_invalid_name", proposal_id=proposal_id)
            return

        agent = await self.register_agent(
            name=name,
            domain=domain,
            capabilities=capabilities,
            trust_level=TrustLevel.OBSERVED,
        )
        self._log.info(
            "agent_spawned_via_governance",
            agent_id=agent.id,
            proposal_id=proposal_id,
            name=name,
        )

    async def list_agents(self) -> list[Agent]:
        """List all registered agents."""
        return self._identity_registry.list_agents()

    # ------------------------------------------------------------------ #
    # Task management
    # ------------------------------------------------------------------ #

    async def submit_task(
        self,
        title: str,
        description: str,
        agent_id: str | None = None,
    ) -> Task:
        """Create and route a task."""
        task = await self._task_manager.create_task(
            title=title,
            created_by=agent_id or "system",
            input={"description": description},
        )

        # Create task in Lattice if available
        if self._lattice is not None and agent_id is not None:
            try:
                await self._lattice.create_task(
                    agent_id=agent_id,
                    task_type="user_request",
                    payload={"title": title, "description": description, "task_id": task.task_id},
                )
            except Exception:
                self._log.warning("lattice.task_create_failed", task_id=task.task_id, exc_info=True)

        self._log.info("task_submitted", task_id=task.task_id, title=title)
        return task

    async def get_task(self, task_id: str) -> Task:
        """Get task by ID."""
        task = await self._task_manager.get_task(task_id)
        if task is None:
            raise ValueError(f"Task not found: {task_id}")
        return task

    async def list_tasks(self, status: str | None = None) -> list[Task]:
        """List tasks, optionally filtered by status."""
        if status:
            return await self._task_manager.list_tasks(status=TaskStatus(status))
        return await self._task_manager.list_tasks()

    # ------------------------------------------------------------------ #
    # Execution pipeline
    # ------------------------------------------------------------------ #

    async def execute_task(self, task_id: str) -> ExecutionResult:
        """Execute a task through the full pipeline.

        Pipeline:
        1. Get task from TaskManager
        2. Check agent exists and is authorized
        3. Plan subtasks (AgentPlanner)
        4. For each subtask: execute → verify → store evidence → assess risk
        5. Update task status
        6. Audit log the full execution
        7. Return ExecutionResult
        """
        task = await self._task_manager.get_task(task_id)
        if task is None:
            raise ValueError(f"Task not found: {task_id}")

        # Update status
        await self._task_manager.update_status(task_id, TaskStatus.RUNNING)

        # Plan subtasks
        description = task.input.get("description", task.title)
        graph = self._planner.plan(description)

        # Ground the LLM in real system state — the actual registered
        # agents and their domains — so it answers as the real system
        # instead of roleplaying fiction.
        agent_roster = ", ".join(
            f"{a.name} ({a.domain})" for a in self._identity_registry.list_agents()
        ) or "none registered"
        system_context = (
            "SYSTEM FACTS (provided by the runtime, not fiction — treat as "
            "ground truth about the software you are running inside):\n"
            f"- You are the Butler module of The Agency, a real multi-agent "
            f"system executing on this machine right now.\n"
            f"- Registered agents (live registry): {agent_roster}\n"
            f"- This conversation is relayed through the Telegram gateway.\n\n"
            "Answer the user's message as the Butler. Be honest: these agents "
            "are real software components, and you may describe what they do. "
            "Never invent agents, missions, codenames, or claims about "
            "capabilities the roster doesn't show. If you don't know, say so.\n\n"
            f"User message: {description}"
        )

        # Execute each subtask
        subtask_results: list[dict[str, Any]] = []
        for subtask in graph.subtasks:
            # Execute
            exec_result = await self._executor.execute(
                task=TaskMessage(
                    task_id=task_id,
                    type="subtask",
                    content=system_context,
                    created_by=task.created_by,
                ),
                context={"agent_id": task.created_by, "subtask_id": subtask.subtask_id},
            )

            # Verify
            verification = self._verifier.verify(
                result=exec_result.output,
                criteria=["completeness", "accuracy", "safety"],
            )

            # Store evidence
            finding = Finding(
                target=task.title,
                evidence=str(exec_result.output),
                methodology="agent_execution",
                confidence=verification.score,
                affected_component=task.title,
                severity=Severity.LOW,
                verification_status=VerificationState.VERIFIED if verification.status is VerificationStatus.PASSED else VerificationState.UNVERIFIED,
            )
            await self._evidence_store.add_finding(finding)

            # Assess risk
            _, category = self._risk_engine.assess(finding)

            # Audit
            await self._audit_log.append(AuditEntry(
                timestamp=datetime.now(UTC),
                agent=task.created_by,
                task=task_id,
                target=task.title,
                authorization="policy_engine",
                capability="execute",
                action=f"subtask_{subtask.subtask_id}",
                result=exec_result.status.value,
                evidence={"finding_id": finding.id},
                model="orchestrator",
                model_version="0.1.0",
                tool_version="0.1.0",
                environment={"name": "default"},
            ))

            subtask_results.append({
                "subtask_id": subtask.subtask_id,
                "status": exec_result.status.value,
                "verification": verification.status.value,
                "risk_category": category.value,
                "output": exec_result.output,
            })

        # Update task status
        all_completed = all(r["status"] == "completed" for r in subtask_results)
        final_status = TaskStatus.COMPLETED if all_completed else TaskStatus.FAILED
        await self._task_manager.update_status(task_id, final_status)

        # Surface the real agent output: join subtask outputs (the actual
        # LLM responses) instead of a technical execution summary.
        outputs = [str(r["output"]) for r in subtask_results if r.get("output")]
        final_output = "\n\n".join(outputs) if outputs else f"Executed {len(subtask_results)} subtasks"

        # Store in memory
        await self._memory_store.store(MemoryItem(
            agent_id=task.created_by,
            content=f"Task '{task.title}' → {final_output[:500]}",
            tier=MemoryTier.NORMAL,
            importance=0.7,
            tags=["task", "execution"],
        ))

        result = ExecutionResult(
            task_id=task_id,
            status=ExecutionStatus.COMPLETED if all_completed else ExecutionStatus.FAILED,
            output=final_output,
            attempts=len(subtask_results),
            duration_s=0.0,
        )

        self._log.info(
            "task_executed",
            task_id=task_id,
            status=final_status.value,
            subtasks=len(subtask_results),
        )
        return result

    # ------------------------------------------------------------------ #
    # Memory
    # ------------------------------------------------------------------ #

    async def search_memory(
        self,
        query: str,
        agent_id: str | None = None,
        limit: int = 10,
    ) -> list[MemoryItem]:
        """Search memory."""
        return await self._memory_retrieval.semantic_search(query, agent_id=agent_id, limit=limit)

    # ------------------------------------------------------------------ #
    # Health
    # ------------------------------------------------------------------ #

    @property
    def llm_adapter(self) -> LLMAdapter:
        """The shared LLM adapter used for agent execution."""
        return self._llm_adapter

    @property
    def executor(self) -> AgentExecutor:
        """The task executor (wired to :attr:`llm_adapter`)."""
        return self._executor

    async def health_check(self) -> dict[str, Any]:
        """Check all components."""
        lattice_status = None
        if self._lattice is not None:
            try:
                lattice_status = await self._lattice.get_status()
            except Exception as exc:
                lattice_status = {"error": str(exc)}

        return {
            "status": "ok" if self._started else "stopped",
            "orchestrator": "running" if self._started else "stopped",
            "identity_registry": len(self._identity_registry.list_agents()),
            "runtime_registry": len(self._runtime_registry.list_agents()),
            "tasks": len(await self._task_manager.list_tasks()),
            "memory": "ok",
            "evidence": "ok",
            "risk": "ok",
            "bridges": len(self._bridge_coordinator.list_bridges()),
            "lattice": lattice_status,
            "llm": {
                "provider": self._llm_adapter.provider,
                "model": self._llm_adapter.model,
                "echo_mode": self._llm_adapter.echo_mode,
            },
            "timestamp": datetime.now(UTC).isoformat(),
        }
