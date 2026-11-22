"""Jev System-1 decision model for Butler message routing.

Phase B of the Butler intelligence upgrade: a "System 1" routing layer that
separates *judgment* from *decision* (doctrine: "Jev makes judgments, code
makes decisions").

- One narrow Noul (true/false) judgment per registered agent domain asks the
  Jev decision model whether the message requests work that belongs to that
  domain. Judgments are independent and evaluated in a single
  ``system_one`` call.
- A deterministic decision table in :class:`JevDecisionModel` turns the
  returned probabilities into a :class:`RoutingDecision` — route, corroborate
  with the keyword router, fall back to ``general``, or defer (``None``) to
  the legacy routing chain. Every decision records the judgments that fired
  (name → probability) so audit records carry metadata only, never message
  content (spec §21).

Privacy: the state sent to the provider contains only the truncated message
(``{"message": message[:2000]}``) — never context, memory or thread contents
(spec §16.2, §21).

Typesafe SDK
------------
:class:`TypeSafeJevClient` lazy-imports the ``typesafe_sdk`` package at call
time (same pattern as the Ollama routing client), so the dependency stays
optional and core stays light. The real distribution name is unverified, so
no pyproject extra is declared — install the SDK manually to enable
``BUTLER_DECISION_PROVIDER=jev``::

    pip install typesafe-sdk

Call shape verified against docs.typesafe.ai: ``AsyncTypeSafeClient(model=...)``
plus ``await client.system_one(state, questions)`` with ``Noul(instructions=...)``
questions and ``result.nouls[name].noul`` answers. ``Noul`` exposes only
``instructions``, so the judgment criteria (true/false cases) are folded into
the instruction text. Any client failure (missing package, missing API key,
API error, unrecognized answer shape) fails closed: :class:`JevDecisionModel`
logs a warning and returns ``None``, leaving the existing routing chain
untouched.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import structlog

from agency.butler.router import DOMAIN_KEYWORDS, MessageRouter
from agency.kernel.identity import Agent

log = structlog.get_logger(__name__)

#: Leading ``@name`` mention resolved against registered agent domains.
_EXPLICIT_MENTION_RE = re.compile(r"@(\w+)")

#: Only the first 2000 message characters are ever sent to the provider.
STATE_MESSAGE_LIMIT = 2000

#: Prefix of the judgment name built per agent domain (``routes_to_<domain>``).
JUDGMENT_NAME_PREFIX = "routes_to_"


@dataclass(frozen=True)
class RoutingDecision:
    """An immutable routing outcome produced by the decision model.

    Attributes:
        domain: Normalized (lowercase) domain the message was decided for.
        agent_id: Id of the resolving agent, when one is known.
        source: Why this decision was made — ``explicit`` (a leading
            ``@domain`` mention matched a registered agent), ``jev`` (a
            judgment crossed the signal threshold),
            ``keyword_corroborated`` (an uncertain judgment the keyword
            scorer agrees with), ``keyword`` (keyword-driven, not produced
            by the current table) or ``general_fallback`` (uncertain and
            uncorroborated; routed to ``general``).
        confidence: The probability behind the decision — ``1.0`` for
            ``explicit``, the judgment probability for ``jev`` and
            ``keyword_corroborated``, ``None`` for ``general_fallback``.
        judgments: Judgment name → probability (audit metadata only; never
            message content).
    """

    domain: str
    agent_id: str | None
    source: str
    confidence: float | None
    judgments: dict[str, float] = field(default_factory=dict)


class JevClient(Protocol):
    """Judgment port hiding the provider SDK shape.

    ``state`` always carries the (truncated) user message under ``message``
    — it is the only field ever sent to a provider. ``questions`` maps
    judgment name → spec with ``instructions``, ``true_case`` and
    ``false_case`` text. Implementations return judgment name → probability
    in ``[0.0, 1.0]`` for every judgment they could evaluate.
    """

    async def judge(
        self, state: dict[str, Any], questions: dict[str, dict[str, str]]
    ) -> dict[str, float]:
        """Return a probability per evaluated judgment."""
        raise NotImplementedError


class TypeSafeJevClient:
    """JevClient adapter over the Typesafe "System One" Python SDK.

    The SDK is imported lazily at first use. Install it manually (it is not
    a declared extra — the distribution name is unverified)::

        pip install typesafe-sdk

    Questions are mapped to ``Noul`` judgments and answers are normalized to
    plain floats, so callers never touch SDK types. A missing package, a
    missing API key or an API error raises; the decision model catches it
    and fails closed.
    """

    def __init__(self, model: str = "jev-latest") -> None:
        self._model = model

    async def judge(
        self, state: dict[str, Any], questions: dict[str, dict[str, str]]
    ) -> dict[str, float]:
        """Ask the Typesafe System One model one Noul judgment per question."""
        try:
            from typesafe_sdk import AsyncTypeSafeClient, Noul  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "typesafe_sdk is not installed; install the Typesafe SDK manually "
                "(pip install typesafe-sdk) to enable decision_provider='jev'."
            ) from exc
        noul_questions = {
            name: Noul(instructions=_noul_instructions(spec)) for name, spec in questions.items()
        }
        async with AsyncTypeSafeClient(model=self._model) as client:
            result = await client.system_one(str(state.get("message", "")), noul_questions)
        return _normalize_answers(result, questions)


class FakeJevClient:
    """Deterministic in-memory :class:`JevClient` for tests (no SDK, no network).

    ``probabilities`` is either a fixed ``{judgment name: probability}`` map
    or a callable ``(state, questions) -> probabilities``. Every call is
    recorded in :attr:`calls` so tests can assert exactly what was sent.
    """

    def __init__(
        self,
        probabilities: (
            dict[str, float]
            | Callable[[dict[str, Any], dict[str, dict[str, str]]], dict[str, float]]
        ),
    ) -> None:
        self._probabilities = probabilities
        self.calls: list[tuple[dict[str, Any], dict[str, dict[str, str]]]] = []

    async def judge(
        self, state: dict[str, Any], questions: dict[str, dict[str, str]]
    ) -> dict[str, float]:
        self.calls.append((state, questions))
        if callable(self._probabilities):
            return dict(self._probabilities(state, questions))
        return dict(self._probabilities)


class JevDecisionModel:
    """System-1 routing: narrow Jev judgments + a deterministic decision table.

    Decision table (pure code, thresholds configurable):

    1. A leading ``@domain`` mention matching a registered agent wins
       immediately (``source="explicit"``, confidence 1.0) — no judgment.
    2. Highest judgment probability ≥ ``signal_threshold`` (default 0.7)
       with a registered agent for that domain → ``source="jev"``.
    3. Highest probability in ``[uncertain_low, signal_threshold)`` (default
       ``[0.4, 0.7)``) → corroborate with keyword scoring: agreement →
       ``source="keyword_corroborated"``; otherwise route to ``general`` if
       a general agent exists (``source="general_fallback"``), else defer.
    4. Highest probability < ``uncertain_low`` or no judgments → defer
       (``None``); the caller falls back to its existing routing chain.

    Judgments whose domain has no registered agent are down-ranked (never
    routed to). Any client exception is logged and deferred — it never
    raises into the turn.
    """

    def __init__(
        self,
        client: JevClient,
        router: MessageRouter,
        *,
        signal_threshold: float = 0.7,
        uncertain_low: float = 0.4,
    ) -> None:
        self._client = client
        self._router = router
        self._signal_threshold = signal_threshold
        self._uncertain_low = uncertain_low

    async def decide(
        self, message: str, context: dict[str, Any], agents: list[Agent]
    ) -> RoutingDecision | None:
        """Decide a route from ``message``; ``None`` defers to the legacy chain.

        ``context`` is accepted for signature symmetry with the routing
        chain and is deliberately never sent to the provider (privacy: only
        the truncated message crosses the boundary).
        """
        _ = context  # privacy: never sent to the provider (spec §16.2, §21)
        text = message.strip()
        if not text:
            return None
        explicit = _explicit_decision(text, agents)
        if explicit is not None:
            return explicit
        domains = _agent_domains(agents)
        if not domains:
            return None
        question_names = {domain: f"{JUDGMENT_NAME_PREFIX}{domain}" for domain in domains}
        questions = {name: _question_for(domain) for domain, name in question_names.items()}
        state = {"message": message[:STATE_MESSAGE_LIMIT]}
        try:
            probabilities = await self._client.judge(state, questions)
        except Exception as exc:  # noqa: BLE001 — judgment failures defer, never fail the turn.
            log.warning("butler.jev_judge_failed", error_type=type(exc).__name__)
            return None
        judgments = {
            name: probabilities[name] for name in question_names.values() if name in probabilities
        }
        # Down-rank: only judgments backed by a registered agent can win.
        judged = [
            (domain, judgments[name])
            for domain, name in question_names.items()
            if name in judgments
        ]
        if not judged:
            return None
        best_domain, best_probability = max(judged, key=lambda item: item[1])
        if best_probability >= self._signal_threshold:
            agent = _agent_for(agents, best_domain)
            if agent is None:
                return None
            return RoutingDecision(
                domain=best_domain,
                agent_id=agent.id,
                source="jev",
                confidence=best_probability,
                judgments=judgments,
            )
        if best_probability >= self._uncertain_low:
            if self._router.detect_domain(message) == best_domain:
                agent = _agent_for(agents, best_domain)
                if agent is None:
                    return None
                return RoutingDecision(
                    domain=best_domain,
                    agent_id=agent.id,
                    source="keyword_corroborated",
                    confidence=best_probability,
                    judgments=judgments,
                )
            general = _agent_for(agents, "general")
            if general is not None:
                return RoutingDecision(
                    domain="general",
                    agent_id=general.id,
                    source="general_fallback",
                    confidence=None,
                    judgments=judgments,
                )
        return None


def _noul_instructions(spec: dict[str, str]) -> str:
    """Fold instructions and true/false criteria into one Noul instruction.

    The documented ``Noul`` question exposes only ``instructions`` (verified
    against docs.typesafe.ai), so the criteria text rides along in it.
    """
    parts = [spec.get("instructions", "")]
    true_case = spec.get("true_case", "")
    false_case = spec.get("false_case", "")
    if true_case:
        parts.append(f"Yes case: {true_case}")
    if false_case:
        parts.append(f"No case: {false_case}")
    return " ".join(part for part in parts if part)


def _normalize_answers(result: Any, questions: dict[str, dict[str, str]]) -> dict[str, float]:
    """Normalize a ``system_one`` result to judgment name → probability.

    Handles the documented shape (``result.nouls[name].noul``) plus tolerant
    fallbacks (mapping or attribute access; float / ``{"noul": x}`` / answer
    object). Judgments with an unrecognized answer shape are skipped — a
    fully unrecognized result yields no judgments and the decision model
    defers to the legacy chain.
    """
    answers: Any = result
    nouls = getattr(result, "nouls", None)
    if isinstance(nouls, dict):
        answers = nouls
    probabilities: dict[str, float] = {}
    for name in questions:
        probability = _answer_probability(_answer_for(answers, name))
        if probability is not None:
            probabilities[name] = probability
    return probabilities


def _answer_for(answers: Any, name: str) -> Any:
    if isinstance(answers, dict):
        return answers.get(name)
    return getattr(answers, name, None)


def _answer_probability(answer: Any) -> float | None:
    """Extract a ``noul`` probability from a float, mapping or answer object."""
    if isinstance(answer, bool):
        return None
    if isinstance(answer, (int, float)):
        return float(answer)
    if isinstance(answer, dict):
        value = answer.get("noul", answer.get("probability"))
    else:
        value = getattr(answer, "noul", None)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _agent_domains(agents: list[Agent]) -> list[str]:
    """Unique normalized domains of ``agents``, in registration order."""
    domains: list[str] = []
    for agent in agents:
        domain = agent.domain.strip().lower()
        if domain and domain not in domains:
            domains.append(domain)
    return domains


def _agent_for(agents: list[Agent], domain: str) -> Agent | None:
    """First agent whose normalized domain equals ``domain``, or None."""
    needle = domain.strip().lower()
    return next((a for a in agents if a.domain.strip().lower() == needle), None)


def _explicit_decision(message: str, agents: list[Agent]) -> RoutingDecision | None:
    """Resolve a leading ``@domain`` mention (deterministic, no judgment).

    An unknown ``@name`` is not explicit — the caller continues to the
    judgment phase.
    """
    match = _EXPLICIT_MENTION_RE.match(message)
    if match is None:
        return None
    agent = _agent_for(agents, match.group(1))
    if agent is None:
        return None
    return RoutingDecision(
        domain=agent.domain.strip().lower(),
        agent_id=agent.id,
        source="explicit",
        confidence=1.0,
        judgments={},
    )


def _question_for(domain: str) -> dict[str, str]:
    """Build one Noul judgment spec for ``domain`` (criteria = domain keywords)."""
    keywords = DOMAIN_KEYWORDS.get(domain, ())
    scope = ", ".join(keywords) if keywords else domain
    return {
        "instructions": f"Does this message request work that belongs to the '{domain}' domain?",
        "true_case": f"It matches the domain's scope: {scope}",
        "false_case": "None of the domain's signals are present.",
    }


__all__ = [
    "JUDGMENT_NAME_PREFIX",
    "STATE_MESSAGE_LIMIT",
    "FakeJevClient",
    "JevClient",
    "JevDecisionModel",
    "RoutingDecision",
    "TypeSafeJevClient",
]
