"""Policy engine — deterministic authorization for agent actions.

The policy engine is the enforcement point of the Agency Kernel. It answers a
single question for every proposed action:

    ``can_execute(action_class, agent, target) -> bool``

The answer is derived **only** from deterministic inputs — the agent's
:class:`~agency.kernel.identity.TrustLevel`, its bounded capabilities, and the
issued :class:`Permission` matching the target scope. LLM output is never used
to authorize an action.

Severity boundary (ref SPEC §16):

    L0 Observation        -> reading approved sources, analyzing logs
    L1 Safe analysis      -> static/dependency/config analysis, sandboxed eval
    L2 Controlled testing -> isolated app testing, synthetic inputs
    L3 High-impact        -> explicit authorization + stronger controls
    L4 Production         -> requires human approval
    L5 Destructive        -> never autonomous
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import PurePosixPath
from re import match as re_match
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, field_validator

if TYPE_CHECKING:
    from agency.kernel.identity import Agent, TrustLevel


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ActionClass(str, Enum):
    """Severity ranking of agent actions.

    Enum ordering mirrors the L0..L5 severity ladder; ``.__ge__`` etc. are
    not needed because we compare via the ``level`` property instead.
    """

    L0_OBSERVATION = "L0_OBSERVATION"
    L1_SAFE_ANALYSIS = "L1_SAFE_ANALYSIS"
    L2_CONTROLLED_TESTING = "L2_CONTROLLED_TESTING"
    L3_HIGH_IMPACT = "L3_HIGH_IMPACT"
    L4_PRODUCTION = "L4_PRODUCTION"
    L5_DESTRUCTIVE = "L5_DESTRUCTIVE"

    @property
    def level(self) -> int:
        """Return the numeric severity 0..5."""
        return int(self.value.split("_")[0][1:])

    def __str__(self) -> str:
        return self.value


def _required_trust(action: ActionClass) -> TrustLevel:
    """Return the minimum trust level required by ``action``.

    Resolved lazily (inside the function body) so neither module has to import
    the other at module load time — breaking the identity <-> policies import
    cycle while keeping a single, obvious source of truth at runtime.
    """
    from agency.kernel.identity import TrustLevel

    trust_by_action: dict[ActionClass, TrustLevel] = {
        ActionClass.L0_OBSERVATION: TrustLevel.UNKNOWN,
        ActionClass.L1_SAFE_ANALYSIS: TrustLevel.OBSERVED,
        ActionClass.L2_CONTROLLED_TESTING: TrustLevel.VERIFIED,
        ActionClass.L3_HIGH_IMPACT: TrustLevel.TRUSTED,
        ActionClass.L4_PRODUCTION: TrustLevel.TRUSTED,
        ActionClass.L5_DESTRUCTIVE: TrustLevel.HIGH_TRUST,
    }
    return trust_by_action[action]


class NetworkScope(str, Enum):
    """Network reachability classes in least-privilege order."""

    NONE = "none"
    LOOPBACK = "loopback"
    PRIVATE = "private"
    LIMITED = "limited"
    FULL = "full"


class NetworkPolicy(BaseModel):
    """Network access constraints bound to a permission."""

    model_config = ConfigDict(frozen=True)

    scope: NetworkScope = Field(default=NetworkScope.NONE)
    allowed_hosts: list[str] = Field(
        default_factory=list, description="Exact hosts; empty = none beyond scope."
    )
    allowed_ports: list[int] = Field(
        default_factory=list, description="Ports; empty = any within scope."
    )

    def allows(self, host: str, port: int) -> bool:
        """Return whether a concrete (host, port) contact is permitted."""
        if self.allowed_hosts and host not in self.allowed_hosts:
            return False
        return not self.allowed_ports or port in self.allowed_ports


class FilesystemPolicy(BaseModel):
    """Filesystem access constraints bound to a permission."""

    model_config = ConfigDict(frozen=True)

    roots: tuple[PurePosixPath, ...] = Field(
        default_factory=tuple,
        description="Absolute paths the agent may touch; empty tuple = none.",
    )
    read_only: bool = Field(default=True, description="Forbid any write when True.")
    forbidden_suffixes: tuple[str, ...] = Field(
        default_factory=tuple,
        description="e.g. ('.env', '.pem'); applied to file names.",
    )

    def allows_read(self, path: str | PurePosixPath) -> bool:
        """Return whether reading ``path`` is inside an allowed root."""
        return self._within_roots(path)

    def allows_write(self, path: str | PurePosixPath) -> bool:
        """Return whether writing ``path`` is permitted (not read-only, in-root)."""
        if self.read_only:
            return False
        return self._within_roots(path)

    def _within_roots(self, path: str | PurePosixPath) -> bool:
        candidate = PurePosixPath(path)
        if candidate.suffix in self.forbidden_suffixes:
            return False
        return any(str(candidate).startswith(str(root)) for root in self.roots)


class DataPolicy(BaseModel):
    """Data handling constraints bound to a permission."""

    model_config = ConfigDict(frozen=True)

    allow_pii: bool = Field(default=False, description="May the agent store/see PII.")
    allow_persist: bool = Field(
        default=False, description="May results be persisted off-ephemeral."
    )
    max_egress_bytes: int = Field(
        default=0, ge=0, description="Max data leaving the isolation boundary."
    )
    allowed_data_types: tuple[str, ...] = Field(
        default_factory=tuple, description="e.g. ('logs', 'synthetic', 'metrics')."
    )


class ResourceLimits(BaseModel):
    """Consumable-resource ceilings bound to a permission."""

    model_config = ConfigDict(frozen=True)

    max_cpu_seconds: int = Field(default=0, ge=0, description="0 = unlimited.")
    max_memory_mb: int = Field(default=0, ge=0, description="0 = unlimited.")
    max_network_mb: int = Field(default=0, ge=0, description="0 = unlimited.")
    max_concurrency: int = Field(default=1, ge=1)


class Permission(BaseModel):
    """A scoped authorization granted to one agent.

    A permission grants the *agent* the right to exercise named
    ``capabilities`` against ``target_scope``. The action class an agent may
    actually reach is the intersection of these named capability ceilings and
    its trust level — the policy engine computes that intersection.

    ``target_scope`` supports ``*`` glob wildcards (whole-element or
    trailing), e.g. ``staging/*`` or ``api.v1.*``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    agent_id: str = Field(min_length=1)
    target_scope: str = Field(
        min_length=1, description="Glob-pattern scope this permission covers."
    )
    capabilities: list[str] = Field(
        default_factory=list, description="Capability names this permission activates."
    )
    network_policy: NetworkPolicy = Field(default_factory=NetworkPolicy)
    filesystem_policy: FilesystemPolicy = Field(default_factory=FilesystemPolicy)
    data_policy: DataPolicy = Field(default_factory=DataPolicy)
    time_limit: timedelta | None = Field(
        default=None,
        description=(
            "Grant validity window: the permission lapses this long after "
            "created_at (None = no relative expiry)."
        ),
    )
    resource_limits: ResourceLimits = Field(default_factory=ResourceLimits)
    created_at: datetime = Field(default_factory=_utcnow)
    expires_at: datetime | None = Field(
        default=None, description="Absolute expiry; None = no absolute expiry."
    )
    human_approved: bool = Field(
        default=False, description="Required for L4 (and L5 override) actions."
    )

    @field_validator("target_scope")
    @classmethod
    def _validate_scope(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("target_scope must not be empty.")
        if any(char in value for char in (" ", "\t", "\n")):
            raise ValueError("target_scope must not contain whitespace.")
        return value

    @field_validator("capabilities", mode="before")
    @classmethod
    def _coerce_capabilities(cls, value: list[str] | str | None) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        return [cap.strip().lower() for cap in value if cap.strip()]

    def is_expired(self, at: datetime | None = None) -> bool:
        """Return whether the permission has lapsed (absolute or relative)."""
        now = at or _utcnow()
        if self.expires_at is not None and now >= self.expires_at:
            return True
        if self.time_limit is not None:
            dead = (
                (self.created_at + self.time_limit) if self.created_at.tzinfo else self.created_at
            )
            if dead.tzinfo is None:
                dead = dead.replace(tzinfo=UTC)
            if now >= dead:
                return True
        return False

    def matches_scope(self, target: str) -> bool:
        """Return whether ``target`` falls under this permission's scope glob."""
        if self.target_scope == "*":
            return True
        pattern = _glob_to_regex(self.target_scope)
        return re_match(pattern, target) is not None


def _glob_to_regex(pattern: str) -> str:
    """Translate a simple shell-style glob into an anchored regex.

    ``*`` matches any run of characters; everything else is literal.
    """
    out: list[str] = ["^"]
    for char in pattern:
        if char == "*":
            out.append(".*")
        else:
            out.append(_re_escape(char))
    out.append("$")
    return "".join(out)


_SPECIAL_RE_CHARS = {".", "^", "$", "+", "?", "[", "]", "{", "}", "(", ")", "\\", "|"}


def _re_escape(char: str) -> str:
    return "\\" + char if char in _SPECIAL_RE_CHARS else char


@dataclass
class Decision:
    """Result of a full policy evaluation."""

    allowed: bool
    action: ActionClass
    agent_id: str
    target: str
    reasons: list[str] = field(default_factory=list)
    matched_permission: Permission | None = None

    @property
    def denied(self) -> bool:
        """Inverse of :attr:`allowed`."""
        return not self.allowed


class PolicyEngine:
    """Enforces whether an agent may execute an action against a target.

    The engine holds the set of issued permissions and evaluates the full
    trust-and-capability intersection per request.
    """

    def __init__(self) -> None:
        # Scope patterns preserved for introspection in the order granted.
        self._permissions: dict[tuple[str, str], Permission] = {}

    # ------------------------------------------------------------------ #
    # Permission bookkeeping
    # ------------------------------------------------------------------ #

    def issue(self, permission: Permission) -> None:
        """Register a permission; replaces any prior permission with the same (agent, scope)."""
        key = (permission.agent_id, permission.target_scope)
        self._permissions[key] = permission

    def revoke(self, agent_id: str, target_scope: str) -> bool:
        """Revoke a specific permission; returns True if one was removed."""
        key = (agent_id, target_scope)
        return self._permissions.pop(key, None) is not None

    def revoke_all(self, agent_id: str) -> int:
        """Revoke every permission issued to an agent; returns the count removed."""
        expired = [key for key in self._permissions if key[0] == agent_id]
        for key in expired:
            del self._permissions[key]
        return len(expired)

    def permissions_for(self, agent_id: str) -> list[Permission]:
        """Return all live permissions issued to an agent."""
        return [perm for (aid, _), perm in self._permissions.items() if aid == agent_id]

    # ------------------------------------------------------------------ #
    # Evaluation
    # ------------------------------------------------------------------ #

    def can_execute(self, action: ActionClass, agent: Agent, target: str) -> bool:
        """Return whether ``agent`` may run ``action`` against ``target``.

        This is a thin wrapper over :meth:`evaluate` for call sites that only
        need a boolean gate.
        """
        return self.evaluate(action, agent, target).allowed

    def evaluate(self, action: ActionClass, agent: Agent, target: str) -> Decision:
        """Perform the full deterministic authorization check with reasons."""
        reasons: list[str] = []

        if agent.revoked:
            reasons.append(f"agent {agent.id!r} is revoked")

        # Trust boundary first: no permission can compensate for untrusted
        # provenance on high-impact classes.
        required = _required_trust(action)
        if agent.trust_level.rank < required.rank:
            reasons.append(
                f"trust {agent.trust_level.value!r} < required {required.value!r} for {action.value}"
            )

        # Capability ceiling: the agent must hold at least one capability
        # whose max action class can cover the requested action.
        if not any(cap.max_level.level >= action.level for cap in agent.capabilities):
            reasons.append(f"no capability covers {action.value}")

        # Human-gate on production-impacting actions.
        if action in (
            ActionClass.L4_PRODUCTION,
            ActionClass.L5_DESTRUCTIVE,
        ) and not self._human_gate_met(action, agent, target):
            reasons.append(f"{action.value} requires an explicit human-approved permission")

        # Match a live permission against the target scope and capability set.
        matched: Permission | None = None
        for key, perm in self._permissions.items():
            if key[0] != agent.id:
                continue
            if not perm.matches_scope(target):
                continue
            matched = perm
            break
        if matched is None:
            reasons.append(f"no permission for scope targeting {target!r}")
        else:
            if matched.is_expired():
                reasons.append("matched permission is expired")
            names = {cap.name for cap in agent.capabilities}
            granted = set(matched.capabilities) & names
            if not granted:
                reasons.append(
                    f"permission {matched.target_scope!r} grants none of the agent's capabilities"
                )
            else:
                ceilings = {cap.name: cap.max_level.level for cap in agent.capabilities}
                if max(ceilings[name] for name in granted) < action.level:
                    reasons.append(
                        f"granted capabilities do not reach {action.value} (need level {action.level})"
                    )

        return Decision(
            allowed=not reasons,
            action=action,
            agent_id=agent.id,
            target=target,
            reasons=reasons,
            matched_permission=matched,
        )

    def _human_gate_met(self, action: ActionClass, agent: Agent, target: str) -> bool:
        """Return whether an explicitly human-approved permission covers the pair."""
        for perm in self.permissions_for(agent.id):
            if perm.human_approved and perm.matches_scope(target):
                if action is ActionClass.L5_DESTRUCTIVE and perm.time_limit is None:
                    # Destructive actions must be bounded in time as well.
                    continue
                return True
        return False
