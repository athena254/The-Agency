"""Fail-closed owner tool admission over a live budget-issued grant."""

from __future__ import annotations

from typing import Any

from agency.admission.models import OwnerExecutionGrant, PilotError
from agency.tools.base import ToolContext, ToolPolicyDecision, ToolRisk, ToolSpec

_ALLOWED_TOOLS = frozenset({"calculator", "utc_time", "web_search"})
_MAX_EXPRESSION_CHARS = 120
_MAX_QUERY_CHARS = 200
_DEFAULT_MAX_RESULTS = 5
_MAX_RESULTS = 5


class OwnerToolPolicy:
    """Admit only bounded read-only tools under the exact live owner lease.

    The policy performs no I/O, mints no grants and dispatches no tools. It
    revalidates the concrete grant and asks the issuing budget adapter whether
    the original lease object is still owned and live. Cancellation, expiry,
    revocation, closed stores and mutated/copied leases therefore fail closed.
    ``ctx.metadata`` is never consulted for authority.
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
        if risk is not ToolRisk.READ_ONLY:
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
        else:
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
        return ToolPolicyDecision(True, f"owner: allowed {name}")


__all__ = ["OwnerToolPolicy"]
