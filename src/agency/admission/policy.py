"""Fail-closed owner tool admission over a live budget-issued grant."""

from __future__ import annotations

from typing import Any

from agency.admission.models import (
    OWNER_TOOL_NAMES,
    OwnerExecutionGrant,
    PilotError,
)
from agency.tools.base import ToolContext, ToolPolicyDecision, ToolRisk, ToolSpec

_ALLOWED_TOOLS = OWNER_TOOL_NAMES
_MEMORY_TOOLS = frozenset({"memory_query", "memory_write"})
_MAX_EXPRESSION_CHARS = 120
_MAX_QUERY_CHARS = 200
_DEFAULT_MAX_RESULTS = 5
_MAX_RESULTS = 5
_MAX_MEMORY_QUERY_CHARS = 200
_MAX_MEMORY_CONTENT_CHARS = 8000
_MAX_MEMORY_TITLE_CHARS = 200
_MAX_MEMORY_LIMIT = 100
_DEFAULT_MEMORY_LIMIT = 10
_MAX_FETCH_URL_CHARS = 2048


class OwnerToolPolicy:
    """Admit only bounded owner tools under the exact live owner lease.

    The default allowlist is the three read-only tools (calculator, utc_time,
    web_search). The additional memory and fetch tools are admitted only when
    the owner pilot configuration explicitly opts in and the call carries the
    matching guard: memory tools require a principal-bound memory scope (R-04)
    and web_fetch requires connection-address pinning (B-05), which the tool
    itself enforces. The policy performs no I/O, mints no grants and dispatches
    no tools. It revalidates the concrete grant and asks the issuing budget
    adapter whether the original lease object is still owned and live.
    Cancellation, expiry, revocation, closed stores and mutated/copied leases
    therefore fail closed. ``ctx.metadata`` is never consulted for authority.
    """

    def __call__(
        self, spec: ToolSpec, args: dict[str, Any], ctx: ToolContext
    ) -> ToolPolicyDecision:
        grant = ctx.owner_grant
        if type(grant) is not OwnerExecutionGrant:
            return ToolPolicyDecision(False, "owner: missing live execution grant")
        if ctx.beta_principal is not None:
            return ToolPolicyDecision(False, "owner: mixed beta identity denied")
        if (
            ctx.memory_store is not None
            or ctx.sandbox_manager is not None
            or ctx.lattice is not None
        ):
            return ToolPolicyDecision(False, "owner: privileged handles denied")
        try:
            grant.__post_init__()
            live = grant.budget.owns(grant.lease)
        except (PilotError, AttributeError, TypeError, ValueError):
            return ToolPolicyDecision(False, "owner: invalid execution grant")
        if live is not True:
            return ToolPolicyDecision(False, "owner: grant is not live")
        if type(ctx.task_id) is not str or ctx.task_id != grant.lease.task_id:
            return ToolPolicyDecision(False, "owner: task binding mismatch")
        name = getattr(spec, "name", None)
        risk = getattr(spec, "risk", None)
        if name not in _ALLOWED_TOOLS or name not in grant.allowed_tools:
            return ToolPolicyDecision(False, f"owner: tool {name!r} denied")
        scoped_memory = name in _MEMORY_TOOLS
        if scoped_memory and ctx.memory_scope is None:
            return ToolPolicyDecision(False, "owner: memory tools require a principal-bound scope")
        if risk is not ToolRisk.READ_ONLY and not (name == "memory_write" and scoped_memory):
            return ToolPolicyDecision(False, f"owner: risk {risk!r} denied")
        if type(args) is not dict:
            return ToolPolicyDecision(False, "owner: args must be an object")
        if name == "calculator":
            if set(args) != {"expression"}:
                return ToolPolicyDecision(False, "owner: calculator args invalid")
            expression = args["expression"]
            if (
                type(expression) is not str
                or not expression.strip()
                or len(expression) > _MAX_EXPRESSION_CHARS
            ):
                return ToolPolicyDecision(False, "owner: calculator expression invalid")
        elif name == "utc_time":
            if args:
                return ToolPolicyDecision(False, "owner: utc_time takes no args")
        elif name == "web_search":
            if not set(args) <= {"query", "max_results"} or "query" not in args:
                return ToolPolicyDecision(False, "owner: web_search args invalid")
            query = args["query"]
            if type(query) is not str or not query.strip() or len(query.strip()) > _MAX_QUERY_CHARS:
                return ToolPolicyDecision(False, "owner: web_search query invalid")
            max_results = args.get("max_results", _DEFAULT_MAX_RESULTS)
            if (
                type(max_results) is not int
                or isinstance(max_results, bool)
                or not 1 <= max_results <= _MAX_RESULTS
            ):
                return ToolPolicyDecision(False, "owner: web_search max_results invalid")
        elif name == "memory_query":
            if not set(args) <= {"query", "limit"}:
                return ToolPolicyDecision(False, "owner: memory_query args invalid")
            query = args.get("query")
            if query is not None and (
                type(query) is not str or len(query) > _MAX_MEMORY_QUERY_CHARS
            ):
                return ToolPolicyDecision(False, "owner: memory_query query invalid")
            limit = args.get("limit", _DEFAULT_MEMORY_LIMIT)
            if (
                type(limit) is not int
                or isinstance(limit, bool)
                or not 1 <= limit <= _MAX_MEMORY_LIMIT
            ):
                return ToolPolicyDecision(False, "owner: memory_query limit invalid")
        elif name == "memory_write":
            if not set(args) <= {"content", "title"}:
                return ToolPolicyDecision(False, "owner: memory_write args invalid")
            content = args.get("content")
            if (
                type(content) is not str
                or not content.strip()
                or len(content) > _MAX_MEMORY_CONTENT_CHARS
            ):
                return ToolPolicyDecision(False, "owner: memory_write content invalid")
            title = args.get("title")
            if title is not None and (
                type(title) is not str or len(title) > _MAX_MEMORY_TITLE_CHARS
            ):
                return ToolPolicyDecision(False, "owner: memory_write title invalid")
        elif name == "web_fetch":
            if set(args) != {"url"}:
                return ToolPolicyDecision(False, "owner: web_fetch args invalid")
            url = args["url"]
            if type(url) is not str or not url.strip() or len(url) > _MAX_FETCH_URL_CHARS:
                return ToolPolicyDecision(False, "owner: web_fetch url invalid")
        else:
            return ToolPolicyDecision(False, f"owner: tool {name!r} denied")
        return ToolPolicyDecision(True, f"owner: allowed {name}")


__all__ = ["OwnerToolPolicy"]
