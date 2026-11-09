"""Deterministic auto-spawn rules.

Fully auto but guided by rules: spawn iff capability missing and
spawn bounds (max children/depth/lifetime) allow. No human vote
required; quorum = reputation-weighted peer check performed by
Lattice, this module decides *whether* to propose.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class SpawnDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    spawn: bool
    capabilities: list[str]
    reason: str
    rule_id: str


def decide_spawn(
    required: list[str],
    existing_capabilities: set[str],
    *,
    current_children: int = 0,
    max_children: int = 8,
    depth: int = 0,
    max_depth: int = 4,
) -> SpawnDecision:
    missing = sorted({c.lower() for c in required} - {c.lower() for c in existing_capabilities})
    if not missing:
        return SpawnDecision(
            spawn=False, capabilities=[], reason="capability covered", rule_id="S-COVERED"
        )
    if depth >= max_depth:
        return SpawnDecision(
            spawn=False, capabilities=missing, reason="depth limit", rule_id="S-DEPTH"
        )
    if current_children >= max_children:
        return SpawnDecision(
            spawn=False, capabilities=missing, reason="children limit", rule_id="S-LIMIT"
        )
    return SpawnDecision(
        spawn=True, capabilities=missing, reason=f"missing {missing}", rule_id="S-SPAWN"
    )
