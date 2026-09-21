"""Agency Risk System.

Transforms technical findings into multi-dimensional risk vectors
(:class:`~agency.risk.engine.models.RiskModel`), labels them with coarse
categories, and aggregates them per agent and per system while tracking the
historical accuracy of each reporting agent.
"""

from __future__ import annotations

from .engine import RiskCategory, RiskEngine, RiskModel
from .scoring import (
    AgentAccuracy,
    AgentRiskProfile,
    Outcome,
    RiskScorer,
    SystemRiskProfile,
)

__all__ = [
    "AgentAccuracy",
    "AgentRiskProfile",
    "Outcome",
    "RiskCategory",
    "RiskEngine",
    "RiskModel",
    "RiskScorer",
    "SystemRiskProfile",
]