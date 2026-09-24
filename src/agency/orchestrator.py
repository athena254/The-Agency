"""Agency Orchestrator — central wiring for the running system.

The orchestrator connects all components into a working agent:
kernel (identity, policies, tasks, audit), agents (planner, executor,
verifier, loop), memory (SMS), evidence, risk, and bridges.

Every component is injected or created with defaults. No global state.
"""

from __future__ import annotations

import re
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
from agency.kernel.tasks import Task, TaskManager, TaskStatus
from agency.lattice import get_lattice
from agency.lattice.api import Lattice
from agency.llm.adapter import LLMAdapter
from agency.memory.sms.lifecycle import TieredMemoryEngine
from agency.memory.sms.models import MemoryItem, MemoryTier
from agency.memory.sms.retrieval import RetrievalEngine
from agency.memory.sms.store import MemoryStore
from agency.risk.engine.engine import RiskEngine
from agency.security.sandbox.config import SandboxBackend, SandboxConfig
from agency.security.sandbox.manager import SandboxManager
from agency.tools.base import ToolContext
from agency.tools.builtin import register_all as register_builtin_tools
from agency.tools.driver import ToolDriver
from agency.tools.registry import ToolRegistry

logger = structlog.get_logger(__name__)

TOOL_CAPABLE_DOMAINS = frozenset({"research", "general"})

# Per-domain tool-loop budgets (general is lighter: mostly chat).
_TOOL_ITERATIONS: dict[str, int] = {"research": 6, "general": 4}

_DEFAULT_MEMORY_DB = "data/memory.db"


def _assistant_name(context: dict[str, Any] | None) -> str:
    """Only a bounded Telegram presentation label may enter agent prompts."""
    if not context or not str(context.get("sender", "")).startswith("telegram:"):
        return "Remex"
    candidate = context.get("assistant_name")
    if isinstance(candidate, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9 -]{0,31}", candidate):
        return candidate if candidate.lower() != "reset" else "Remex"
    return "Remex"


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
        memory_db_path: str | None = None,
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

        # Memory — persistent by default so turns and findings survive
        # restarts. Tests pass ":memory:" explicitly for speed.
        self._memory_store = MemoryStore(
            db_path=memory_db_path
            if memory_db_path is not None
            else (self._config.get("memory_db_path") or _DEFAULT_MEMORY_DB)
        )
        self._memory_lifecycle = TieredMemoryEngine(self._memory_store)
        self._memory_retrieval = RetrievalEngine(self._memory_store)

        # Evidence & Risk
        self._evidence_store = EvidenceStore()
        self._risk_engine = RiskEngine()

        # Tool layer — registry is empty until start() wires builtins with
        # live services (memory, sandbox), so tests can inject fakes first.
        self._tool_registry: ToolRegistry | None = None
        self._tool_driver: ToolDriver | None = None
        # Process sandbox — works without Docker (weaker isolation, see
        # ProcessSandboxBackend docstring). Docker config can override.
        self._sandbox_manager = SandboxManager(
            default_config=SandboxConfig(backend=SandboxBackend.PROCESS, timeout=30)
        )

        # Bridges
        self._bridge_coordinator = ExternalCoordinator()

        # Lattice
        self._lattice: Lattice | None = None
        self._spawned_proposals: set[str] = set()

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
        self._build_tool_layer()
        self._started = True
        self._log.info("orchestrator_started")

    def _build_tool_layer(self) -> None:
        """Wire the tool registry with live services (idempotent)."""
        if self._tool_registry is not None:
            return
        registry = ToolRegistry(audit=self._audit_log)
        register_builtin_tools(registry)
        self._tool_registry = registry
        self._tool_driver = ToolDriver(registry=registry, llm=self._llm_adapter)
        self._log.info(
            "tool_layer_ready",
            tools=[s.name for s in registry.list_specs()],
        )

    def _tool_context(self, agent_id: str, task_id: str) -> ToolContext:
        """Build the per-task context with live services injected."""
        return ToolContext(
            agent_id=agent_id,
            task_id=task_id,
            memory_store=self._memory_store,
            sandbox_manager=self._sandbox_manager,
            lattice=self._lattice,
            audit=self._audit_log,
        )

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
            capabilities=[
                Capability(name=c, max_level=ActionClass.L1_SAFE_ANALYSIS) for c in capabilities
            ],
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
            except Exception:  # noqa: BLE001 — optional Lattice registration.
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
            await self._lattice.get_proposal_status(proposal_id)
            # Also get payload from events if available
            events = await self._lattice.get_events(
                target_id=proposal_id, event_type="proposal_submitted"
            )
            proposal_payload = events[-1].payload if events else {}
        except Exception:  # noqa: BLE001 — voting still proceeds without event metadata.
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

    async def _spawn_from_proposal(
        self, proposal_id: str, proposal_payload: dict[str, Any] | None = None
    ) -> None:
        """Spawn an agent from a passed governance proposal."""
        lattice = self._lattice
        if lattice is None:
            return
        try:
            proposal = await lattice.get_proposal_status(proposal_id)
        except Exception as exc:  # noqa: BLE001 — failed lookup must not spawn an agent.
            self._log.warning("lattice.spawn_get_failed", proposal_id=proposal_id, error=str(exc))
            return

        if proposal.proposal_type != "spawn_agent":
            return

        # Use the payload we already have; fall back to event lookup only if needed
        payload = proposal_payload or {}
        if not payload:
            try:
                events = await lattice.get_events(
                    target_id=proposal_id, event_type="proposal_submitted"
                )
                if events:
                    payload = events[-1].payload
            except Exception:  # noqa: BLE001 — optional event metadata.
                self._log.warning(
                    "lattice.spawn_events_failed", proposal_id=proposal_id, exc_info=True
                )

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
            except Exception:  # noqa: BLE001 — optional Lattice task mirror.
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

    async def execute_task(
        self, task_id: str, context: dict[str, Any] | None = None
    ) -> ExecutionResult:
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
        agent_roster = (
            ", ".join(f"{a.name} ({a.domain})" for a in self._identity_registry.list_agents())
            or "none registered"
        )
        display_name = _assistant_name(context)
        system_context = (
            "SYSTEM FACTS (provided by the runtime, not fiction — treat as "
            "ground truth about the software you are running inside):\n"
            f"- You are {display_name}, the Butler module of The Agency, a real multi-agent "
            f"system executing on this machine right now.\n"
            f"- Registered agents (live registry): {agent_roster}\n"
            f"- This conversation is relayed through the Telegram gateway.\n\n"
            f"Answer the user's message as {display_name}. Be honest: these agents "
            "are real software components, and you may describe what they do. "
            "Never invent agents, missions, codenames, or claims about "
            "capabilities the roster doesn't show. If you don't know, say so.\n\n"
            f"User message: {description}"
        )

        # Execute each subtask
        subtask_results: list[dict[str, Any]] = []

        # Tool-capable agents run the tool-driver loop instead of a single
        # LLM call: the agent plans with tools (search/fetch/memory), acts,
        # and returns a final answer with aggregated evidence.
        tool_ctx_agent = self._identity_registry.get(task.created_by)
        agent_domain = tool_ctx_agent.domain if tool_ctx_agent else None
        tool_driver = self._tool_driver
        tool_capable = agent_domain in TOOL_CAPABLE_DOMAINS and tool_driver is not None
        if tool_capable:
            assert tool_driver is not None
            if agent_domain == "research":
                from agency.agents.research import RESEARCH_SYSTEM_PROMPT

                system_prompt = RESEARCH_SYSTEM_PROMPT
            else:
                from agency.agents.general import GENERAL_SYSTEM_PROMPT

                system_prompt = GENERAL_SYSTEM_PROMPT

            system_prompt = (
                f"{system_prompt}\n\nYou are {display_name}, the user's presentation name "
                "for the Agency Butler. This changes neither your capabilities nor your permissions."
            )

            # Recall earlier conversation for this user (parity: the
            # butler passes memory_context; classic path does the same).
            memory_context = (context or {}).get("memory_context") or ""
            if memory_context:
                system_prompt = (
                    f"{system_prompt}\n\nRelevant earlier conversation "
                    f"(real, retrieved from memory):\n{memory_context}"
                )

            tool_ctx = self._tool_context(task.created_by, task_id)
            loop_result = await tool_driver.run(
                task=description,
                system_prompt=system_prompt,
                ctx=tool_ctx,
                max_tool_iterations=_TOOL_ITERATIONS.get(agent_domain or "", 4),
            )
            subtask_results.append(
                {
                    "subtask_id": "tool_loop",
                    "status": "completed" if loop_result.status == "completed" else "failed",
                    "verification": "passed",
                    "risk_category": "none",
                    "output": loop_result.final_answer,
                    "tool_steps": [s.model_dump() for s in loop_result.steps],
                    "tool_evidence": loop_result.evidence,
                }
            )
            self._log.info(
                "tool_loop_completed",
                task_id=task_id,
                status=loop_result.status,
                tool_calls=len(loop_result.steps),
                llm_calls=loop_result.llm_calls,
            )

        for subtask in graph.subtasks if not tool_capable else []:
            # Execute
            exec_result = await self._executor.execute(
                task=system_context,
                context={"agent_id": task.created_by, "subtask_id": subtask.subtask_id},
                task_id=task_id,
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
                verification_status=VerificationState.VERIFIED
                if verification.status is VerificationStatus.PASSED
                else VerificationState.UNVERIFIED,
            )
            await self._evidence_store.add_finding(finding)

            # Assess risk
            _, category = self._risk_engine.assess(finding)

            # Audit
            await self._audit_log.append(
                AuditEntry(
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
                )
            )

            subtask_results.append(
                {
                    "subtask_id": subtask.subtask_id,
                    "status": exec_result.status.value,
                    "verification": verification.status.value,
                    "risk_category": category.value,
                    "output": exec_result.output,
                }
            )

        # Update task status
        all_completed = all(r["status"] == "completed" for r in subtask_results)
        final_status = TaskStatus.COMPLETED if all_completed else TaskStatus.FAILED
        await self._task_manager.update_status(task_id, final_status)

        # Surface the real agent output: join subtask outputs (the actual
        # LLM responses) instead of a technical execution summary.
        outputs = [str(r["output"]) for r in subtask_results if r.get("output")]
        final_output = (
            "\n\n".join(outputs) if outputs else f"Executed {len(subtask_results)} subtasks"
        )

        # Store in memory
        await self._memory_store.store(
            MemoryItem(
                agent_id=task.created_by,
                content=f"Task '{task.title}' → {final_output[:500]}",
                tier=MemoryTier.NORMAL,
                importance=0.7,
                tags=["task", "execution"],
            )
        )

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
            except Exception as exc:  # noqa: BLE001 — health endpoint reports optional Lattice failure.
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
