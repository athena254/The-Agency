"""Bounded apprentice policy for the Forge factory.

The apprentice is an L0 worker: it may take trivial jobs, never risky ones,
runs under a hard tool-budget cap, and reaches striker work only with recorded
promotion evidence. The module is pure — no I/O, no LLM, no subprocess. The
work-order renderer embeds the policy text, and ``enforce_apprentice_policy``
is the seam a dispatcher can call before handing a job to a worker (it cannot
be wired inside ``external_agent.py`` from here).
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

APPRENTICE_ROLE = "apprentice"
TRIVIAL_CLASS = "trivial"
STANDARD_CLASS = "standard"
RISKY_CLASS = "risky"

DEFAULT_TOOL_BUDGET_CAP = 12
MAX_TOOL_BUDGET_CAP = 64
PROMOTION_TASK_THRESHOLD = 10

_POLICY_CAVEAT = "apprentice_policy_evaluated"


class PromotionEvidence(BaseModel):
    """Recorded proof that an apprentice may be promoted to striker work."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    passed_tasks: int = Field(default=0, ge=0)
    isolation_violations: int = Field(default=0, ge=0)
    approver: str = ""
    note: str = ""

    def is_sufficient(self) -> bool:
        """Promotion requires enough clean, gated work and a named human."""
        return (
            self.passed_tasks >= PROMOTION_TASK_THRESHOLD
            and self.isolation_violations == 0
            and bool(self.approver.strip())
        )

    def render(self) -> str:
        status = "sufficient" if self.is_sufficient() else "insufficient"
        return (
            f"passed_gated_tasks={self.passed_tasks} (need {PROMOTION_TASK_THRESHOLD}), "
            f"isolation_violations={self.isolation_violations}, "
            f"approver={self.approver.strip() or 'none'}, status={status}"
        )


class ApprenticePolicy(BaseModel):
    """Immutable policy values; the default mirrors the trivial routing cap."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tool_budget_cap: int = Field(default=DEFAULT_TOOL_BUDGET_CAP, ge=0, le=MAX_TOOL_BUDGET_CAP)
    allowed_classes: tuple[str, ...] = (TRIVIAL_CLASS,)
    refused_classes: tuple[str, ...] = (RISKY_CLASS,)


DEFAULT_POLICY = ApprenticePolicy()


def _norm(value: str) -> str:
    return (value or "").strip().lower()


def _as_int(value: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _evaluate(
    role: str,
    job_class: str,
    promotion_evidence: PromotionEvidence | None,
    policy: ApprenticePolicy,
) -> tuple[bool, tuple[str, ...]]:
    """Return (allowed, reasons). Only apprentices are constrained here."""
    normalized_role, normalized_class = _norm(role), _norm(job_class)
    reasons: list[str] = []
    if normalized_role == APPRENTICE_ROLE:
        if normalized_class in policy.refused_classes:
            reasons.append("apprentice_refuses_risky_class")
        elif normalized_class == STANDARD_CLASS:
            if promotion_evidence is None or not promotion_evidence.is_sufficient():
                reasons.append("apprentice_requires_promotion_evidence")
        elif normalized_class not in policy.allowed_classes:
            reasons.append("apprentice_unknown_job_class")
    return (not reasons), tuple(reasons)


def can_take(
    role: str,
    job_class: str,
    *,
    promotion_evidence: PromotionEvidence | None = None,
    policy: ApprenticePolicy | None = None,
) -> bool:
    """Whether *role* may take *job_class* under the (default) policy."""
    return _evaluate(role, job_class, promotion_evidence, policy or DEFAULT_POLICY)[0]


def refusal_reasons(
    role: str,
    job_class: str,
    *,
    promotion_evidence: PromotionEvidence | None = None,
    policy: ApprenticePolicy | None = None,
) -> tuple[str, ...]:
    """Why a job was refused (empty when allowed)."""
    return _evaluate(role, job_class, promotion_evidence, policy or DEFAULT_POLICY)[1]


def tool_budget(role: str, requested: int, *, policy: ApprenticePolicy | None = None) -> int:
    """Cap the tool budget: apprentices get the strict cap, others the global cap."""
    resolved = policy or DEFAULT_POLICY
    value = _as_int(requested)
    if _norm(role) == APPRENTICE_ROLE:
        return min(value, resolved.tool_budget_cap)
    return min(value, MAX_TOOL_BUDGET_CAP)


@dataclass(frozen=True)
class EnforcementResult:
    """Outcome of an apprentice-policy check, safe to fold into a gate report."""

    allowed: bool
    role: str
    job_class: str
    effective_tool_budget: int
    reasons: tuple[str, ...] = ()
    caveats: tuple[str, ...] = ()


def enforce_apprentice_policy(
    role: str,
    job_class: str,
    requested_tool_budget: int = DEFAULT_TOOL_BUDGET_CAP,
    *,
    promotion_evidence: PromotionEvidence | None = None,
    policy: ApprenticePolicy | None = None,
) -> EnforcementResult:
    """Dispatcher seam: decide a job and expose reasons/caveats honestly.

    Wire this into ``external_agent.py`` before dispatch: refuse when
    ``allowed`` is false, apply ``effective_tool_budget`` to the worker, and
    append ``caveats`` to the report's caveat tuple.
    """
    resolved = policy or DEFAULT_POLICY
    allowed, reasons = _evaluate(role, job_class, promotion_evidence, resolved)
    effective = tool_budget(role, requested_tool_budget, policy=resolved)
    caveats: list[str] = [_POLICY_CAVEAT]
    if effective < _as_int(requested_tool_budget):
        caveats.append("tool_budget_capped")
    if _norm(role) == APPRENTICE_ROLE and promotion_evidence is not None:
        caveats.append("promotion_evidence_recorded")
        if promotion_evidence.is_sufficient():
            caveats.append("apprentice_promoted")
    return EnforcementResult(
        allowed=allowed,
        role=_norm(role),
        job_class=_norm(job_class),
        effective_tool_budget=effective,
        reasons=reasons,
        caveats=tuple(caveats),
    )


def render_apprentice_policy(
    role: str,
    job_class: str = "",
    requested_budget: int = DEFAULT_TOOL_BUDGET_CAP,
    promotion_evidence: PromotionEvidence | None = None,
    *,
    policy: ApprenticePolicy | None = None,
) -> str:
    """Render the policy into a work order; empty for non-apprentice roles."""
    if _norm(role) != APPRENTICE_ROLE:
        return ""
    resolved = policy or DEFAULT_POLICY
    decision = (
        "ALLOWED"
        if can_take(role, job_class, promotion_evidence=promotion_evidence, policy=resolved)
        else "REFUSED"
    )
    effective = tool_budget(role, requested_budget, policy=resolved)
    lines = [
        "APPRENTICE POLICY (enforced):",
        f"- job class: {_norm(job_class) or 'unspecified'} -> {decision}.",
        f"- tool budget: at most {effective} tool calls (hard cap {resolved.tool_budget_cap}).",
        "- risky classes are always refused; no push, merge, release or deploy.",
        (
            f"- promotion to striker work needs {PROMOTION_TASK_THRESHOLD} passing gated tasks,"
            " 0 isolation violations and a named approver."
        ),
    ]
    if promotion_evidence is not None:
        lines.append(f"- recorded promotion evidence: {promotion_evidence.render()}")
    return "\n".join(lines)


__all__ = [
    "DEFAULT_POLICY",
    "DEFAULT_TOOL_BUDGET_CAP",
    "MAX_TOOL_BUDGET_CAP",
    "PROMOTION_TASK_THRESHOLD",
    "ApprenticePolicy",
    "EnforcementResult",
    "PromotionEvidence",
    "can_take",
    "enforce_apprentice_policy",
    "refusal_reasons",
    "render_apprentice_policy",
    "tool_budget",
]
