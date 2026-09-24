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
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from agency.butler.config import ButlerConfig
from agency.butler.router import MessageRouter
from agency.butler.threads import Thread, ThreadMessage, ThreadStore, Workspace
from agency.kernel.audit import AuditEntry, AuditLog
from agency.kernel.identity import Agent
from agency.memory.sms.models import MemoryItem, MemoryTier
from agency.memory.sms.retrieval import RetrievalEngine
from agency.memory.sms.store import MemoryStore
from agency.orchestrator import AgencyOrchestrator

if TYPE_CHECKING:
    from agency.lattice.api import Lattice

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
        self._audit = audit_log or AuditLog(":memory:")
        # Share the orchestrator's memory store — one store, one truth.
        # (A standalone MemoryStore here was a second, separate DB.)
        self._memory = memory_store or self._orchestrator._memory_store
        self._memory_retrieval = RetrievalEngine(self._memory)
        self._threads = thread_store
        self._owns_threads = thread_store is None
        self._lattice: Lattice | None = None  # Will be set from orchestrator
        self._llm = llm
        self._log = structlog.get_logger(__name__)
        self._started = False
        self._seeded = False

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
        """Initialise stores, orchestrator and default agents (idempotent)."""
        if self._started:
            return
        await self._audit.initialize()
        await self._memory.initialize()
        await self._orchestrator.start()
        # Pick up the Lattice from orchestrator after it starts
        if hasattr(self._orchestrator, "_lattice") and self._orchestrator._lattice is not None:
            self._lattice = self._orchestrator._lattice
        await self._ensure_default_agents()
        self._started = True
        self._log.info("butler.started")

    async def stop(self) -> None:
        """Shut down background services (memory lifecycle only; stores stay open)."""
        if not self._started:
            return
        await self._orchestrator.stop()
        if self._owns_threads and self._threads is not None:
            self._threads.close()
            self._threads = None
        self._started = False
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
    ) -> str:
        """Validate and execute a turn; anonymous channels disable memory."""
        await self._ensure_started()
        if not message or not message.strip():
            raise ValueError("message must not be empty.")
        text = message.strip()
        if len(text) > self._config.max_message_length:
            self._log.warning(
                "butler.message_truncated",
                sender=sender,
                length=len(text),
                max_length=self._config.max_message_length,
            )
            text = text[: self._config.max_message_length]

        merged: dict[str, Any] = {**context, "sender": sender}
        merged.pop("memory_context", None)  # Recalled memory is never caller-supplied.
        thread_id = merged.get("thread_id")
        if thread_id is not None:
            # Authorize before routing, recalling, executing, or writing anything.
            self.thread_store.get_thread(sender, thread_id)
        started = datetime.now(UTC)

        # Recall earlier conversation with this sender so the agent has
        # continuity (parity with OpenClaw/Hermes). Best-effort — never
        # blocks the turn.
        if thread_id is not None:
            history = self.thread_store.list_messages(sender, thread_id)[-10:]
            memory_context = (
                "Earlier conversation:\n"
                + "\n".join(f"- [{item.role}] {item.content[:300]}" for item in history)
                if history
                else ""
            )
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
            target=agent.domain,
            evidence={"sender": sender, "message": text[:500]},
        )

        try:
            response = await asyncio.wait_for(
                self.execute(agent, text, merged), timeout=self._config.timeout
            )
            result = "completed"
        except TimeoutError as exc:
            response = f"Request timed out after {self._config.timeout:g}s. Please try again."
            result = "timeout"
            self._log.warning("butler.execute_timeout", sender=sender, error=str(exc))
        except Exception:  # noqa: BLE001 — execution errors become a failed turn.
            response = "Sorry, I could not process that request."
            result = "failed"
            self._log.exception("butler.execute_failed", sender=sender)

        if thread_id is not None:
            self.thread_store.append_message(sender, thread_id, "user", text)
            self.thread_store.append_message(sender, thread_id, "assistant", response)
        elif memory_enabled:
            await self._store_turn(sender, agent, text, response)
        await self._audit_append(
            agent=agent.id,
            action="butler.handle_message",
            result=result,
            target=agent.domain,
            evidence={
                "sender": sender,
                "message": text[:500],
                "response": response[:500],
                "duration_s": (datetime.now(UTC) - started).total_seconds(),
            },
        )
        return response

    async def route(self, message: str, context: dict[str, Any]) -> Agent:
        """Select the handling agent, preferring the LLM when configured."""
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
            except Exception:  # noqa: BLE001 — fallback to deterministic routing.
                self._log.exception("butler.llm_route_failed")
        return await self._router.route(message, context)

    async def execute(self, agent: Agent, message: str, context: dict[str, Any]) -> str:
        """Run ``message`` for ``agent`` through the orchestrator pipeline."""
        task = await self._orchestrator.submit_task(
            title=f"Butler message from {context.get('sender', 'unknown')}",
            description=message,
            agent_id=agent.id,
        )
        result = await self._orchestrator.execute_task(task.task_id, context=context)
        output = result.output if isinstance(result.output, str) else str(result.output)
        await self._audit_append(
            agent=agent.id,
            action="butler.execute",
            result=result.status.value,
            target=task.title,
            evidence={"task_id": task.task_id, "output": output[:500]},
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
            await self._audit.append(
                AuditEntry(
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
            )
        except Exception:  # noqa: BLE001 — best-effort audit persistence.
            self._log.exception("butler.audit_failed", action=action)

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
            self._log.debug("butler.history_recall_failed", sender=sender)
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
        except Exception:  # noqa: BLE001 — optional memory persistence cannot block a turn.
            self._log.exception("butler.memory_store_failed")


__all__ = ["ButlerService", "LLMCallable"]
