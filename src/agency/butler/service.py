"""Butler gateway — receive messages, route to agents, return responses.

:class:`ButlerService` is the single entry point for conversational traffic:

1. Validate + truncate the inbound message.
2. Route to an :class:`Agent` (LLM decision when configured, else keyword).
3. Execute via :class:`AgencyOrchestrator` (submit + execute a task).
4. Audit-log every step and persist the turn in memory.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from agency.agents.assessment import (
    ExecutionAssessment,
    assessed_output,
    bound_assessment,
    render_output,
)
from agency.agents.executor import ExecutionStatus
from agency.butler.commands import CommandError, ThreadCommands
from agency.butler.config import ButlerConfig
from agency.butler.context import ThreadContextEngine
from agency.butler.router import MessageRouter
from agency.butler.threads import Thread, ThreadMessage, ThreadStore, Workspace
from agency.kernel.audit import AuditEntry, AuditLog
from agency.kernel.identity import Agent
from agency.llm.metered import MeteredDenied, MeteredError
from agency.memory.sms.models import MemoryItem, MemoryTier
from agency.memory.sms.retrieval import RetrievalEngine
from agency.memory.sms.store import MemoryStore
from agency.orchestrator import AgencyOrchestrator
from agency.tools.base import BetaPrincipal

if TYPE_CHECKING:
    from agency.admission.models import OwnerExecutionGrant
    from agency.lattice.api import Lattice
    from agency.llm.metered import MeteredModel
    from agency.tools.registry import ToolRegistry

log = structlog.get_logger(__name__)

LLMCallable = Callable[[str, dict[str, Any]], Any | Awaitable[Any]]

_DEFAULT_DOMAINS: tuple[str, ...] = (
    "security",
    "memory",
    "evidence",
    "risk",
    "research",
    "general",
)


@dataclass(frozen=True)
class ButlerReply:
    """A read-only conversational turn with an explicit execution status."""

    text: str
    status: str
    assessment: ExecutionAssessment | None = None
    task_id: str | None = None


class ButlerExecutionError(RuntimeError):
    """Non-completed orchestrator execution without raw output or secrets."""


class ButlerAuditError(RuntimeError):
    """A required audit acknowledgement is unavailable; effects may be uncertain."""

    def __init__(self) -> None:
        super().__init__("butler audit unavailable")


class ButlerService:
    """Conversational gateway over the orchestrator."""

    def __init__(
        self,
        config: ButlerConfig | None = None,
        orchestrator: AgencyOrchestrator | None = None,
        router: MessageRouter | None = None,
        audit_log: AuditLog | None = None,
        memory_store: MemoryStore | None = None,
        llm: LLMCallable | None = None,
        thread_store: ThreadStore | None = None,
    ) -> None:
        self._config = config or ButlerConfig()
        self._orchestrator = orchestrator or AgencyOrchestrator()
        self._router = router or MessageRouter()
        self._owns_audit = audit_log is None
        self._audit = audit_log if audit_log is not None else AuditLog(":memory:")
        # Share the orchestrator's memory store — one store, one truth.
        # (A standalone MemoryStore here was a second, separate DB.)
        self._memory = memory_store or self._orchestrator._memory_store
        self._memory_retrieval = RetrievalEngine(self._memory)
        self._threads = thread_store
        self._owns_threads = thread_store is None
        self._engine: ThreadContextEngine | None = None
        self._commands: ThreadCommands | None = None
        self._lattice: Lattice | None = None  # Will be set from orchestrator
        self._llm = llm
        self._log = structlog.get_logger(__name__)
        self._started = False
        self._seeded = False
        self._orchestrator_active = False

    @property
    def lattice(self) -> Lattice | None:
        """Reference to the orchestrator's Lattice instance, if available."""
        if self._lattice is not None:
            return self._lattice
        if hasattr(self._orchestrator, "_lattice"):
            return self._orchestrator._lattice
        return None

    # ------------------------------------------------------------------ #
    # Properties
    # ------------------------------------------------------------------ #

    @property
    def config(self) -> ButlerConfig:
        """Active runtime configuration."""
        return self._config

    @property
    def router(self) -> MessageRouter:
        """Underlying message router."""
        return self._router

    @property
    def orchestrator(self) -> AgencyOrchestrator:
        """Underlying agency orchestrator."""
        return self._orchestrator

    @property
    def thread_store(self) -> ThreadStore:
        """Lazy persistent store; an injected store retains caller ownership."""
        if self._threads is None:
            memory_path = self._memory.db_path
            if memory_path == ":memory:":
                thread_path = ":memory:"
            else:
                path = Path(memory_path).with_name("threads.db")
                path.parent.mkdir(parents=True, exist_ok=True)
                thread_path = str(path)
            self._threads = ThreadStore(thread_path)
        return self._threads

    @property
    def thread_context_engine(self) -> ThreadContextEngine:
        """Layered per-thread context engine over the shared stores."""
        if self._engine is None:
            self._engine = ThreadContextEngine(self.thread_store, self._memory)
        return self._engine

    @property
    def commands(self) -> ThreadCommands:
        """Deterministic thread-command executor."""
        if self._commands is None:
            self._commands = ThreadCommands(self.thread_store, self.thread_context_engine)
        return self._commands

    def create_workspace(self, owner: str, title: str) -> Workspace:
        """Create a persistent workspace for a trusted caller identity."""
        return self.thread_store.create_workspace(owner, title)

    def create_thread(
        self, owner: str, workspace_id: str, title: str, parent_thread_id: str | None = None
    ) -> Thread:
        """Create an isolated thread (or provenance-only branch)."""
        return self.thread_store.create_thread(owner, workspace_id, title, parent_thread_id)

    def list_thread_messages(self, owner: str, thread_id: str) -> list[ThreadMessage]:
        """Read only the specified owner's thread history."""
        return self.thread_store.list_messages(owner, thread_id)

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def start(self) -> None:
        """Initialise stores and services, rolling back failed startup."""
        if self._started:
            return
        try:
            await self._audit.initialize()
            # The orchestrator alone initializes/closes its shared memory.
            self._orchestrator_active = True
            await self._orchestrator.start()
            if self._memory is not self._orchestrator._memory_store:
                await self._memory.initialize()
            self._lattice = self._orchestrator._lattice
            await self._ensure_default_agents()
        except BaseException:
            try:
                await self.stop()
            except BaseException as exc:  # noqa: BLE001 — finish cleanup before re-raising.
                self._log.warning("butler.rollback_failed", error_type=type(exc).__name__)
            raise
        self._started = True
        self._log.info("butler.started")

    async def stop(self) -> None:
        """Stop the orchestrator; close only Butler-owned audit/thread stores."""
        self._started = False
        self._lattice = None
        self._engine = None
        self._commands = None
        error: BaseException | None = None
        if self._orchestrator_active:
            try:
                await self._orchestrator.stop()
            except BaseException as exc:  # noqa: BLE001 — finish cleanup before re-raising.
                error = exc
            else:
                self._orchestrator_active = False
        if self._owns_threads and self._threads is not None:
            try:
                self._threads.close()
            except BaseException as exc:  # noqa: BLE001 — finish cleanup before re-raising.
                if error is None:
                    error = exc
            else:
                self._threads = None
        if self._owns_audit:
            try:
                await self._audit.close()
            except BaseException as exc:  # noqa: BLE001 — finish cleanup before re-raising.
                if error is None:
                    error = exc
        if error is not None:
            raise error
        self._log.info("butler.stopped")

    async def _ensure_started(self) -> None:
        if not self._started:
            await self.start()

    async def _ensure_default_agents(self) -> None:
        """Seed one agent per default domain if the router/orchestrator is empty."""
        if self._seeded:
            return
        existing = {a.domain.strip().lower() for a in self._router.list_agents()}
        try:
            orch_agents = await self._orchestrator.list_agents()
            existing |= {a.domain.strip().lower() for a in orch_agents}
        except Exception:  # noqa: BLE001 — optional seeding must not break startup.
            self._log.warning("butler.seed_list_failed")
        for domain in _DEFAULT_DOMAINS:
            if domain not in existing:
                try:
                    agent = await self._orchestrator.register_agent(
                        name=f"{domain}-agent",
                        domain=domain,
                        capabilities=[domain, "respond"],
                    )
                    self._router.register_agent(agent)
                    self._log.info("butler.agent_seeded", domain=domain, agent_id=agent.id)
                except Exception:  # noqa: BLE001 — continue seeding other domains.
                    self._log.exception("butler.seed_failed", domain=domain)
        self._seeded = True

    # ------------------------------------------------------------------ #
    # Core API
    # ------------------------------------------------------------------ #

    async def handle_message(
        self,
        message: str,
        sender: str,
        context: dict[str, Any],
        *,
        memory_enabled: bool = True,
        beta_principal: BetaPrincipal | None = None,
        commands_enabled: bool = False,
    ) -> str:
        """Validate and execute a turn; anonymous channels disable memory.

        Only trusted in-process admission may supply ``beta_principal``. Beta
        execution is dormant: reject it before startup, routing or persistence.

        ``commands_enabled`` opts a trusted channel into deterministic thread
        commands (``/new``, ``/threads``, ``/switch``, ``/current``) and the
        persisted active-thread fallback. Unauthenticated channels must leave
        it off: commands create and mutate owner-scoped thread state.
        """
        if beta_principal is not None and not isinstance(beta_principal, BetaPrincipal):
            raise TypeError("beta_principal must be a BetaPrincipal")
        if beta_principal is not None:
            raise RuntimeError("beta execution disabled: policy, budget and audit gates missing")
        await self._ensure_started()
        if not message or not message.strip():
            raise ValueError("message must not be empty.")
        text = message.strip()
        if len(text) > self._config.max_message_length:
            self._log.warning(
                "butler.message_truncated",
                length=len(text),
                max_length=self._config.max_message_length,
            )
            text = text[: self._config.max_message_length]

        merged: dict[str, Any] = {**context, "sender": sender}
        merged.pop("beta_principal", None)  # Context is data, never an identity channel.
        merged.pop("memory_context", None)  # Recalled memory is never caller-supplied.
        thread_id = merged.get("thread_id")
        if thread_id is not None:
            # Authorize before routing, recalling, executing, or writing anything.
            self.thread_store.get_thread(sender, thread_id)
        started = datetime.now(UTC)

        # Deterministic conversation commands run before any routing. They are
        # opt-in per call so unauthenticated channels (public HTTP) can never
        # create or mutate thread state.
        await self._audit_append(
            agent=None,
            action="butler.request",
            result="received",
            target="converse",
            evidence={"message_length": len(text)},
        )
        parsed = ThreadCommands.parse(text) if commands_enabled else None
        if parsed is not None:
            command, args = parsed
            return await self._run_command(sender, command, args, started)

        # Effective thread: an explicit authorized thread_id wins; otherwise a
        # persisted active thread applies only when the caller opted into
        # conversational commands (trusted channels).
        if thread_id is None and commands_enabled:
            active = self.thread_store.get_active_thread(sender)
            if active is not None:
                thread_id = active.id
        engine = self.thread_context_engine if thread_id is not None else None

        # Recall earlier conversation with this sender so the agent has
        # continuity (parity with OpenClaw/Hermes). Best-effort — never
        # blocks the turn.
        if engine is not None:
            # One source per turn: layered thread context only — sender-global
            # memory is never blended into a thread's prompt.
            assert thread_id is not None  # engine is non-None only with a thread.
            assembled = await engine.assemble(sender, thread_id, text)
            memory_context = assembled.render()
        elif memory_enabled:
            memory_context = await self._recall_history(sender, text)
        else:
            memory_context = ""
        if memory_context:
            merged["memory_context"] = memory_context

        agent = await self.route(text, merged)
        await self._audit_append(
            agent=agent.id,
            action="butler.route",
            result="routed",
            target=agent.id,
            evidence={"message_length": len(text)},
        )

        try:
            execution = self.execute(agent, text, merged)
            response = await asyncio.wait_for(execution, timeout=self._config.timeout)
            result = "completed"
        except (ButlerAuditError, MeteredDenied, MeteredError):
            raise
        except TimeoutError as exc:
            response = f"Request timed out after {self._config.timeout:g}s. Please try again."
            result = "timeout"
            self._log.warning("butler.execute_timeout", error_type=type(exc).__name__)
        except Exception as exc:  # noqa: BLE001 — execution errors become a failed turn.
            response = "Sorry, I could not process that request."
            result = "failed"
            self._log.warning("butler.execute_failed", error_type=type(exc).__name__)

        await self._audit_append(
            agent=agent.id,
            action="butler.handle_message",
            result=result,
            target=agent.id,
            evidence={
                "message_length": len(text),
                "response_length": len(response),
                "duration_s": (datetime.now(UTC) - started).total_seconds(),
            },
        )
        if thread_id is not None and engine is not None:
            # Persist the conversation only after required gateway acknowledgement.
            # These stores are still independent, not a cross-store transaction.
            self.thread_store.append_message(sender, thread_id, "user", text)
            self.thread_store.append_message(sender, thread_id, "assistant", response)
            await engine.record_turn(sender, thread_id, text, response)
            await self._maybe_summarize(sender, thread_id)
        elif thread_id is not None:
            self.thread_store.append_message(sender, thread_id, "user", text)
            self.thread_store.append_message(sender, thread_id, "assistant", response)
        elif memory_enabled:
            await self._store_turn(sender, agent, text, response)
        return response

    async def handle_owner_message(
        self,
        message: str,
        thread_id: str,
        *,
        grant: OwnerExecutionGrant,
        model: MeteredModel,
        registry: ToolRegistry,
    ) -> ButlerReply:
        """Carry an admitted turn with thread-local context; never start legacy services."""
        import json

        from agency.admission.models import OwnerExecutionGrant, PilotError

        if type(grant) is not OwnerExecutionGrant or self._threads is None or self._owns_threads:
            raise PilotError("forbidden")
        grant.__post_init__()
        row = await grant.budget._store.get_request(grant.lease.principal, grant.lease.request_id)
        grant.__post_init__()
        if row.task_id != grant.lease.task_id or row.thread_id != thread_id:
            raise PilotError("forbidden")
        owner = grant.lease.principal.owner_id
        try:
            self._threads.get_thread(owner, thread_id)
            context = await self.thread_context_engine.assemble(
                owner, thread_id, message, local_only=True
            )
        except KeyError:
            raise PilotError("not_found") from None
        result = await self._orchestrator.execute_owner_request(
            message,
            grant=grant,
            model=model,
            registry=registry,
            memory_context=json.dumps(context.render(), ensure_ascii=False),
        )
        if result.task_id != row.task_id or result.status is not ExecutionStatus.COMPLETED:
            raise PilotError("conflict")
        text = render_output(result.output)
        assessment = bound_assessment(
            result.assessment,
            task_id=row.task_id or "",
            execution_status=result.status.value,
            output=text,
        )
        if assessment is None:
            raise PilotError("conflict")
        return ButlerReply(text, result.status.value, assessment, result.task_id)

    async def handle_isolated_message(self, message: str, sender: str) -> ButlerReply:
        """Run a read-only, tool-free, memory-free turn for a local caller.

        Unlike :meth:`handle_message`, this path deliberately does **not**:

        * recall or persist memory (``memory_enabled=False`` alone is not
          enough — the tool driver could still reach ``memory_query`` /
          ``memory_write``),
        * run the tool driver, so no tool is reachable even if the model asks,
        * mirror the private text to the Lattice,
        * read or write per-owner threads,
        * accept caller-supplied context, scope or privilege.

        Only the fixed ``sender`` chosen by the local adapter is forwarded. A
        :class:`ButlerReply` carries the real execution status so the caller
        can fail closed instead of showing a failed turn as success.
        """
        await self._ensure_started()
        if not message or not message.strip():
            raise ValueError("message must not be empty.")
        text = message.strip()
        if len(text) > self._config.max_message_length:
            self._log.warning(
                "butler.message_truncated",
                length=len(text),
                max_length=self._config.max_message_length,
            )
            text = text[: self._config.max_message_length]

        # No caller-supplied context is trusted; only the fixed sender.
        merged: dict[str, Any] = {"sender": sender}
        started = datetime.now(UTC)
        await self._audit_append(
            agent=None,
            action="butler.isolated_request",
            result="received",
            target="isolated",
            evidence={"message_length": len(text)},
        )
        agent = await self.route(text, merged)
        await self._audit_append(
            agent=agent.id,
            action="butler.route_isolated",
            result="routed",
            target=agent.id,
            evidence={"message_length": len(text)},
        )

        status = "failed"
        reply_text = "Sorry, I could not process that request."
        assessment = None
        task_id = None
        try:
            result = await asyncio.wait_for(
                self._orchestrator.converse_isolated(text, agent.id, context=merged),
                timeout=self._config.timeout,
            )
            status = str(result.status.value)
            output = result.output
            reply_text = render_output(output)
            task_id = getattr(result, "task_id", None)
            assessment = bound_assessment(
                getattr(result, "assessment", None),
                task_id=task_id or "",
                execution_status=status,
                output=output,
            )
        except (ButlerAuditError, MeteredDenied, MeteredError):
            raise
        except TimeoutError as exc:
            status = "timeout"
            reply_text = f"Request timed out after {self._config.timeout:g}s. Please try again."
            self._log.warning("butler.isolated_timeout", error_type=type(exc).__name__)
        except Exception as exc:  # noqa: BLE001 — never surface raw exception text or stack.
            status = "failed"
            self._log.warning("butler.isolated_failed", error_type=type(exc).__name__)

        # Sanitized audit: message/response bodies are never persisted here.
        await self._audit_append(
            agent=agent.id,
            action="butler.handle_isolated_message",
            result=status,
            target=agent.id,
            evidence={
                "message_length": len(text),
                "response_length": len(reply_text),
                "duration_s": (datetime.now(UTC) - started).total_seconds(),
            },
        )
        return ButlerReply(text=reply_text, status=status, assessment=assessment, task_id=task_id)

    async def _run_command(self, sender: str, command: str, args: str, started: datetime) -> str:
        """Execute a parsed command; errors become user-visible replies."""
        outcome = "ok"
        try:
            command_result = self.commands.execute(sender, command, args)
            reply = command_result.text
        except CommandError as exc:
            reply = str(exc)
            outcome = "error"
        command_code = command if command in {"new", "threads", "switch", "current"} else "unknown"
        await self._audit_append(
            agent=None,
            action="butler.command",
            result=outcome,
            target=f"/{command_code}",
            evidence={
                "command": command_code,
                "duration_s": (datetime.now(UTC) - started).total_seconds(),
            },
        )
        return reply

    async def _maybe_summarize(self, owner: str, thread_id: str) -> None:
        """Compress older turns into the rolling summary (deterministic).

        Runs once a thread holds at least two full windows of messages;
        the newest window stays verbatim. Best-effort: summary failures
        never fail the turn.
        """
        try:
            engine = self.thread_context_engine
            count = self.thread_store.count_messages(owner, thread_id)
            if count >= engine.window * 2:
                engine.summarize_prefix(owner, thread_id)
        except Exception:  # noqa: BLE001 — summarization must not block turns.
            self._log.debug("butler.summarize_failed", thread_id=thread_id)

    async def route(self, message: str, context: dict[str, Any]) -> Agent:
        """Select the handling agent, preferring the LLM when configured."""
        await self._audit_append(
            agent=None,
            action="butler.route_intent",
            result="pending",
            target="routing",
            evidence={"message_length": len(message)},
        )
        llm = self._llm or self._build_llm_from_config()
        if llm is not None:
            try:
                domain = await self._llm_route(llm, message, context)
                if domain:
                    for agent in self._router.list_agents():
                        if agent.domain.strip().lower() == domain.strip().lower():
                            self._log.info("butler.llm_routed", domain=domain)
                            return agent
                    self._log.warning("butler.llm_unknown_domain", domain=domain)
            except (ButlerAuditError, MeteredDenied, MeteredError):
                raise
            except Exception as exc:  # noqa: BLE001 — ordinary legacy failures may fall back.
                self._log.warning("butler.llm_route_failed", error_type=type(exc).__name__)
        return await self._router.route(message, context)

    async def execute(
        self,
        agent: Agent,
        message: str,
        context: dict[str, Any],
        *,
        beta_principal: BetaPrincipal | None = None,
    ) -> str:
        """Run a turn; refuse beta before creating a task on the legacy registry.

        This typed argument is reserved for future authenticated admission,
        budget and policy gates. Context and sender are never identity grants.
        """
        if beta_principal is not None and not isinstance(beta_principal, BetaPrincipal):
            raise TypeError("beta_principal must be a BetaPrincipal")
        if beta_principal is not None:
            raise RuntimeError("beta execution disabled: policy, budget and audit gates missing")
        await self._audit_append(
            agent=agent.id,
            action="butler.execute_intent",
            result="pending",
            target=agent.id,
            evidence={"message_length": len(message)},
        )
        task = await self._orchestrator.submit_task(
            title=f"Butler message from {context.get('sender', 'unknown')}",
            description=message,
            agent_id=agent.id,
        )
        result = await self._orchestrator.execute_task(task.task_id, context=context)
        if result.status is not ExecutionStatus.COMPLETED:
            await self._audit_append(
                agent=agent.id,
                action="butler.execute",
                result=result.status.value,
                target=task.task_id,
                evidence={"task_id": task.task_id},
            )
            self._log.warning(
                "butler.execute_failed_status",
                agent_id=agent.id,
                task_id=task.task_id,
                status=result.status.value,
            )
            raise ButlerExecutionError(f"Task execution failed with status: {result.status.value}")
        output = assessed_output(
            result.output,
            assessment=getattr(result, "assessment", None)
            if getattr(result, "task_id", None) == task.task_id
            else None,
            task_id=task.task_id,
            execution_status=result.status.value,
        )
        await self._audit_append(
            agent=agent.id,
            action="butler.execute",
            result=result.status.value,
            target=task.task_id,
            evidence={"task_id": task.task_id, "output_length": len(output)},
        )
        self._log.info(
            "butler.executed", agent_id=agent.id, task_id=task.task_id, status=result.status.value
        )
        return output

    # ------------------------------------------------------------------ #
    # LLM routing
    # ------------------------------------------------------------------ #

    def _build_llm_from_config(self) -> LLMCallable | None:
        if not self._config.llm_enabled:
            return None
        provider = self._config.llm_provider.strip().lower()
        if provider == "ollama":
            return self._ollama_llm
        if provider in ("openai", "anthropic", "custom"):
            return self._http_llm
        self._log.warning("butler.unknown_llm_provider", provider=provider)
        return None

    async def _llm_route(
        self, llm: LLMCallable, message: str, context: dict[str, Any]
    ) -> str | None:
        domains = sorted({a.domain for a in self._router.list_agents()} or set(_DEFAULT_DOMAINS))
        prompt = (
            "Classify the user message into exactly one of these domains: "
            + ", ".join(domains)
            + ". Reply with only the domain name.\nMessage: "
            + message[:2000]
        )
        raw = llm(prompt, dict(context))
        if asyncio.iscoroutine(raw) or isinstance(raw, Awaitable):
            raw = await raw
        text = str(raw).strip().lower()
        for domain in domains:
            if domain.lower() in text:
                return domain
        return None

    async def _ollama_llm(self, prompt: str, context: dict[str, Any]) -> str:
        """Route via a local Ollama server (lazy import so core stays light)."""
        _ = context
        try:
            import ollama  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "ollama package not installed (pip install theagency[ollama])."
            ) from exc
        model = self._config.llm_model or "llama3.1"
        client = ollama.AsyncClient()
        response = await client.generate(model=model, prompt=prompt)
        if isinstance(response, dict):
            return str(response.get("response", ""))
        return str(getattr(response, "response", response))

    async def _http_llm(self, prompt: str, context: dict[str, Any]) -> str:
        """Route via an OpenAI-compatible HTTP endpoint (lazy import)."""
        _ = context
        api_key = os.environ.get(self._config.api_key_env_var, "")
        if not api_key:
            raise RuntimeError(f"API key env var {self._config.api_key_env_var!r} is not set.")
        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError("httpx is required for HTTP LLM routing.") from exc
        model = self._config.llm_model or "gpt-4o-mini"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 32,
                    "temperature": 0,
                },
            )
            resp.raise_for_status()
            data = resp.json()
        return str(data["choices"][0]["message"]["content"])

    # ------------------------------------------------------------------ #
    # Persistence helpers
    # ------------------------------------------------------------------ #

    async def _audit_append(
        self,
        *,
        agent: str | None,
        action: str,
        result: str,
        target: str | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> None:
        try:
            entry = AuditEntry(
                timestamp=datetime.now(UTC),
                agent=agent,
                target=target,
                authorization="butler",
                capability="converse",
                action=action,
                result=result,
                evidence=evidence,
                model=self._config.llm_model or None,
                model_version=self._config.llm_provider,
                tool_version="butler-0.1.0",
                environment={"name": "butler"},
            )
            acknowledgement = await self._audit.append(entry)
            if type(acknowledgement) is not str or acknowledgement != entry.entry_id:
                raise ValueError("audit acknowledgement mismatch")
        except Exception as exc:  # noqa: BLE001 — no private storage bodies or tracebacks.
            self._log.warning("butler.audit_failed", error_type=type(exc).__name__)
            raise ButlerAuditError from None

    async def _recall_history(self, sender: str, message: str) -> str:
        """Retrieve earlier turns with ``sender`` relevant to ``message``.

        Returns a formatted context block, or "" when nothing relevant
        is found. Best-effort: any failure returns "".
        """
        try:
            items = await self._memory_retrieval.semantic_search(
                message, agent_id=f"chat-{sender}", limit=5
            )
        except Exception:  # noqa: BLE001 — optional recall cannot block a turn.
            self._log.debug("butler.history_recall_failed")
            return ""
        if not items:
            return ""
        lines = [f"- {item.content[:300]}" for item in items]
        return "Earlier conversation:\n" + "\n".join(lines)

    async def _store_turn(self, sender: str, agent: Agent, message: str, response: str) -> None:
        try:
            await self._memory.store(
                MemoryItem(
                    agent_id=f"chat-{sender}",
                    content=f"[{sender}] {message}\n[{agent.name}] {response}",
                    tier=MemoryTier.NORMAL,
                    importance=0.6,
                    tags=["butler", "conversation", agent.domain, f"chat-{sender}"],
                )
            )
        except Exception as exc:  # noqa: BLE001 — optional transcript remains best effort.
            self._log.warning("butler.memory_store_failed", error_type=type(exc).__name__)


__all__ = [
    "ButlerAuditError",
    "ButlerExecutionError",
    "ButlerReply",
    "ButlerService",
    "LLMCallable",
]
