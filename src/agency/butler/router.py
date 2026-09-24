"""Keyword-based message routing for the Butler.

:class:`MessageRouter` maps free-text messages to registered :class:`Agent`
identities using per-domain keyword scoring. It is the deterministic fallback
used when no LLM is configured (and the first-pass filter when one is).
"""

from __future__ import annotations

import re
from threading import RLock

import structlog

from agency.kernel.identity import Agent

log = structlog.get_logger(__name__)

DOMAIN_KEYWORDS: dict[str, tuple[str, ...]] = {
    "security": (
        "security",
        "vulnerability",
        "vulnerabilities",
        "exploit",
        "attack",
        "threat",
        "malware",
        "phishing",
        "intrusion",
        "firewall",
        "sandbox",
        "pentest",
        "red team",
        "blue team",
        "purple",
        "harden",
        "cve",
        "scan",
    ),
    "memory": (
        "memory",
        "remember",
        "recall",
        "forget",
        "memorize",
        "reminisce",
        "conversation history",
        "what did i say",
        "what did we discuss",
    ),
    "evidence": (
        "evidence",
        "finding",
        "findings",
        "proof",
        "artifact",
        "artifacts",
        "report",
        "observation",
        "verify",
        "verification",
        "forensic",
    ),
    "risk": (
        "risk",
        "risks",
        "risky",
        "assess",
        "assessment",
        "impact",
        "likelihood",
        "exposure",
        "threat model",
        "mitigat",
        "severity",
    ),
    "research": (
        "research",
        "look up",
        "lookup",
        "find out",
        "latest on",
        "news on",
        "news about",
        "search for",
        "search the web",
        "what's new",
        "who is",
        "what is",
        "cite",
        "sources",
    ),
    "task": (
        "task",
        "todo",
        "plan",
        "execute",
        "schedule",
        "workflow",
        "pipeline",
    ),
    "bridge": (
        "bridge",
        "external",
        "telegram",
        "discord",
        "webhook",
        "integrat",
    ),
}

_WORD_RE = re.compile(r"[a-z0-9]+(?:[ _-][a-z0-9]+)*")


def _keywords_for(domain: str) -> tuple[str, ...]:
    return DOMAIN_KEYWORDS.get(domain.strip().lower(), ())


class MessageRouter:
    """Route messages to agents by domain keyword scoring.

    Usage::

        router = MessageRouter()
        router.register_agent(agent)
        agent = await router.route("assess the risk of this finding", {})
    """

    def __init__(self) -> None:
        self._agents: dict[str, Agent] = {}
        self._lock = RLock()
        self._log = structlog.get_logger(__name__)

    def register_agent(self, agent: Agent) -> Agent:
        """Register (or replace) an agent by its id. Returns the agent."""
        with self._lock:
            self._agents[agent.id] = agent
        self._log.info("router.agent_registered", agent_id=agent.id, domain=agent.domain)
        return agent

    def unregister_agent(self, agent_id: str) -> bool:
        """Remove an agent; returns True if it existed."""
        with self._lock:
            return self._agents.pop(agent_id, None) is not None

    def list_agents(self) -> list[Agent]:
        """Return all registered agents in registration order."""
        with self._lock:
            return list(self._agents.values())

    def detect_domain(self, message: str) -> str | None:
        """Return the best-scoring domain for ``message``, or None on tie/empty."""
        text = message.strip().lower()
        if not text:
            return None
        words = set(_WORD_RE.findall(text))
        phrases = text  # multi-word keywords matched by substring
        best_domain: str | None = None
        best_score = 0
        for domain, keywords in DOMAIN_KEYWORDS.items():
            score = 0
            for keyword in keywords:
                kw = keyword.lower()
                if " " in kw:
                    if kw in phrases:
                        score += 2  # phrase match is a strong signal
                elif kw in words or kw in phrases:
                    score += 1
            if score > best_score:
                best_score = score
                best_domain = domain
        return best_domain if best_score > 0 else None

    async def route(self, message: str, context: dict[str, object] | None = None) -> Agent:
        """Pick the best agent for ``message``.

        Selection order:
        1. Highest keyword score among agents whose domain matches
           :meth:`detect_domain` (or any keyword of their own domain).
        2. An agent whose domain is ``general``.
        3. The first registered agent.

        Raises :exc:`ValueError` if no agents are registered.
        """
        _ = context  # reserved for future scoped routing
        with self._lock:
            agents = list(self._agents.values())
        if not agents:
            raise ValueError("No agents registered — cannot route message.")

        domain = self.detect_domain(message)
        if domain is not None:
            for agent in agents:
                if agent.domain.strip().lower() == domain:
                    self._log.info("router.routed", domain=domain, agent_id=agent.id)
                    return agent
            # No agent owns the detected domain: score each agent's own
            # domain keywords against the message and take the best hit.
            scored = sorted(agents, key=lambda a: self._score(a.domain, message), reverse=True)
            if self._score(scored[0].domain, message) > 0:
                self._log.info("router.routed", domain=scored[0].domain, agent_id=scored[0].id)
                return scored[0]

        for agent in agents:
            if agent.domain.strip().lower() == "general":
                self._log.info("router.fallback_general", agent_id=agent.id)
                return agent
        self._log.info("router.fallback_first", agent_id=agents[0].id)
        return agents[0]

    @staticmethod
    def _score(domain: str, message: str) -> int:
        text = message.strip().lower()
        words = set(_WORD_RE.findall(text))
        score = 0
        for keyword in _keywords_for(domain):
            kw = keyword.lower()
            if " " in kw:
                if kw in text:
                    score += 2
            elif kw in words or kw in text:
                score += 1
        return score


__all__ = ["DOMAIN_KEYWORDS", "MessageRouter"]
