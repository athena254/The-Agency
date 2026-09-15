"""Nexus Gateway System - Butler Architecture.

A message relay system connecting user interfaces (Telegram, Discord, CLI, VS Code)
to agent systems. Supports three operational modes:
  - Butler Only: Routes directly to domain agents (no Buddy UI agent)
  - Butler + Buddy Separate (RECOMMENDED): Routes to Buddy via Lattice + routes to domain agents
  - Butler + Buddy Merged: Single process combining both roles
"""

from .launcher import GatewayLauncher, launch_gateway
from .gateway_agent import GatewayAgent, HealthStatus, GovernanceHook
from .butler_only import ButlerOnlyGateway
from .butler_separate import ButlerSeparateGateway
from .butler_merged import ButlerMergedGateway

__all__ = [
    "GatewayLauncher",
    "launch_gateway",
    "GatewayAgent",
    "HealthStatus",
    "GovernanceHook",
    "ButlerOnlyGateway",
    "ButlerSeparateGateway",
    "ButlerMergedGateway",
]

__version__ = "1.0.0"
