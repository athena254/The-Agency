"""Agent registry — manages the identity life-cycle.

The registry is the source of truth for *who exists* in the agency. It owns
agent identity records and the transitions they may pass through:

        register -> get / list -> update_capabilities -> revoke

Registration intentionally does **not** grant permissions — permissions are
issued separately via :class:`~agency.kernel.policies.PolicyEngine`. Revocation
here is the identity-level kill-switch: a revoked agent is refused by the
policy engine; the caller should also revoke the agent's permissions.
"""

from __future__ import annotations

from datetime import datetime
from threading import RLock
from typing import Any

import structlog

from agency.kernel.identity import Agent, Capability


class AgentRegistry:
    """Thread-safe, in-memory registry of agent identities.

    Methods are synchronous (fast dict operations) and guarded by an RLock so
    the registry is callable from both sync and async contexts.
    """

    def __init__(self) -> None:
        self._agents: dict[str, Agent] = {}
        self._lock = RLock()
        self._log = structlog.get_logger(__name__)

    # ------------------------------------------------------------------ #
    # Registration
    # ------------------------------------------------------------------ #

    def register(self, agent: Agent | None = None, **identity_fields: Any) -> Agent:
        """Register an agent by model instance or constructor kwargs.

        Parameters
        ----------
        agent:
            An existing :class:`Agent`; if omitted, one is built from the
            remaining keyword arguments.

        Returns
        -------
        Agent
            The registered (immutable-by-convention) identity.

        Raises
        ------
        ValueError
            If an agent with the same ``id`` is already registered.
        """
        instance = agent or Agent(**identity_fields)
        with self._lock:
            if instance.id in self._agents:
                raise ValueError(f"agent {instance.id!r} is already registered")
            self._agents[instance.id] = instance
        self._log.info("registry.register", agent_id=instance.id, trust=instance.trust_level.value)
        return instance

    def get(self, agent_id: str) -> Agent | None:
        """Fetch an agent by id, or ``None`` if unknown."""
        with self._lock:
            return self._agents.get(agent_id)

    def get_required(self, agent_id: str) -> Agent:
        """Fetch an agent by id; raise :class:`KeyError` if unknown."""
        with self._lock:
            if agent_id not in self._agents:
                raise KeyError(f"unknown agent {agent_id!r}")
            return self._agents[agent_id]

    def list_agents(self, *, include_revoked: bool = False, domain: str | None = None) -> list[Agent]:
        """List registered agents, newest first.

        Parameters
        ----------
        include_revoked:
            Include revoked identities (default: hide them).
        domain:
            Restrict to a single domain, e.g. ``security``.
        """
        with self._lock:
            agents = list(self._agents.values())
        if not include_revoked:
            agents = [agent for agent in agents if not agent.revoked]
        if domain is not None:
            agents = [agent for agent in agents if agent.domain == domain]
        return sorted(agents, key=lambda agent: agent.created_at, reverse=True)

    def count(self, *, include_revoked: bool = False) -> int:
        """Return the number of registered agents."""
        return len(self.list_agents(include_revoked=include_revoked))

    # ------------------------------------------------------------------ #
    # Capability management
    # ------------------------------------------------------------------ #

    def update_capabilities(
        self,
        agent_id: str,
        capabilities: list[str | Capability],
        *,
        replace: bool = False,
    ) -> Agent:
        """Set or replace the agent's capability set.

        Parameters
        ----------
        agent_id:
            Target agent.
        capabilities:
            New capabilities (plain names or fully specified
            :class:`Capability` models).
        replace:
            ``True`` replaces the full set; ``False`` (default) unions with
            the existing claims without downgrading existing ceilings.

        Returns
        -------
        Agent
            The updated identity.
        """
        with self._lock:
            agent = self.get_required(agent_id)
            incoming: list[Capability] = []
            for entry in capabilities:
                if isinstance(entry, Capability):
                    incoming.append(entry)
                else:
                    incoming.append(Capability(name=entry))
            if replace:
                agent.capabilities = incoming
            else:
                existing = {cap.name: cap for cap in agent.capabilities}
                for cap in incoming:
                    if cap.name not in existing:
                        existing[cap.name] = cap
                agent.capabilities = list(existing.values())
        self._log.info(
            "registry.capabilities",
            agent_id=agent_id,
            count=len(agent.capabilities),
            mode="replace" if replace else "merge",
        )
        return agent

    def grant_capability(self, agent_id: str, capability: str | Capability) -> Agent:
        """Grant a single capability (no-op if already present)."""
        return self.update_capabilities(agent_id, [capability], replace=False)

    def revoke_capability(self, agent_id: str, name: str) -> Agent:
        """Remove a single capability by name (no-op if absent)."""
        with self._lock:
            agent = self.get_required(agent_id)
            agent.revoke_capability(name)
        self._log.info("registry.revoke_capability", agent_id=agent_id, capability=name)
        return agent

    # ------------------------------------------------------------------ #
    # Revocation
    # ------------------------------------------------------------------ #

    def revoke(self, agent_id: str) -> Agent:
        """Revoke an agent identity in place; the record is retained.

        A revoked agent can no longer satisfy :meth:`PolicyEngine.can_execute`.
        The record stays queryable for auditability; an agent cannot be
        re-registered without a new id.
        """
        with self._lock:
            agent = self.get_required(agent_id)
            agent.revoked = True
        self._log.warning("registry.revoke", agent_id=agent_id)
        return agent

    # ------------------------------------------------------------------ #
    # Introspection
    # ------------------------------------------------------------------ #

    def modified_since(self, since: datetime) -> list[Agent]:
        """Return agents whose ``created_at`` is at or after ``since``."""
        with self._lock:
            return [
                agent
                for agent in self._agents.values()
                if agent.created_at >= since and not agent.revoked
            ]

    def __len__(self) -> int:
        return self.count()

    def __contains__(self, agent_id: str) -> bool:
        with self._lock:
            return agent_id in self._agents

    def __repr__(self) -> str:
        return f"AgentRegistry(agents={self.count(include_revoked=True)})"