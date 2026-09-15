"""Gateway Agent - core logic with governance hooks and health monitoring."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)


class HealthStatus(Enum):
    """Health status of a domain agent or service."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


# A governance hook is a callable that inspects/modifies a message.
GovernanceHook = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass
class AgentHealth:
    """Tracks health state for a domain agent."""

    name: str
    status: HealthStatus = HealthStatus.UNKNOWN
    last_check: float = 0.0
    latency_ms: float = 0.0
    error_count: int = 0
    consecutive_failures: int = 0


@dataclass
class SessionState:
    """Tracks an active user session."""

    session_id: str
    user_id: str
    adapter: str  # "telegram", "cli", "vscode", "discord"
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    context: dict[str, Any] = field(default_factory=dict)
    active_agent: str | None = None


class GatewayAgent:
    """Core gateway agent that manages routing, governance, and health.

    The GatewayAgent is the brains behind the Butler architecture. It:
    - Maintains registry of domain agents and their health
    - Applies governance hooks to all inbound and outbound messages
    - Manages active user sessions
    - Provides health monitoring and circuit-breaking
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.governance_config = config.get("governance", {})
        self.agents: dict[str, AgentHealth] = {}
        self.sessions: dict[str, SessionState] = {}
        self._governance_hooks: list[GovernanceHook] = []
        self._health_task: asyncio.Task[None] | None = None
        self._running = False

        # Initialize agent registry from config
        for agent_cfg in config.get("domain_agents", []):
            name = agent_cfg["name"]
            self.agents[name] = AgentHealth(name=name)

    # ── Governance ────────────────────────────────────────────────

    def register_governance_hook(self, hook: GovernanceHook) -> None:
        """Register a hook to inspect/modify messages."""
        self._governance_hooks.append(hook)
        logger.debug("Registered governance hook: %s", hook.__name__)

    async def apply_governance(self, message: dict[str, Any]) -> dict[str, Any]:
        """Apply all registered governance hooks to a message.

        Raises:
            GovernanceError: If a message is rejected by governance.
        """
        current = message
        for hook in self._governance_hooks:
            try:
                current = await hook(current)
            except Exception as e:
                logger.error("Governance hook %s failed: %s", hook.__name__, e)
                raise GovernanceError(f"Hook {hook.__name__} rejected message") from e
        return current

    async def _default_governance(self, message: dict[str, Any]) -> dict[str, Any]:
        """Built-in governance: length limits and blocked patterns."""
        content = message.get("content", "")

        max_len = self.governance_config.get("max_input_length", 10000)
        if len(content) > max_len:
            raise GovernanceError(f"Message exceeds max length ({max_len})")

        blocked = self.governance_config.get("blocked_patterns", [])
        for pattern in blocked:
            if pattern.lower() in content.lower():
                raise GovernanceError(f"Blocked pattern detected: {pattern}")

        return message

    # ── Session Management ────────────────────────────────────────

    def create_session(
        self, session_id: str, user_id: str, adapter: str
    ) -> SessionState:
        """Create and track a new user session."""
        session = SessionState(
            session_id=session_id, user_id=user_id, adapter=adapter
        )
        self.sessions[session_id] = session
        logger.info("Created session %s for user %s via %s", session_id, user_id, adapter)
        return session

    def get_session(self, session_id: str) -> SessionState | None:
        """Retrieve an active session."""
        return self.sessions.get(session_id)

    def end_session(self, session_id: str) -> None:
        """End and remove a session."""
        if session_id in self.sessions:
            del self.sessions[session_id]
            logger.info("Ended session %s", session_id)

    # ── Health Monitoring ─────────────────────────────────────────

    async def start_health_monitoring(self) -> None:
        """Start background health checks for all domain agents."""
        self._running = True
        self._health_task = asyncio.create_task(self._health_loop())
        logger.info("Health monitoring started")

    async def stop_health_monitoring(self) -> None:
        """Stop background health checks."""
        self._running = False
        if self._health_task:
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass

    async def _health_loop(self) -> None:
        """Periodically check health of all domain agents."""
        interval = 60.0  # default
        agent_configs = self.config.get("domain_agents", [])
        intervals = {
            a["name"]: a.get("health_check_interval_seconds", 60) for a in agent_configs
        }

        while self._running:
            for name, health in self.agents.items():
                interval = intervals.get(name, 60.0)
                try:
                    await self._check_agent_health(name)
                except Exception as e:
                    logger.warning("Health check failed for %s: %s", name, e)
                    health.status = HealthStatus.UNHEALTHY
                    health.consecutive_failures += 1
            await asyncio.sleep(interval)

    async def _check_agent_health(self, name: str) -> None:
        """Perform a health check on a single domain agent."""
        agent_cfg = next(
            (a for a in self.config.get("domain_agents", []) if a["name"] == name),
            None,
        )
        if not agent_cfg:
            return

        health = self.agents[name]
        start = time.monotonic()

        try:
            # Perform HTTP health check (simplified)
            import urllib.request
            url = agent_cfg["endpoint"].rsplit("/", 1)[0] + "/health"
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: urllib.request.urlopen(url, timeout=5),
            )
            elapsed = (time.monotonic() - start) * 1000
            health.latency_ms = elapsed
            health.status = HealthStatus.HEALTHY
            health.consecutive_failures = 0
            health.last_check = time.time()
        except Exception as e:
            health.error_count += 1
            health.consecutive_failures += 1
            health.last_check = time.time()
            if health.consecutive_failures >= 3:
                health.status = HealthStatus.UNHEALTHY
            else:
                health.status = HealthStatus.DEGRADED
            raise

    def get_health_report(self) -> dict[str, Any]:
        """Get a health report for all agents."""
        return {
            name: {
                "status": h.status.value,
                "latency_ms": h.latency_ms,
                "error_count": h.error_count,
                "last_check": h.last_check,
            }
            for name, h in self.agents.items()
        }

    # ── Lifecycle ─────────────────────────────────────────────────

    async def shutdown(self) -> None:
        """Gracefully shut down the gateway agent."""
        await self.stop_health_monitoring()
        self.sessions.clear()
        logger.info("Gateway agent shut down")


class GovernanceError(Exception):
    """Raised when a message is rejected by governance rules."""
    pass
