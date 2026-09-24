"""Tool registry: registration, JSON-schema validation, timeouts, audit."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable
from typing import Any

import structlog

from agency.tools.base import Tool, ToolContext, ToolError, ToolResult, ToolSpec

logger = structlog.get_logger(__name__)

_TYPE_NAMES = ("string", "integer", "number", "boolean", "array", "object")


def _type_matches(value: Any, expected: str) -> bool:
    """Check a JSON-schema primitive type; bool is not an integer."""
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    return True


def _validate_value(schema: dict[str, Any], value: Any, path: str) -> str | None:
    """Validate a single value against a (sub)schema; None if valid."""
    expected = schema.get("type")
    if expected is not None:
        if isinstance(expected, list):
            if not any(_type_matches(value, t) for t in expected):
                return f"{path}: expected type {expected}, got {type(value).__name__}"
        elif isinstance(expected, str):
            if expected not in _TYPE_NAMES:
                pass  # unknown type name — ignore
            elif not _type_matches(value, expected):
                return f"{path}: expected {expected}, got {type(value).__name__}"
    if isinstance(value, dict):
        properties = schema.get("properties") or {}
        required = schema.get("required") or []
        for key in required:
            if key not in value:
                return f"{path}: missing required property {key!r}"
        for key, prop_schema in properties.items():
            if key in value and isinstance(prop_schema, dict):
                err = _validate_value(prop_schema, value[key], f"{path}.{key}")
                if err is not None:
                    return err
        additional = schema.get("additionalProperties", True)
        if additional is False:
            allowed = set(properties.keys())
            for key in value:
                if key not in allowed:
                    return f"{path}: unknown property {key!r} (additionalProperties is false)"
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for i, item in enumerate(value):
            err = _validate_value(schema["items"], item, f"{path}[{i}]")
            if err is not None:
                return err
    return None


def _validate_args(spec: ToolSpec, args: dict[str, Any]) -> str | None:
    """Validate args against ``spec.parameters`` JSON Schema.

    Returns None when valid, otherwise a human-readable violation string.
    Supports: ``type``, ``required``, ``additionalProperties``,
    nested ``properties``/``items``.
    """
    schema = spec.parameters or {}
    if not schema:
        if not isinstance(args, dict):
            return f"args must be an object, got {type(args).__name__}"
        return None
    if not isinstance(args, dict):
        return f"args must be an object, got {type(args).__name__}"
    return _validate_value(schema, args, "args")


class ToolRegistry:
    """Named-tool store with validation, timeouts, and audit logging.

    Parameters
    ----------
    audit:
        Optional AuditLog-like object with an async ``append`` method.
        Kwargs-style ``append(agent, action, result, target, evidence)``
        is tried first; ``AuditEntry``-style ``append(entry)`` is used as
        a fallback. When None (or on audit failure), structlog only.
    default_timeout_s:
        Default per-tool execution timeout in seconds.
    """

    def __init__(self, audit: Any | None = None, default_timeout_s: float = 30.0) -> None:
        self._tools: dict[str, Tool] = {}
        self._timeouts: dict[str, float] = {}
        self._audit = audit
        self._default_timeout_s = default_timeout_s
        self._log = structlog.get_logger(__name__)

    def register(self, tool: Tool) -> None:
        """Register a tool; raises ToolError on duplicate name."""
        name = tool.spec.name
        if name in self._tools:
            raise ToolError(f"duplicate tool registration: {name!r}", tool=name)
        self._tools[name] = tool
        self._log.info("tool.registered", tool=name)

    def get(self, name: str) -> Tool:
        """Return the tool named ``name``; raises ToolError if unknown."""
        try:
            return self._tools[name]
        except KeyError:
            raise ToolError(f"unknown tool: {name}", tool=name) from None

    def list_specs(self) -> list[ToolSpec]:
        """Return all tool specs sorted by name."""
        return [self._tools[name].spec for name in sorted(self._tools)]

    def set_timeout(self, name: str, seconds: float) -> None:
        """Override the execution timeout for one tool.

        Raises ToolError if the tool is unknown, ValueError if
        ``seconds`` is not positive.
        """
        if name not in self._tools:
            raise ToolError(f"unknown tool: {name}", tool=name)
        if seconds <= 0:
            raise ValueError("timeout seconds must be > 0.")
        self._timeouts[name] = seconds

    async def call(self, name: str, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """Call a tool by name with validated args; never raises to caller.

        Unknown tools, schema violations, timeouts, and tool exceptions
        all become ``ToolResult(ok=False, ...)``.
        """
        tool = self._tools.get(name)
        if tool is None:
            result = ToolResult(tool=name, ok=False, error=f"unknown tool: {name}")
            await self._audit_call(ctx, name, result)
            return result

        violation = _validate_args(tool.spec, args)
        if violation is not None:
            result = ToolResult(tool=name, ok=False, error=f"invalid args: {violation}")
            await self._audit_call(ctx, name, result)
            return result

        timeout = self._timeouts.get(name, self._default_timeout_s)
        start = time.perf_counter()
        try:
            raw = await asyncio.wait_for(tool.run(args, ctx), timeout=timeout)
            duration_ms = int((time.perf_counter() - start) * 1000)
            if isinstance(raw, ToolResult):
                raw.duration_ms = duration_ms
                if not raw.tool:
                    raw.tool = name
                result = raw
            else:
                result = ToolResult(tool=name, ok=True, output=raw, duration_ms=duration_ms)
        except TimeoutError:
            duration_ms = int((time.perf_counter() - start) * 1000)
            result = ToolResult(
                tool=name,
                ok=False,
                error=f"TimeoutError: tool {name!r} timed out after {timeout}s",
                duration_ms=duration_ms,
            )
        except Exception as exc:  # noqa: BLE001 — surfaced as ToolResult
            duration_ms = int((time.perf_counter() - start) * 1000)
            result = ToolResult(
                tool=name,
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
                duration_ms=duration_ms,
            )
        await self._audit_call(ctx, name, result)
        return result

    async def _audit_call(self, ctx: ToolContext, name: str, result: ToolResult) -> None:
        """Best-effort audit logging for one tool call."""
        action = "tool.call" if result.ok else "tool.error"
        outcome = "ok" if result.ok else "error"
        evidence: dict[str, Any] = {
            "agent_id": ctx.agent_id,
            "task_id": ctx.task_id,
            "tool": name,
            "ok": result.ok,
            "duration_ms": result.duration_ms,
        }
        if result.error:
            evidence["error"] = result.error
        self._log.info(
            "tool.call",
            tool=name,
            ok=result.ok,
            agent_id=ctx.agent_id,
            task_id=ctx.task_id,
        )
        audit = self._audit if self._audit is not None else ctx.audit
        if audit is None:
            return
        try:
            await audit.append(
                agent=ctx.agent_id,
                action=action,
                result=outcome,
                target=name,
                evidence=evidence,
            )
        except TypeError:
            # Fallback for AuditEntry-style logs: append(entry).
            try:
                from agency.kernel.audit import AuditEntry

                entry = AuditEntry(
                    agent=ctx.agent_id,
                    task=ctx.task_id,
                    target=name,
                    action=action,
                    result=outcome,
                    evidence=evidence,
                )
                await audit.append(entry)
            except Exception:  # noqa: BLE001 — audit must never break calls
                self._log.warning("tool.audit_failed", tool=name)
        except Exception:  # noqa: BLE001 — audit must never break calls
            self._log.warning("tool.audit_failed", tool=name)


def build_default_registry(tools: Iterable[Tool] | None = None, **ctx_kwargs: Any) -> ToolRegistry:
    """Return a ToolRegistry for the Agency tool layer.

    Builtin tools are registered by ``agency.tools.builtin`` (wired in a
    later brief); this factory intentionally does NOT import builtin
    modules so it stays dependency-light. Pass an optional list of
    ``Tool`` instances to pre-register them. Extra ``ctx_kwargs`` are
    accepted for forward compatibility and currently ignored.
    """
    _ = ctx_kwargs
    registry = ToolRegistry()
    for tool in tools or []:
        registry.register(tool)
    return registry


__all__ = ["ToolRegistry", "build_default_registry"]
