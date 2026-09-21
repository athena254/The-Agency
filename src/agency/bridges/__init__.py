"""Agency Bridges — coordination layer for external AI systems.

Bridges adapt external AI CLIs / APIs (Claude Code, Codex, OpenClaw, Hermes)
behind a uniform async interface (:class:`~agency.bridges.base.Bridge`) so the
kernel can route tasks to them, stream partial output, and track health via
:class:`~agency.bridges.coordinator.ExternalCoordinator`.
"""

from __future__ import annotations

from agency.bridges.base import Bridge, BridgeConfig, BridgeResult, BridgeStatus
from agency.bridges.coordinator import (
    BridgeNotFoundError,
    CircuitBreaker,
    CircuitState,
    ExternalCoordinator,
)

__all__ = [
    "Bridge",
    "BridgeConfig",
    "BridgeNotFoundError",
    "BridgeResult",
    "BridgeStatus",
    "CircuitBreaker",
    "CircuitState",
    "ExternalCoordinator",
]
