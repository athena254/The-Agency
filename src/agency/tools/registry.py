"""Tool registry: registration, JSON-schema validation, timeouts, audit."""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Iterable
from typing import Any

import structlog

from agency.kernel.audit import AuditEntry, BetaAuditLog
from agency.tools.base import (
    BetaToolPolicy,
    Tool,
    ToolContext,
    ToolError,
    ToolPolicy,
    ToolPolicyDecision,
    ToolResult,
    ToolSpec,
)

logger = structlog.get_logger(__name__)

_TYPE_NAMES = ("string", "integer", "number", "boolean", "array", "object")

_BETA_AUDIT_ERROR = "beta audit unavailable"


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


def _coerce_policy_verdict(verdict: bool | ToolPolicyDecision) -> tuple[bool, str]:
    """Normalize a policy return into (allowed, reason)."""
    if isinstance(verdict, bool):
        return verdict, ""
    if isinstance(verdict, ToolPolicyDecision):
        if isinstance(verdict.allowed, bool):
            return verdict.allowed, verdict.reason
        return False, "invalid policy decision"
    return False, "invalid policy decision"


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
    beta_policy:
        Optional per-call preflight hook checked after schema validation
        and before ``tool.run``. ``None`` (default) keeps legacy-open
        behavior. A denial returns ``ToolResult(ok=False, ...)`` and is
        audited; policy errors fail closed.

    Beta audit gate
    ---------------
    Beta-only calls require an initialized ``BetaAuditLog``: a concrete
    disk-backed SQLite store with WAL and synchronous FULL commits. A
    verified pre-execution intent must commit before ``tool.run``; a
    verified outcome must commit before returning any successful result.
    Arbitrary append-shaped fakes and ordinary NORMAL-sync AuditLog instances
    cannot authorize execution. Storage failures return the fixed error
    without retrying a possibly committed append. Beta records omit args,
    query, output, free-form errors and raw context identifiers. Denials,
    unknown tools and schema violations never execute; their audit writes
    are best effort and cannot turn a denial into an authorization.
    A failed outcome append reports ``INDETERMINATE``: external effects cannot
    be rolled back. This seam does not implement task-level recovery or
    admission suspension. Without ``beta_policy`` audit stays best effort.
    """

    def __init__(
        self,
        audit: Any | None = None,
        default_timeout_s: float = 30.0,
        beta_policy: ToolPolicy | None = None,
    ) -> None:
        self._tools: dict[str, Tool] = {}
        self._timeouts: dict[str, float] = {}
        self._audit = audit
        self._default_timeout_s = default_timeout_s
        self._beta_policy = beta_policy
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

        With ``beta_policy`` set, the call additionally fails closed when a
        sanitized pre-execution intent or the final outcome cannot be
        persisted; see the class docstring.
        """
        tool = self._tools.get(name)
        if tool is None:
            safe_name = "unknown" if self._beta_policy is not None else name
            result = ToolResult(tool=safe_name, ok=False, error=f"unknown tool: {safe_name}")
            await self._audit_call(ctx, safe_name, result)
            return result

        violation = _validate_args(tool.spec, args)
        if violation is not None:
            detail = (
                "invalid args" if self._beta_policy is not None else f"invalid args: {violation}"
            )
            result = ToolResult(tool=name, ok=False, error=detail)
            await self._audit_call(ctx, name, result)
            return result

        if self._beta_policy is not None:
            try:
                verdict = self._beta_policy(tool.spec, args, ctx)
            except Exception as exc:  # noqa: BLE001 — policy errors fail closed
                result = ToolResult(
                    tool=name,
                    ok=False,
                    error=f"beta policy denied: policy error: {type(exc).__name__}",
                )
                await self._audit_call(ctx, name, result)
                return result
            allowed, reason = _coerce_policy_verdict(verdict)
            if not allowed:
                detail = reason if type(self._beta_policy) is BetaToolPolicy else "not allowed"
                result = ToolResult(tool=name, ok=False, error=f"beta policy denied: {detail}")
                await self._audit_call(ctx, name, result)
                return result
            persisted = await self._beta_persist(
                ctx, name, action="tool.call", outcome="pending", phase="intent", extra={}
            )
            if not persisted:
                self._log.warning("tool.beta_audit_unavailable", tool=name, phase="intent")
                return ToolResult(
                    tool=name,
                    ok=False,
                    error=_BETA_AUDIT_ERROR,
                    evidence={"status": "FAILED"},
                )

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
        if self._beta_policy is not None:
            if not result.ok:
                result = ToolResult(
                    tool=name, ok=False, error="beta tool failed", duration_ms=result.duration_ms
                )
            extra: dict[str, Any] = {"ok": result.ok, "duration_ms": result.duration_ms}
            persisted = await self._beta_persist(
                ctx,
                name,
                action="tool.call" if result.ok else "tool.error",
                outcome="ok" if result.ok else "error",
                phase="outcome",
                extra=extra,
            )
            if not persisted:
                # Outward effects may already have happened; they cannot be
                # rolled back, so the honest status is INDETERMINATE.
                self._log.warning("tool.beta_audit_unavailable", tool=name, phase="outcome")
                return ToolResult(
                    tool=name,
                    ok=False,
                    error=_BETA_AUDIT_ERROR,
                    duration_ms=result.duration_ms,
                    evidence={"status": "INDETERMINATE"},
                )
            self._log.info("tool.call", tool=name, ok=result.ok)
            return result
        await self._audit_call(ctx, name, result)
        return result

    async def _beta_persist(
        self,
        ctx: ToolContext,
        name: str,
        *,
        action: str,
        outcome: str,
        phase: str,
        extra: dict[str, Any],
    ) -> bool:
        """Persist through the concrete disk-backed beta store, exactly once.

        Never interpret a duck-typed append, a falsy response, or a TypeError
        as an acknowledgement. A failed append can have committed already;
        retrying here could create a second event.
        """
        audit = self._audit if self._audit is not None else ctx.audit
        if type(audit) is not BetaAuditLog:
            return False
        try:
            agent_id = hashlib.sha256(ctx.agent_id.encode("utf-8")).hexdigest()
            task_id = hashlib.sha256(ctx.task_id.encode("utf-8")).hexdigest()
            evidence: dict[str, Any] = {
                "agent_id": agent_id,
                "task_id": task_id,
                "tool": name,
                "phase": phase,
            }
            evidence.update(extra)
            entry = AuditEntry(
                agent=agent_id,
                task=task_id,
                target=name,
                action=action,
                result=outcome,
                evidence=evidence,
            )
            return await BetaAuditLog.append_beta(audit, entry) == entry.entry_id
        except Exception:  # noqa: BLE001 — never leak storage details
            return False

    async def _audit_call(self, ctx: ToolContext, name: str, result: ToolResult) -> None:
        """Audit one tool call; beta rejection evidence has no input or errors."""
        if self._beta_policy is not None:
            await self._beta_persist(
                ctx, name, action="tool.error", outcome="denied", phase="denial", extra={}
            )
            return
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


def build_default_registry(
    tools: Iterable[Tool] | None = None,
    *,
    beta_policy: ToolPolicy | None = None,
    **ctx_kwargs: Any,
) -> ToolRegistry:
    """Return a ToolRegistry for the Agency tool layer.

    Builtin tools are registered by ``agency.tools.builtin`` (wired in a
    later brief); this factory intentionally does NOT import builtin
    modules so it stays dependency-light. Pass an optional list of
    ``Tool`` instances to pre-register them. An explicitly supplied
    beta policy must survive this factory; other ``ctx_kwargs`` remain
    ignored for compatibility.
    """
    _ = ctx_kwargs
    registry = ToolRegistry(beta_policy=beta_policy)
    for tool in tools or []:
        registry.register(tool)
    return registry


__all__ = ["BetaToolPolicy", "ToolRegistry", "build_default_registry"]
