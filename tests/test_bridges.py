"""Tests for bridges: base types, circuit breaker, coordinator, all 4 bridges (mocked)."""

from unittest.mock import AsyncMock, patch

import pytest

from agency.bridges.base import BridgeConfig, BridgeResult, BridgeStatus, BridgeUsage
from agency.bridges.claude.bridge import ClaudeCodeBridge, ClaudeConfig
from agency.bridges.codex.bridge import CodexBridge, CodexConfig
from agency.bridges.coordinator import (
    BridgeNotFoundError,
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitState,
    ExternalCoordinator,
)
from agency.bridges.hermes.bridge import HermesBridge, HermesConfig
from agency.bridges.openclaw.bridge import OpenClawBridge, OpenClawConfig


def test_bridge_usage_add():
    total = BridgeUsage(
        input_tokens=1, output_tokens=2, total_tokens=3, estimated_cost_usd=0.1
    ) + BridgeUsage(input_tokens=1)
    assert total.input_tokens == 2
    assert total.total_tokens == 3


def test_bridge_result_ok_and_failure():
    assert BridgeResult(output="hi").ok is True
    failed = BridgeResult.failure("boom")
    assert failed.ok is False
    assert failed.error == "boom"
    assert failed.status is BridgeStatus.FAILED
    assert BridgeResult.failure("x", status=BridgeStatus.TIMEOUT).status is BridgeStatus.TIMEOUT


def test_bridge_config_backoff():
    cfg = BridgeConfig(name="t")
    assert cfg.backoff_for(0) == pytest.approx(1.0)
    assert cfg.backoff_for(1) == pytest.approx(2.0)
    assert cfg.backoff_for(2) == pytest.approx(4.0)


async def test_circuit_breaker_closed_to_open_to_half_open():
    cfg = CircuitBreakerConfig(failure_threshold=2, recovery_timeout_s=0.05)
    breaker = CircuitBreaker(cfg)
    assert breaker.state is CircuitState.CLOSED
    assert await breaker.allow() is True
    await breaker.record_failure()
    assert breaker.state is CircuitState.CLOSED
    await breaker.record_failure()
    assert breaker.state is CircuitState.OPEN
    assert await breaker.allow() is False
    import asyncio

    await asyncio.sleep(0.06)
    assert await breaker.allow() is True  # half-open probe admitted
    await breaker.record_success()
    assert breaker.state is CircuitState.CLOSED


async def test_circuit_breaker_half_open_failure_reopens():
    cfg = CircuitBreakerConfig(failure_threshold=1, recovery_timeout_s=0.02)
    breaker = CircuitBreaker(cfg)
    await breaker.record_failure()
    assert breaker.state is CircuitState.OPEN
    import asyncio

    await asyncio.sleep(0.03)
    assert await breaker.allow() is True
    await breaker.record_failure()
    assert breaker.state is CircuitState.OPEN


async def test_coordinator_register_route_unregister(test_coordinator, fake_bridge):
    assert test_coordinator.list_bridges() == ["fake"]
    assert test_coordinator.get_bridge("fake") is fake_bridge
    result = await test_coordinator.route_task("do scan", "fake")
    assert result.ok and result.output == "done"
    assert fake_bridge.calls == ["do scan"]
    chunks = [c async for c in test_coordinator.route_stream("hi", "fake")]
    assert chunks == ["done"]
    assert await test_coordinator.unregister_bridge("fake") is True
    assert await test_coordinator.unregister_bridge("fake") is False
    with pytest.raises(BridgeNotFoundError):
        test_coordinator.get_bridge("fake")


async def test_coordinator_unknown_bridge_raises(test_coordinator):
    with pytest.raises(BridgeNotFoundError):
        await test_coordinator.route_task("t", "missing")
    with pytest.raises(BridgeNotFoundError):
        test_coordinator.breaker_state("missing")


async def test_coordinator_counts_bridge_failure(test_coordinator, fake_bridge):
    fake_bridge._output = "x"
    with patch.object(fake_bridge, "execute", return_value=BridgeResult.failure("task failed")):
        result = await test_coordinator.route_task("t", "fake")
        assert not result.ok
    assert test_coordinator.breaker_state("fake") is CircuitState.CLOSED  # 1 failure < threshold


async def test_coordinator_transport_exception_encoded(test_coordinator, fake_bridge):
    with patch.object(fake_bridge, "execute", side_effect=RuntimeError("down")):
        result = await test_coordinator.route_task("t", "fake")
        assert not result.ok
        assert "down" in result.error


async def test_coordinator_open_circuit_short_circuits(test_coordinator, fake_bridge):
    coord = ExternalCoordinator(CircuitBreakerConfig(failure_threshold=1, recovery_timeout_s=60.0))
    await coord.register_bridge(fake_bridge)
    with patch.object(fake_bridge, "execute", return_value=BridgeResult.failure("bad")):
        await coord.route_task("t", "fake")
    result = await coord.route_task("t2", "fake")
    assert result.status is BridgeStatus.UNAVAILABLE


async def test_coordinator_health_and_capabilities(test_coordinator):
    assert await test_coordinator.health_check_all() == {"fake": True}
    caps = await test_coordinator.capabilities_all()
    assert caps["fake"]["streaming"] is True


async def test_cancelled_bridge_stream_does_not_mark_breaker_healthy(fake_bridge, monkeypatch):
    import asyncio

    coord = ExternalCoordinator(CircuitBreakerConfig(failure_threshold=1))
    await coord.register_bridge(fake_bridge)

    async def cancelled(*_args, **_kwargs):
        raise asyncio.CancelledError()
        yield "unreachable"

    monkeypatch.setattr(fake_bridge, "stream", cancelled)
    with pytest.raises(asyncio.CancelledError):
        _ = [chunk async for chunk in coord.route_stream("task", "fake")]
    assert coord.breaker_state("fake") is CircuitState.OPEN


# --- The four real bridges (transports mocked) --- #


def test_claude_bridge_config_and_capabilities():
    bridge = ClaudeCodeBridge(ClaudeConfig())
    assert bridge.name == "claude"
    caps = bridge.capabilities()
    assert isinstance(caps, dict)
    assert bridge.config.model == "claude-sonnet-4-5"


def test_codex_bridge_config_and_capabilities():
    bridge = CodexBridge(CodexConfig())
    assert bridge.name == "codex"
    assert isinstance(bridge.capabilities(), dict)


def test_hermes_bridge_config_and_capabilities():
    bridge = HermesBridge(HermesConfig())
    assert bridge.name == "hermes"
    assert isinstance(bridge.capabilities(), dict)


def test_openclaw_bridge_config_and_capabilities():
    bridge = OpenClawBridge(OpenClawConfig(base_url="http://localhost:9999"))
    assert bridge.name == "openclaw"
    assert isinstance(bridge.capabilities(), dict)


async def test_bridges_execute_mocked():
    claude = ClaudeCodeBridge(ClaudeConfig(max_retries=0))
    with patch.object(claude, "_run_once", new=AsyncMock(return_value=("out", BridgeUsage(), {}))):
        result = await claude.execute("scan")
        assert result.ok and result.output == "out"

    codex = CodexBridge(CodexConfig(max_retries=0))
    with patch.object(codex, "execute", new=AsyncMock(return_value=BridgeResult(output="c"))):
        assert (await codex.execute("t")).output == "c"
        await codex.aclose()

    hermes = HermesBridge(HermesConfig())
    with patch.object(hermes, "execute", new=AsyncMock(return_value=BridgeResult(output="h"))):
        assert (await hermes.execute("t")).output == "h"

    openclaw = OpenClawBridge(OpenClawConfig(base_url="http://localhost:9999"))
    with patch.object(openclaw, "execute", new=AsyncMock(return_value=BridgeResult(output="o"))):
        assert (await openclaw.execute("t")).output == "o"
        await openclaw.aclose()


async def test_bridges_health_check_bool():
    assert isinstance(await ClaudeCodeBridge(ClaudeConfig()).health_check(), bool)
    codex = CodexBridge(CodexConfig())
    assert isinstance(await codex.health_check(), bool)
    await codex.aclose()
    assert isinstance(await HermesBridge(HermesConfig()).health_check(), bool)
    openclaw = OpenClawBridge(OpenClawConfig(base_url="http://localhost:9999"))
    assert isinstance(await openclaw.health_check(), bool)
    await openclaw.aclose()
