"""Tool registry: registration, JSON-schema validation, timeouts, audit."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any
from uuid import uuid4

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

if TYPE_CHECKING:
    from agency.admission.models import OwnerExecutionGrant
    from agency.admission.policy import OwnerToolPolicy

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

    owner_policy:
        Concrete, exclusive OwnerToolPolicy. Requires the exact live owner grant,
        task and shared durable audit. Uses the same intent/outcome gate as beta,
        plus a durable grant-lowered tool reservation before dispatch. Owner quota,
        audit and uncertain outcomes raise sanitized PilotError, never retryable
        observations. Privileged context handles and mixed identities are denied.

    Beta audit gate
    ---------------
    Beta-only calls require an initialized ``BetaAuditLog``: a concrete
    disk-backed SQLite store with WAL and synchronous FULL commits. A
    verified pre-execution intent must commit before ``tool.run``; a
    verified outcome must commit before returning any successful result.
    Arbitrary append-shaped fakes and ordinary NORMAL-sync AuditLog instances
    cannot authorize execution. Storage failures return the fixed error
    without retrying a possibly committed append. Beta records omit args,
    query, output, free-form errors and raw or hashed context identifiers;
    a random per-call ID correlates intent and outcome. Denials, unknown tools
    and schema violations never execute; their audit writes are best effort
    and cannot turn a denial into an authorization. An unpaired pending intent
    suspends new beta calls, including after reopening the audit store, until
    separately reconciled. Cancellation never becomes a success: if the
    outcome is not durably acknowledged, the intent remains pending.
    This seam does not implement task-level recovery or caller-level failure
    propagation. Without ``beta_policy`` audit stays best effort.
    """

    def __init__(
        self,
        audit: Any | None = None,
        default_timeout_s: float = 30.0,
        beta_policy: ToolPolicy | None = None,
        *,
        owner_policy: OwnerToolPolicy | None = None,
    ) -> None:
        from agency.admission.policy import OwnerToolPolicy

        if owner_policy is not None and (
            type(owner_policy) is not OwnerToolPolicy or beta_policy is not None
        ):
            raise TypeError("owner policy must be concrete and exclusive")
        self._tools: dict[str, Tool] = {}
        self._timeouts: dict[str, float] = {}
        self._audit = audit
        self._default_timeout_s = default_timeout_s
        self._owner_policy = owner_policy
        self._beta_policy = beta_policy
        self._beta_suspended = False
        self._log = structlog.get_logger(__name__)

    @property
    def _strict(self) -> bool:
        return self._beta_policy is not None or self._owner_policy is not None

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
        """Call a tool by name with validated args.

        Legacy/beta tool failures return results; owner blockers raise PilotError.

        Unknown tools, schema violations, timeouts, and tool exceptions
        all become ``ToolResult(ok=False, ...)``.

        With either strict policy set, the call additionally fails closed when a
        sanitized pre-execution intent or the final outcome cannot be
        persisted; see the class docstring.
        """
        from agency.admission.models import PilotError

        owner = self._owner_policy is not None
        owner_binding = self._owner_binding(ctx) if owner else None
        if owner_binding is not None:
            await self._check_owner(ctx, owner_binding)
        elif ctx.owner_grant is not None:
            raise PilotError("forbidden")
        tool = self._tools.get(name)
        if tool is None:
            safe_name = "unknown" if self._strict else name
            result = ToolResult(tool=safe_name, ok=False, error=f"unknown tool: {safe_name}")
            await self._audit_call(ctx, safe_name, result)
            return result

        violation = _validate_args(tool.spec, args)
        if violation is not None:
            detail = "invalid args" if self._strict else f"invalid args: {violation}"
            result = ToolResult(tool=name, ok=False, error=detail)
            await self._audit_call(ctx, name, result)
            return result

        if self._strict:
            if self._beta_suspended:
                if owner:
                    raise PilotError("audit", uncertain=True)
                return ToolResult(
                    tool=name,
                    ok=False,
                    error=_BETA_AUDIT_ERROR,
                    evidence={"status": "INDETERMINATE"},
                )
            try:
                policy = self._owner_policy if owner else self._beta_policy
                assert policy is not None
                verdict = policy(tool.spec, args, ctx)
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
            audit = self._audit if self._audit is not None else ctx.audit
            if type(audit) is not BetaAuditLog:
                self._beta_suspended = True
                return ToolResult(
                    tool=name,
                    ok=False,
                    error=_BETA_AUDIT_ERROR,
                    evidence={"status": "INDETERMINATE"},
                )
            if owner:
                assert ctx.owner_grant is not None
                reserved = await ctx.owner_grant.budget.reserve_tool_call(
                    ctx.owner_grant.lease, name, limit=ctx.owner_grant.max_tool_calls
                )
                assert owner_binding is not None
                await self._check_owner(ctx, owner_binding)
                if reserved is not True:
                    raise PilotError("quota")
            call_id = uuid4().hex
            claim = await self._beta_claim(audit, name, call_id)
            if owner_binding is not None:
                await self._check_owner(ctx, owner_binding)
                if claim is not True:
                    self._beta_suspended = True
                    raise PilotError("audit", uncertain=True)
            if claim is False:
                # A live call may still write its outcome; do not latch.
                return ToolResult(
                    tool=name,
                    ok=False,
                    error=_BETA_AUDIT_ERROR,
                    evidence={"status": "INDETERMINATE"},
                )
            if claim is None:
                self._beta_suspended = True
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

            async def dispatch() -> ToolResult:
                if owner_binding is not None:
                    await self._check_owner(ctx, owner_binding)
                    policy = self._owner_policy
                    assert policy is not None
                    if policy(tool.spec, args, ctx).allowed is not True:
                        raise PilotError("forbidden")
                return await tool.run(args, ctx)

            raw = await asyncio.wait_for(dispatch(), timeout=timeout)
            duration_ms = int((time.perf_counter() - start) * 1000)
            if isinstance(raw, ToolResult):
                raw.duration_ms = duration_ms
                if not raw.tool:
                    raw.tool = name
                result = raw
            else:
                result = ToolResult(tool=name, ok=True, output=raw, duration_ms=duration_ms)
        except asyncio.CancelledError:
            if owner:
                self._beta_suspended = True
                raise PilotError("uncertainty", uncertain=True) from None
            if self._strict:
                # No completion can be asserted: the intent remains pending.
                self._beta_suspended = True
            raise
        except TimeoutError:
            if owner:
                self._beta_suspended = True
                raise PilotError("uncertainty", uncertain=True) from None
            duration_ms = int((time.perf_counter() - start) * 1000)
            result = ToolResult(
                tool=name,
                ok=False,
                error=f"TimeoutError: tool {name!r} timed out after {timeout}s",
                duration_ms=duration_ms,
            )
        except Exception as exc:  # surfaced as ToolResult
            if owner:
                if isinstance(exc, PilotError):
                    raise
                self._beta_suspended = True
                raise PilotError("uncertainty", uncertain=True) from None
            duration_ms = int((time.perf_counter() - start) * 1000)
            result = ToolResult(
                tool=name,
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
                duration_ms=duration_ms,
            )
        if owner_binding is not None:
            await self._check_owner(ctx, owner_binding, uncertain=True)
        if self._strict:
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
                call_id=call_id,
            )
            if owner_binding is not None:
                await self._check_owner(ctx, owner_binding, uncertain=True)
            if not persisted:
                if owner:
                    self._beta_suspended = True
                    raise PilotError("audit", uncertain=True)
                # Outward effects may already have happened; they cannot be
                # rolled back, so the honest status is INDETERMINATE.
                self._beta_suspended = True
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

    @staticmethod
    def _owner_facts(grant: OwnerExecutionGrant) -> tuple[Any, ...]:
        return (
            id(grant.lease),
            id(grant.budget),
            grant.allowed_tools,
            grant.max_tool_calls,
            grant.max_model_calls,
        )

    def _owner_binding(self, ctx: ToolContext) -> tuple[Any, ...]:
        from agency.admission.models import OwnerExecutionGrant, PilotError

        if type(ctx.owner_grant) is not OwnerExecutionGrant:
            raise PilotError("forbidden")
        return (
            ctx.owner_grant,
            self._owner_policy,
            self._audit,
            self._owner_facts(ctx.owner_grant),
        )

    def _validate_owner(self, ctx: ToolContext, binding: tuple[Any, ...]) -> OwnerExecutionGrant:
        from agency.admission.models import OwnerExecutionGrant, PilotError
        from agency.admission.policy import OwnerToolPolicy

        grant, policy, audit, facts = binding
        if (
            type(self) is not ToolRegistry
            or type(ctx) is not ToolContext
            or type(grant) is not OwnerExecutionGrant
            or ctx.owner_grant is not grant
            or type(policy) is not OwnerToolPolicy
            or self._owner_policy is not policy
            or self._beta_policy is not None
            or type(audit) is not BetaAuditLog
            or self._audit is not audit
            or ctx.audit is not audit
            or ctx.beta_principal is not None
            or ctx.memory_store is not None
            or ctx.sandbox_manager is not None
            or ctx.lattice is not None
        ):
            raise PilotError("forbidden")
        assert isinstance(grant, OwnerExecutionGrant)
        try:
            grant.__post_init__()
            if (
                self._owner_facts(grant) != facts
                or type(ctx.task_id) is not str
                or ctx.task_id != grant.lease.task_id
            ):
                raise PilotError("forbidden")
        except (AttributeError, TypeError, ValueError):
            raise PilotError("forbidden") from None
        return grant

    async def _check_owner(
        self, ctx: ToolContext, binding: tuple[Any, ...], *, uncertain: bool = False
    ) -> None:
        from agency.admission.models import PilotError

        try:
            grant = self._validate_owner(ctx, binding)
            active = await grant.budget._active(grant.lease)
            self._validate_owner(ctx, binding)
            if active is not True:
                raise PilotError("forbidden")
        except (Exception, asyncio.CancelledError):  # noqa: BLE001 -- fail closed at storage boundary
            raise PilotError(
                "uncertainty" if uncertain else "forbidden", uncertain=uncertain
            ) from None

    async def _beta_claim(self, audit: BetaAuditLog, name: str, call_id: str) -> bool | None:
        """True only for acknowledged admission, False for pending, None for outage."""
        entry = AuditEntry(
            agent="owner-tools" if self._owner_policy is not None else None,
            task="owner-pilot" if self._owner_policy is not None else None,
            target=name,
            action="tool.call",
            result="pending",
            evidence={"call_id": call_id, "tool": name, "phase": "intent"},
        )
        try:
            return await BetaAuditLog.claim_intent(audit, entry)
        except asyncio.CancelledError:
            if self._owner_policy is not None:
                from agency.admission.models import PilotError

                self._beta_suspended = True
                raise PilotError("audit", uncertain=True) from None
            # The claim may have committed. Never execute or retry it.
            self._beta_suspended = True
            raise
        except Exception:  # noqa: BLE001 — never leak storage details
            return None

    async def _beta_persist(
        self,
        ctx: ToolContext,
        name: str,
        *,
        action: str,
        outcome: str,
        phase: str,
        extra: dict[str, Any],
        call_id: str | None = None,
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
            evidence: dict[str, Any] = {
                "call_id": call_id or uuid4().hex,
                "tool": name,
                "phase": phase,
            }
            evidence.update(extra)
            entry = AuditEntry(
                agent="owner-tools" if self._owner_policy is not None else None,
                task="owner-pilot" if self._owner_policy is not None else None,
                target=name,
                action=action,
                result=outcome,
                evidence=evidence,
            )
            return await BetaAuditLog.append_beta(audit, entry) == entry.entry_id
        except asyncio.CancelledError:
            if self._owner_policy is not None:
                from agency.admission.models import PilotError

                self._beta_suspended = True
                raise PilotError("audit", uncertain=True) from None
            self._beta_suspended = True
            raise
        except Exception:  # noqa: BLE001 — never leak storage details
            return False

    async def _audit_call(self, ctx: ToolContext, name: str, result: ToolResult) -> None:
        """Audit one tool call; beta rejection evidence has no input or errors."""
        if self._strict:
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
    owner_policy: OwnerToolPolicy | None = None,
    **ctx_kwargs: Any,
) -> ToolRegistry:
    """Return a ToolRegistry for the Agency tool layer.

    Builtin tools are registered by ``agency.tools.builtin`` (wired in a
    later brief); this factory intentionally does NOT import builtin
    modules so it stays dependency-light. Pass an optional list of
    ``Tool`` instances to pre-register them. An explicitly supplied
    beta policy must survive this factory. A concrete owner policy is exclusive
    with beta; its explicitly supplied audit survives the factory as well.
    Other ``ctx_kwargs`` remain ignored for compatibility.
    """
    _ = ctx_kwargs
    registry = ToolRegistry(
        beta_policy=beta_policy,
        owner_policy=owner_policy,
        audit=ctx_kwargs.get("audit") if owner_policy is not None else None,
    )
    for tool in tools or []:
        registry.register(tool)
    return registry


__all__ = ["BetaToolPolicy", "ToolRegistry", "build_default_registry"]
