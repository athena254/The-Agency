"""Sandbox tool: execute code inside a managed sandbox.

The tool creates a fresh sandbox per call (or reuses ``ctx.metadata``
``"sandbox_id"`` when it still exists), runs the code with the interpreter
matching ``language``, and destroys sandboxes it created.
"""

from __future__ import annotations

import sys
import time
from typing import Any

import structlog

from agency.security.sandbox.manager import SandboxError, SandboxStatus
from agency.tools.base import ToolContext, ToolResult, ToolRisk, ToolSpec

logger = structlog.get_logger(__name__)

_MAX_OUTPUT_CHARS = 4000

_LANGUAGE_COMMANDS: dict[str, list[str]] = {
    "python": [sys.executable, "-c"],
    "bash": ["bash", "-c"],
    "sh": ["sh", "-c"],
    "node": ["node", "-e"],
    "javascript": ["node", "-e"],
    "js": ["node", "-e"],
}


class SandboxExecTool:
    """Execute code in a sandboxed backend and return the result."""

    def __init__(self) -> None:
        self.spec = ToolSpec(
            name="sandbox_exec",
            description=(
                "Execute code in a sandbox and return stdout, stderr, "
                "exit_code, and duration. 'language' defaults to 'python'."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "language": {"type": "string", "default": "python"},
                },
                "required": ["code"],
            },
            risk=ToolRisk.EXECUTES_CODE,
        )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        start = time.perf_counter()
        manager = ctx.sandbox_manager
        if manager is None:
            return ToolResult(tool="sandbox_exec", ok=False, error="no sandbox manager available")
        code = args.get("code")
        if not isinstance(code, str) or not code.strip():
            return ToolResult(tool="sandbox_exec", ok=False, error="missing required param: 'code'")
        language = args.get("language", "python")
        if not isinstance(language, str) or not language.strip():
            language = "python"
        language = language.strip().lower()
        prefix = _LANGUAGE_COMMANDS.get(language)
        if prefix is None:
            return ToolResult(
                tool="sandbox_exec",
                ok=False,
                error=f"unsupported language: {language!r}",
                duration_ms=int((time.perf_counter() - start) * 1000),
            )
        command = [*prefix, code]

        sandbox_id: str | None = None
        created_here = False
        try:
            candidate = (ctx.metadata or {}).get("sandbox_id")
            if isinstance(candidate, str):
                existing = manager.get_sandbox(candidate)
                if existing is not None and existing.status is SandboxStatus.RUNNING:
                    sandbox_id = candidate
            if sandbox_id is None:
                sandbox = manager.create_sandbox(ctx.agent_id)
                sandbox_id = sandbox.id
                created_here = True
            result = manager.execute(sandbox_id, command)
        except SandboxError as exc:
            return ToolResult(
                tool="sandbox_exec",
                ok=False,
                error=f"sandbox execution failed: {exc}",
                duration_ms=int((time.perf_counter() - start) * 1000),
            )
        except Exception as exc:  # noqa: BLE001 — backend failure is a tool error
            return ToolResult(
                tool="sandbox_exec",
                ok=False,
                error=f"sandbox execution failed: {type(exc).__name__}: {exc}",
                duration_ms=int((time.perf_counter() - start) * 1000),
            )
        finally:
            if sandbox_id is not None and created_here:
                try:
                    manager.destroy_sandbox(sandbox_id)
                except Exception as exc:  # noqa: BLE001 — cleanup must not fail call
                    logger.warning(
                        "tool.sandbox_exec.destroy_failed",
                        sandbox_id=sandbox_id,
                        error=str(exc),
                    )
        logger.info(
            "tool.sandbox_exec",
            language=language,
            exit_code=result.exit_code,
            duration_ms=round(result.duration_ms, 2),
        )
        return ToolResult(
            tool="sandbox_exec",
            ok=True,
            output={
                "stdout": (result.stdout or "")[:_MAX_OUTPUT_CHARS],
                "stderr": (result.stderr or "")[:_MAX_OUTPUT_CHARS],
                "exit_code": result.exit_code,
                "duration": result.duration_ms,
            },
            duration_ms=int((time.perf_counter() - start) * 1000),
            evidence={"exit_code": result.exit_code, "language": language},
        )
