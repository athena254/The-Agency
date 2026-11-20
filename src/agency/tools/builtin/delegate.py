"""Agent-to-agent delegation tool: assign a child task to a registered agent.

``delegate_task`` is how one Agency agent hands work to another: the child
task joins the shared dispatcher queue (executing in parallel with
everything else), and the delegating agent's tool loop blocks — bounded by
``timeout`` — until the child reaches a terminal state, then receives its
result. Depth limits and cycle guards are enforced by
:meth:`agency.orchestrator.AgencyOrchestrator.delegate_task`.
"""

from __future__ import annotations

import time
from typing import Any

import structlog

from agency.tools.base import ToolContext, ToolResult, ToolRisk, ToolSpec

logger = structlog.get_logger(__name__)

_MAX_TITLE_CHARS = 200
_MAX_TASK_CHARS = 8000
_DEFAULT_TIMEOUT_S = 300.0
_MAX_TIMEOUT_S = 3600.0


class DelegateTaskTool:
    """Assign a child task to another agent and wait for its result."""

    def __init__(self, delegate: Any) -> None:
        # ``delegate`` is AgencyOrchestrator.delegate_task (parent_task_id,
        # target_agent_id, *, title, description, priority, timeout) -> Task.
        self._delegate = delegate
        self.spec = ToolSpec(
            name="delegate_task",
            description=(
                "Assign a task to another registered Agency agent and wait for its result. "
                "Provide 'target_agent_id' (a registered agent id), 'title', and 'task' "
                "(full task description with acceptance criteria)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target_agent_id": {"type": "string"},
                    "title": {"type": "string"},
                    "task": {"type": "string"},
                    "priority": {"type": "integer", "minimum": 0, "maximum": 10},
                    "timeout": {"type": "number", "exclusiveMinimum": 0},
                },
                "required": ["target_agent_id", "task"],
                "additionalProperties": False,
            },
            risk=ToolRisk.MUTATES_STATE,
        )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        started = time.perf_counter()
        target = args.get("target_agent_id")
        if not isinstance(target, str) or not target.strip():
            return ToolResult(
                tool="delegate_task",
                ok=False,
                error="target_agent_id is required",
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
        task_description = args.get("task")
        if not isinstance(task_description, str) or not task_description.strip():
            return ToolResult(
                tool="delegate_task",
                ok=False,
                error="task description is required",
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
        raw_title = args.get("title")
        title = (
            raw_title.strip()[:_MAX_TITLE_CHARS]
            if isinstance(raw_title, str) and raw_title.strip()
            else ""
        )
        priority = args.get("priority", 0)
        if type(priority) is not int or not 0 <= priority <= 10:
            priority = 0
        timeout = args.get("timeout", _DEFAULT_TIMEOUT_S)
        if type(timeout) not in (int, float) or isinstance(timeout, bool):
            timeout = _DEFAULT_TIMEOUT_S
        timeout = max(1.0, min(float(timeout), _MAX_TIMEOUT_S))

        try:
            child = await self._delegate(
                ctx.task_id,
                target.strip(),
                title=title,
                description=task_description.strip()[:_MAX_TASK_CHARS],
                priority=priority,
                timeout=timeout,
            )
        except TimeoutError:  # asyncio.TimeoutError aliases TimeoutError
            logger.info("delegate_task.timeout", parent=ctx.task_id, target=target.strip())
            return ToolResult(
                tool="delegate_task",
                ok=False,
                error=(
                    "delegated task did not finish in time; it is still queued or running — "
                    "check back with task_status rather than re-delegating"
                ),
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
        except ValueError as exc:
            return ToolResult(
                tool="delegate_task",
                ok=False,
                error=str(exc),
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
        output = {
            "child_task_id": child.task_id,
            "status": child.status.value,
            "result": child.output.get("summary", ""),
            "error": child.error,
        }
        return ToolResult(
            tool="delegate_task",
            ok=child.status.value == "completed",
            output=output,
            duration_ms=int((time.perf_counter() - started) * 1000),
            evidence={"child_task_id": child.task_id, "target_agent_id": target.strip()},
        )
