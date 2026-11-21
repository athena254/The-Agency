"""Tests for the Jev System-1 decision-model routing layer.

Covers the deterministic decision table (signal threshold, uncertain
corroboration, general fallback, defer), the explicit ``@domain`` prefix,
privacy of the state sent to the provider, fail-closed behavior on client
errors, audit evidence metadata, and default-config behavior parity.
"""

from __future__ import annotations

import json
import sys
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
import pytest_asyncio
from pydantic import ValidationError

from agency.butler.config import ButlerConfig
from agency.butler.decision import (
    STATE_MESSAGE_LIMIT,
    FakeJevClient,
    JevDecisionModel,
    TypeSafeJevClient,
)
from agency.butler.router import MessageRouter
from agency.butler.service import ButlerService
from agency.kernel.identity import Agent
from agency.orchestrator import AgencyOrchestrator

# ------------------------------------------------------------------ #
# Fixtures / helpers
# ------------------------------------------------------------------ #

SECURITY_SIGNAL: dict[str, float] = {
    "routes_to_security": 0.9,
    "routes_to_research": 0.1,
    "routes_to_general": 0.05,
}
SECURITY_UNCERTAIN: dict[str, float] = {
    "routes_to_security": 0.55,
    "routes_to_research": 0.2,
    "routes_to_general": 0.2,
}


def _unit_agents() -> list[Agent]:
    """Fresh agents for unit tests (ids regenerate per call)."""
    return [
        Agent(name="security-agent", domain="security"),
        Agent(name="research-agent", domain="research"),
        Agent(name="general-agent", domain="general"),
    ]


def _unit_model(probabilities: dict[str, float]) -> JevDecisionModel:
    return JevDecisionModel(FakeJevClient(probabilities), MessageRouter())


def _boom(state: dict[str, Any], questions: dict[str, dict[str, str]]) -> dict[str, float]:
    """A judgment callable that always fails (simulates a dead provider)."""
    raise RuntimeError("provider down")


def make_jev_service(
    probabilities: (
        dict[str, float] | Callable[[dict[str, Any], dict[str, dict[str, str]]], dict[str, float]]
    ),
    **config_overrides: Any,
) -> ButlerService:
    """Build an unstarted Butler with decision routing enabled (fake provider)."""
    config = ButlerConfig(decision_provider="jev", **config_overrides)
    svc = ButlerService(
        config=config, orchestrator=AgencyOrchestrator(memory_db_path=":memory:")
    )
    svc._decision_model = JevDecisionModel(
        FakeJevClient(probabilities),
        svc.router,
        signal_threshold=config.jev_signal_threshold,
        uncertain_low=config.jev_uncertain_low,
    )
    return svc


@pytest_asyncio.fixture
async def service() -> AsyncIterator[ButlerService]:
    svc = ButlerService(
        config=ButlerConfig(), orchestrator=AgencyOrchestrator(memory_db_path=":memory:")
    )
    await svc.start()
    try:
        yield svc
    finally:
        await svc.stop()


@pytest_asyncio.fixture
async def jev_service() -> AsyncIterator[ButlerService]:
    """Butler with decision routing enabled and a fixed fake judgment map."""
    svc = make_jev_service(SECURITY_SIGNAL)
    await svc.start()
    try:
        yield svc
    finally:
        await svc.stop()


# ------------------------------------------------------------------ #
# Decision table (unit)
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_signal_threshold_routes_to_judged_domain() -> None:
    agents = _unit_agents()
    decision = await _unit_model(SECURITY_SIGNAL).decide(
        "check the firewall for intrusions", {}, agents
    )
    assert decision is not None
    assert decision.source == "jev"
    assert decision.domain == "security"
    assert decision.confidence == pytest.approx(0.9)
    assert decision.agent_id == agents[0].id


@pytest.mark.asyncio
async def test_uncertain_judgment_corroborated_by_keywords() -> None:
    decision = await _unit_model(SECURITY_UNCERTAIN).decide(
        "scan for vulnerabilities", {}, _unit_agents()
    )
    assert decision is not None
    assert decision.source == "keyword_corroborated"
    assert decision.domain == "security"
    assert decision.confidence == pytest.approx(0.55)


@pytest.mark.asyncio
async def test_uncertain_judgment_without_corroboration_falls_back_to_general() -> None:
    agents = _unit_agents()
    decision = await _unit_model(SECURITY_UNCERTAIN).decide("tell me a joke please", {}, agents)
    assert decision is not None
    assert decision.source == "general_fallback"
    assert decision.domain == "general"
    assert decision.confidence is None
    assert decision.agent_id == agents[2].id


@pytest.mark.asyncio
async def test_uncertain_judgment_without_general_agent_defers() -> None:
    agents = [a for a in _unit_agents() if a.domain != "general"]
    decision = await _unit_model(SECURITY_UNCERTAIN).decide("tell me a joke please", {}, agents)
    assert decision is None


@pytest.mark.asyncio
async def test_below_threshold_defers_to_caller() -> None:
    decision = await _unit_model({"routes_to_security": 0.3, "routes_to_research": 0.2}).decide(
        "check the firewall for intrusions", {}, _unit_agents()
    )
    assert decision is None


@pytest.mark.asyncio
async def test_no_judgments_defers() -> None:
    decision = await _unit_model({}).decide("check the firewall", {}, _unit_agents())
    assert decision is None


@pytest.mark.asyncio
async def test_blank_message_defers() -> None:
    assert await _unit_model(SECURITY_SIGNAL).decide("   ", {}, _unit_agents()) is None


@pytest.mark.asyncio
async def test_no_agents_defers() -> None:
    assert await _unit_model(SECURITY_SIGNAL).decide("hello there", {}, []) is None


@pytest.mark.asyncio
async def test_judgment_for_missing_agent_is_down_ranked() -> None:
    """A judgment for a domain without an agent can never win."""
    decision = await _unit_model({"routes_to_ghost": 0.95, "routes_to_security": 0.9}).decide(
        "hello there", {}, _unit_agents()
    )
    assert decision is not None
    assert decision.source == "jev"
    assert decision.domain == "security"
    assert "routes_to_ghost" not in decision.judgments


@pytest.mark.asyncio
async def test_client_failure_defers() -> None:
    model = JevDecisionModel(FakeJevClient(_boom), MessageRouter())
    assert await model.decide("check the firewall", {}, _unit_agents()) is None


# ------------------------------------------------------------------ #
# Explicit @domain prefix
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_explicit_prefix_beats_judgments() -> None:
    decision = await _unit_model({"routes_to_research": 0.95}).decide(
        "@security check the firewall", {}, _unit_agents()
    )
    assert decision is not None
    assert decision.source == "explicit"
    assert decision.domain == "security"
    assert decision.confidence == 1.0
    assert decision.judgments == {}


@pytest.mark.asyncio
async def test_explicit_unknown_name_continues_to_judgments() -> None:
    decision = await _unit_model(SECURITY_SIGNAL).decide(
        "@nonexistent check the firewall", {}, _unit_agents()
    )
    assert decision is not None
    assert decision.source == "jev"
    assert decision.domain == "security"


# ------------------------------------------------------------------ #
# Judgment questions and provider-state privacy
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_judgment_questions_are_domain_scoped() -> None:
    fake = FakeJevClient(SECURITY_SIGNAL)
    model = JevDecisionModel(fake, MessageRouter())
    await model.decide("check the firewall", {}, _unit_agents())
    assert fake.calls
    _state, questions = fake.calls[0]
    assert set(questions) == {"routes_to_security", "routes_to_research", "routes_to_general"}
    spec = questions["routes_to_security"]
    assert spec["instructions"] == (
        "Does this message request work that belongs to the 'security' domain?"
    )
    assert spec["true_case"].startswith("It matches the domain's scope: security, vulnerability")
    assert spec["false_case"] == "None of the domain's signals are present."
    # A domain without keyword entries falls back to its own name.
    assert questions["routes_to_general"]["true_case"] == "It matches the domain's scope: general"


@pytest.mark.asyncio
async def test_provider_state_carries_only_the_message() -> None:
    fake = FakeJevClient(SECURITY_SIGNAL)
    model = JevDecisionModel(fake, MessageRouter())
    context = {"sender": "tester", "memory_context": "SECRET-MEMORY", "thread": "SECRET-THREAD"}
    message = "check the firewall for intrusions"
    await model.decide(message, context, _unit_agents())
    assert fake.calls, "the decision model must consult the judgment provider"
    state, _questions = fake.calls[0]
    # Only the truncated message crosses the provider boundary (spec §16.2, §21).
    assert state == {"message": message}


@pytest.mark.asyncio
async def test_provider_state_truncates_long_messages() -> None:
    fake = FakeJevClient(SECURITY_SIGNAL)
    model = JevDecisionModel(fake, MessageRouter())
    long_message = "x" * (STATE_MESSAGE_LIMIT + 500)
    await model.decide(long_message, {}, _unit_agents())
    assert fake.calls
    state, _questions = fake.calls[0]
    assert state == {"message": "x" * STATE_MESSAGE_LIMIT}


@pytest.mark.asyncio
async def test_typesafe_client_fails_closed_without_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without the optional SDK, the client raises a clear RuntimeError."""
    monkeypatch.setitem(sys.modules, "typesafe_sdk", None)
    client = TypeSafeJevClient(model="jev-latest")
    with pytest.raises(RuntimeError, match="typesafe_sdk is not installed"):
        await client.judge(
            {"message": "hello"}, {"routes_to_general": {"instructions": "Is this general?"}}
        )


# ------------------------------------------------------------------ #
# Configuration
# ------------------------------------------------------------------ #


def test_decision_config_defaults() -> None:
    config = ButlerConfig()
    assert config.decision_provider == "none"
    assert config.jev_model == "jev-latest"
    assert config.jev_signal_threshold == pytest.approx(0.7)
    assert config.jev_uncertain_low == pytest.approx(0.4)
    assert config.decision_enabled is False


def test_decision_provider_is_validated() -> None:
    with pytest.raises(ValidationError):
        ButlerConfig(decision_provider="openai")
    enabled = ButlerConfig(decision_provider="jev")
    assert enabled.decision_enabled is True
    assert enabled.decision_provider == "jev"


# ------------------------------------------------------------------ #
# Service integration
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_default_config_preserves_keyword_routing(service: ButlerService) -> None:
    """With decision_provider='none' (default) routing is unchanged."""
    assert service.config.decision_provider == "none"
    assert service._decision_model is None  # never built
    agent = await service.route("check the firewall for intrusions", {"sender": "t"})
    assert agent.domain == "security"
    agent = await service.route("research the latest AI news", {"sender": "t"})
    assert agent.domain == "research"


@pytest.mark.asyncio
async def test_default_config_audit_evidence_unchanged(service: ButlerService) -> None:
    message = "scan for vulnerabilities"
    await service.handle_message(message, "tester", {})
    entries = await service._audit.query()
    route_entries = [e for e in entries if e.action == "butler.route"]
    assert route_entries
    assert route_entries[-1].evidence == {"message_length": len(message)}


@pytest.mark.asyncio
async def test_jev_signal_threshold_routes_to_security_agent(jev_service: ButlerService) -> None:
    agent = await jev_service.route("check the firewall for intrusions", {"sender": "t"})
    assert agent.domain == "security"


@pytest.mark.asyncio
async def test_explicit_prefix_wins_in_service() -> None:
    svc = make_jev_service({"routes_to_research": 0.95})
    await svc.start()
    try:
        agent = await svc.route("@security check the firewall for intrusions", {"sender": "t"})
        assert agent.domain == "security"
    finally:
        await svc.stop()


@pytest.mark.asyncio
async def test_uncertain_judgment_corroborated_in_service() -> None:
    svc = make_jev_service(SECURITY_UNCERTAIN)
    await svc.start()
    try:
        agent = await svc.route("scan for vulnerabilities", {"sender": "t"})
        assert agent.domain == "security"
    finally:
        await svc.stop()


@pytest.mark.asyncio
async def test_uncertain_judgment_falls_back_to_general_in_service() -> None:
    svc = make_jev_service(SECURITY_UNCERTAIN)
    await svc.start()
    try:
        agent = await svc.route("tell me a joke please", {"sender": "t"})
        assert agent.domain == "general"
    finally:
        await svc.stop()


@pytest.mark.asyncio
async def test_below_threshold_defers_to_keyword_router() -> None:
    svc = make_jev_service({"routes_to_research": 0.2})
    await svc.start()
    try:
        # The 0.2 judgment defers; the keyword router (not the judgment) wins.
        agent = await svc.route("scan the network for threats", {"sender": "t"})
        assert agent.domain == "security"
    finally:
        await svc.stop()


@pytest.mark.asyncio
async def test_client_failure_falls_back_and_turn_survives() -> None:
    svc = make_jev_service(_boom)
    await svc.start()
    try:
        response = await svc.handle_message("scan the network for threats", "tester", {})
        assert isinstance(response, str) and response
        entries = await svc._audit.query()
        route_entries = [e for e in entries if e.action == "butler.route"]
        assert route_entries
        assert "decision_source" not in (route_entries[-1].evidence or {})
    finally:
        await svc.stop()


@pytest.mark.asyncio
async def test_route_audit_evidence_carries_decision_metadata(jev_service: ButlerService) -> None:
    message = "scan for vulnerabilities and exploits"
    response = await jev_service.handle_message(message, "tester", {})
    assert response
    entries = await jev_service._audit.query()
    route_entries = [e for e in entries if e.action == "butler.route"]
    assert route_entries
    evidence = route_entries[-1].evidence or {}
    assert evidence["decision_source"] == "jev"
    assert evidence["confidence"] == pytest.approx(0.9)
    assert evidence["judgments"]["routes_to_security"] == pytest.approx(0.9)
    assert set(evidence) <= {"message_length", "decision_source", "confidence", "judgments"}
    # Audit records carry metadata only — never message content (spec §21).
    assert message not in json.dumps(evidence)


@pytest.mark.asyncio
async def test_service_wires_config_model_and_thresholds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The service builds its decision model from config (model + thresholds)."""
    built: dict[str, Any] = {}

    def fake_client_factory(model: str) -> FakeJevClient:
        built["model"] = model
        client = FakeJevClient({"routes_to_security": 0.8})
        built["client"] = client
        return client

    monkeypatch.setattr("agency.butler.service.TypeSafeJevClient", fake_client_factory)
    svc = ButlerService(
        config=ButlerConfig(
            decision_provider="jev", jev_model="jev-test", jev_signal_threshold=0.95
        ),
        orchestrator=AgencyOrchestrator(memory_db_path=":memory:"),
    )
    await svc.start()
    try:
        # 0.8 < 0.95 → uncertain zone; keywords agree → keyword_corroborated
        # (with the default 0.7 threshold the same judgment would be "jev").
        response = await svc.handle_message("scan for vulnerabilities", "tester", {})
        assert response
        assert built["model"] == "jev-test"
        entries = await svc._audit.query()
        route_entries = [e for e in entries if e.action == "butler.route"]
        assert route_entries
        evidence = route_entries[-1].evidence or {}
        assert evidence["decision_source"] == "keyword_corroborated"
    finally:
        await svc.stop()
