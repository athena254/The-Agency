"""Tool-loop adapter for explicitly pinned, governed Agency skills."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agency.skills.executor import SkillRunResult
from agency.tools.base import ToolContext, ToolResult, ToolSpec


class SkillInvokeTool:
    """Expose exact skill invocation to the existing tool loop.

    The adapter accepts only the current agent identity from ToolContext;
    registry lookup, blueprint pinning, and permission authorization remain
    inside the orchestrator callback.
    """

    spec = ToolSpec(
        name="agency_skill",
        description=(
            "Run a published Agency skill only when its exact skill ID and version "
            "are pinned to you. Use only skills shown as available for this agent."
        ),
        parameters={
            "type": "object",
            "properties": {
                "skill_id": {"type": "string"},
                "version": {"type": "string"},
                "inputs": {"type": "object"},
            },
            "required": ["skill_id", "version", "inputs"],
            "additionalProperties": False,
        },
    )

    def __init__(self, invoke: Callable[[str, str, str, dict[str, Any]], SkillRunResult]) -> None:
        self._invoke = invoke

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        skill_id, version, inputs = args.get("skill_id"), args.get("version"), args.get("inputs")
        if (
            not isinstance(skill_id, str)
            or not isinstance(version, str)
            or not isinstance(inputs, dict)
        ):
            return ToolResult(tool=self.spec.name, ok=False, error="invalid skill request")
        result = self._invoke(ctx.agent_id, skill_id, version, inputs)
        return ToolResult(
            tool=self.spec.name,
            ok=result.success,
            output=result.output,
            error=result.error,
            evidence={"skill_id": result.skill_id, "version": result.version},
        )


__all__ = ["SkillInvokeTool"]
