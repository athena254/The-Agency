"""External coordinator — registry, router and circuit breaker for bridges.

:class:`ExternalCoordinator` owns the set of active :class:`Bridge`
instances: it routes tasks to a named bridge, guards each bridge with a
per-bridge :class:`CircuitBreaker` (closed -> open -> half-open), and fans
out :meth:`health_check_all` concurrently.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from enum import Enum
from time import monotonic
from typing import Any

import structlog
from pydantic import BaseModel, ConfigDict, Field

from agency.bridges.base import Bridge, BridgeResult, BridgeStatus

logger = structlog.get_logger(__name__)


class BridgeNotFoundError(KeyError):
    """Raised when routing to a bridge name that is not registered."""


class CircuitState(str, Enum):
    """Circuit breaker state for one bridge."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __str__(self) -> str:
        return self.value


class CircuitBreakerConfig(BaseModel):
    """Tuning for :class:`CircuitBreaker`."""

    model_config = ConfigDict(frozen=True)

    failure_threshold: int = Field(default=5, ge=1, description="Failures to trip open.")
    recovery_timeout_s: float = Field(
        default=60.0, gt=0, description="Open dwell before half-open."
    )
    half_open_max_calls: int = Field(default=1, ge=1, description="Probe calls allowed half-open.")
    success_threshold: int = Field(
        default=1, ge=1, description="Successes to close from half-open."
    )


@dataclass
class _CircuitRuntime:
    state: CircuitState = CircuitState.CLOSED
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    opened_at: float = 0.0
    half_open_inflight: int = 0


class CircuitBreaker:
    """Per-bridge circuit breaker (closed -> open -> half-open).

    Failures increment a consecutive counter; reaching
    ``failure_threshold`` opens the circuit for ``recovery_timeout_s``.
    After the dwell, one probe at a time is admitted (half-open): success
    closes the circuit, failure re-opens it.
    """

    def __init__(self, config: CircuitBreakerConfig | None = None) -> None:
        self._config = config or CircuitBreakerConfig()
        self._runtime = _CircuitRuntime()
        self._lock = asyncio.Lock()
        self._log = structlog.get_logger(f"{__name__}.CircuitBreaker")

    @property
    def state(self) -> CircuitState:
        """Current state (refreshes open -> half-open on elapsed dwell)."""
        runtime = self._runtime
        if runtime.state is CircuitState.OPEN and self._dwell_elapsed():
            runtime.state = CircuitState.HALF_OPEN
            runtime.half_open_inflight = 0
            runtime.consecutive_successes = 0
        return runtime.state

    @property
    def config(self) -> CircuitBreakerConfig:
        """Active breaker tuning."""
        return self._config

    async def allow(self) -> bool:
        """Whether a call may proceed right now."""
        async with self._lock:
            runtime = self._runtime
            if runtime.state is CircuitState.CLOSED:
                return True
            if runtime.state is CircuitState.OPEN:
                if self._dwell_elapsed():
                    runtime.state = CircuitState.HALF_OPEN
                    runtime.half_open_inflight = 0
                    runtime.consecutive_successes = 0
                    self._log.info("breaker.half_open")
                else:
                    return False
            # HALF_OPEN: admit up to half_open_max_calls probes.
            if runtime.half_open_inflight >= self._config.half_open_max_calls:
                return False
            runtime.half_open_inflight += 1
            return True

    async def record_success(self) -> None:
        """Record a successful call."""
        async with self._lock:
            runtime = self._runtime
            runtime.consecutive_failures = 0
            if runtime.state is CircuitState.HALF_OPEN:
                runtime.half_open_inflight = max(0, runtime.half_open_inflight - 1)
                runtime.consecutive_successes += 1
                if runtime.consecutive_successes >= self._config.success_threshold:
                    runtime.state = CircuitState.CLOSED
                    runtime.consecutive_successes = 0
                    self._log.info("breaker.closed")

    async def record_failure(self) -> None:
        """Record a failed call; trips the breaker at threshold."""
        async with self._lock:
            runtime = self._runtime
            runtime.consecutive_failures += 1
            if runtime.state is CircuitState.HALF_OPEN:
                runtime.half_open_inflight = max(0, runtime.half_open_inflight - 1)
                self._trip_locked()
            elif runtime.consecutive_failures >= self._config.failure_threshold:
                self._trip_locked()

    def _trip_locked(self) -> None:
        runtime = self._runtime
        runtime.state = CircuitState.OPEN
        runtime.opened_at = monotonic()
        runtime.consecutive_successes = 0
        self._log.warning("breaker.open", consecutive_failures=runtime.consecutive_failures)

    def _dwell_elapsed(self) -> bool:
        return (monotonic() - self._runtime.opened_at) >= self._config.recovery_timeout_s


class ExternalCoordinator:
    """Manages all bridges: registration, routing, streaming and health.

    Each registered bridge gets its own :class:`CircuitBreaker`. Calls to
    open-circuit bridges short-circuit to a ``status=UNAVAILABLE`` result
    without touching the transport.
    """

    def __init__(self, breaker_config: CircuitBreakerConfig | None = None) -> None:
        self._bridges: dict[str, Bridge] = {}
        self._breakers: dict[str, CircuitBreaker] = {}
        self._breaker_config = breaker_config or CircuitBreakerConfig()
        self._lock = asyncio.Lock()
        self._log = structlog.get_logger(f"{__name__}.ExternalCoordinator")

    # ------------------------------------------------------------------ #
    # Registry
    # ------------------------------------------------------------------ #

    async def register_bridge(self, bridge: Bridge) -> None:
        """Register (or replace) the bridge held under ``bridge.name``."""
        async with self._lock:
            self._bridges[bridge.name] = bridge
            self._breakers.setdefault(bridge.name, CircuitBreaker(self._breaker_config))
        self._log.info("coordinator.registered", bridge=bridge.name)

    async def unregister_bridge(self, name: str) -> bool:
        """Remove a bridge; ``True`` when one was registered under ``name``."""
        async with self._lock:
            removed = self._bridges.pop(name, None) is not None
            self._breakers.pop(name, None)
        if removed:
            self._log.info("coordinator.unregistered", bridge=name)
        return removed

    def get_bridge(self, name: str) -> Bridge:
        """Return the bridge registered under ``name``.

        Raises
        ------
        BridgeNotFoundError
            If no bridge is registered under ``name``.
        """
        try:
            return self._bridges[name]
        except KeyError as exc:
            raise BridgeNotFoundError(f"unknown bridge {name!r}") from exc

    def list_bridges(self) -> list[str]:
        """Return registered bridge names in registration order."""
        return list(self._bridges.keys())

    def breaker_state(self, name: str) -> CircuitState:
        """Return the circuit state for ``name`` (raises if unknown)."""
        try:
            return self._breakers[name].state
        except KeyError as exc:
            raise BridgeNotFoundError(f"unknown bridge {name!r}") from exc

    # ------------------------------------------------------------------ #
    # Routing
    # ------------------------------------------------------------------ #

    async def route_task(
        self,
        task: str,
        bridge_name: str,
        context: dict[str, Any] | None = None,
    ) -> BridgeResult:
        """Execute ``task`` on the named bridge with circuit-breaker guard.

        Raises
        ------
        BridgeNotFoundError
            If ``bridge_name`` is not registered.
        """
        bridge = self.get_bridge(bridge_name)
        breaker = self._breakers[bridge_name]
        if not await breaker.allow():
            self._log.warning("coordinator.circuit_open", bridge=bridge_name)
            return BridgeResult.failure(
                f"bridge {bridge_name!r} circuit is open",
                status=BridgeStatus.UNAVAILABLE,
            )
        try:
            result = await bridge.execute(task, context)
        except Exception as exc:  # noqa: BLE001 — bridge transport failures become failed results.
            await breaker.record_failure()
            self._log.exception("coordinator.execute_error", bridge=bridge_name)
            return BridgeResult.failure(f"bridge {bridge_name!r} raised: {exc}")
        if result.ok:
            await breaker.record_success()
        else:
            await breaker.record_failure()
        self._log.info(
            "coordinator.routed",
            bridge=bridge_name,
            status=result.status.value,
            duration_s=round(result.duration_s, 3),
        )
        return result

    async def route_stream(
        self,
        task: str,
        bridge_name: str,
        context: dict[str, Any] | None = None,
    ) -> AsyncGenerator[str, None]:
        """Stream ``task`` output from the named bridge.

        Raises
        ------
        BridgeNotFoundError
            If ``bridge_name`` is not registered.
        RuntimeError
            If the bridge circuit is currently open.
        """
        bridge = self.get_bridge(bridge_name)
        breaker = self._breakers[bridge_name]
        if not await breaker.allow():
            raise RuntimeError(f"bridge {bridge_name!r} circuit is open")
        failed = False
        try:
            async for chunk in bridge.stream(task, context):
                yield chunk
        except asyncio.CancelledError:
            failed = True
            raise
        except Exception:  # Record failures from arbitrary bridge plugins.
            failed = True
            raise
        finally:
            if failed:
                await breaker.record_failure()
            else:
                await breaker.record_success()

    # ------------------------------------------------------------------ #
    # Health
    # ------------------------------------------------------------------ #

    async def health_check_all(self) -> dict[str, bool]:
        """Probe every registered bridge concurrently (10s guard each)."""
        names = self.list_bridges()
        results = await asyncio.gather(
            *(self._safe_health(name) for name in names), return_exceptions=False
        )
        report = dict(zip(names, results))
        self._log.info("coordinator.health", **{k: str(v) for k, v in report.items()})
        return report

    async def _safe_health(self, name: str) -> bool:
        try:
            return await asyncio.wait_for(self._bridges[name].health_check(), timeout=10.0)
        except Exception:  # noqa: BLE001 — one failing bridge must not fail aggregate health.
            self._log.warning("coordinator.health_failed", bridge=name)
            return False

    async def capabilities_all(self) -> dict[str, dict[str, Any]]:
        """Return ``capabilities()`` for every registered bridge."""
        return {name: bridge.capabilities() for name, bridge in self._bridges.items()}
