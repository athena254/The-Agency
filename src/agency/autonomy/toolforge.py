"""Forge tool-gap loop with verified self-publish.

Deterministic: tool exists -> ALLOW; missing -> Forge must build
and pass inspection + security scan + tests (ForgePipelineReport
gate=PASS); only PASS may self-publish to registry.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ToolGapDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    action: str
    tool_name: str
    reason: str
    rule_id: str


def decide_tool_gap(tool_name: str, available_tools: set[str]) -> ToolGapDecision:
    if tool_name in available_tools:
        return ToolGapDecision(
            action="USE", tool_name=tool_name, reason="tool present", rule_id="T-USE"
        )
    return ToolGapDecision(
        action="BUILD",
        tool_name=tool_name,
        reason="tool missing; forge build required",
        rule_id="T-BUILD",
    )


def can_self_publish(gate: str, artifact_sha256: str, manifest_sha256: str) -> bool:
    return gate == "PASS" and bool(artifact_sha256) and artifact_sha256 == manifest_sha256
