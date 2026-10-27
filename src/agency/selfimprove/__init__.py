"""Self-improvement loop: distill completed task experience into versioned skills.

This package adapts the Hermes-style "save reusable procedures from experience"
idea into Agency's governed, versioned SkillRegistry. Distilled skills always
start as DRAFT and advance only through the normal lifecycle (TESTING ->
APPROVED -> PUBLISHED) with evidence, so no autonomous creation bypasses the
Forge gates or human approval.
"""

from __future__ import annotations

from agency.selfimprove.distiller import TaskTrace, distill_skill
from agency.selfimprove.loop import SelfImprovementLoop

__all__ = ["SelfImprovementLoop", "TaskTrace", "distill_skill"]
