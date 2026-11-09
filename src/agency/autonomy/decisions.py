"""Deterministic decision states (SOURCE_CONSOLIDATED_BRIEF §14).

Every consequential action resolves to one Decision. The Agency
evaluates decisions in code, not prompts. LLM may reason, never
grant itself authority.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class Decision(str, Enum):
    ALLOW = "allow"
    BLOCK = "block"
    REQUIRE_APPROVAL = "require_approval"
    RETRY = "retry"
    ESCALATE = "escalate"
    QUARANTINE = "quarantine"

    def __str__(self) -> str:
        return self.value


class DecisionRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    action: str = Field(min_length=1)
    decision: Decision
    reason: str = Field(min_length=1, description="Deterministic rule that fired.")
    rule_id: str = Field(min_length=1)


def decide(
    action: str,
    *,
    has_capability: bool,
    has_tool: bool,
    budget_left: int,
    risk_trusted: bool,
) -> DecisionRecord:
    if not risk_trusted:
        return DecisionRecord(
            action=action, decision=Decision.QUARANTINE, reason="untrusted risk", rule_id="R-SAFE"
        )
    if not has_capability:
        return DecisionRecord(
            action=action,
            decision=Decision.ESCALATE,
            reason="capability missing; spawn required",
            rule_id="R-SPAWN",
        )
    if not has_tool:
        return DecisionRecord(
            action=action,
            decision=Decision.ESCALATE,
            reason="tool missing; forge build required",
            rule_id="R-TOOL",
        )
    if budget_left <= 0:
        return DecisionRecord(
            action=action, decision=Decision.BLOCK, reason="budget exhausted", rule_id="R-BUDGET"
        )
    return DecisionRecord(
        action=action, decision=Decision.ALLOW, reason="all preconditions met", rule_id="R-ALLOW"
    )
