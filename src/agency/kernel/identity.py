"""Agent identity, capabilities and trust levels.

The identity module defines *who* an agent is and *what* it may do. Every
security decision made by :mod:`agency.kernel.policies` is anchored to an
:class:`Agent` identity, so the fields here are deliberately explicit:

- ``id`` — stable, unique identifier referenced by permissions, tasks and audit.
- ``capabilities`` — named capabilities, each bounded by a maximum
  :class:`~agency.kernel.policies.ActionClass` level (least privilege by default).
- ``trust_level`` — the provenance confidence assigned to the agent.

The kernel treats an agent's *stated* capabilities as claims until they are
bound by an issued :class:`~agency.kernel.policies.Permission`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agency.kernel.policies import ActionClass


def utcnow() -> datetime:
    """Return a timezone-aware UTC timestamp (microsecond precision)."""
    return datetime.now(UTC)


class TrustLevel(str, Enum):
    """Provenance confidence assigned to an agent identity.

    Ordering matters: higher trust levels unlock higher-impact action
    classes. An agent with ``UNKNOWN`` provenance is trusted with
    observation only.

    Because the enum derives from ``str``, comparisons use the explicit
    :attr:`rank` instead of overriding ``__lt__``/``__ge__`` (which would
    violate Liskov substitutability against ``str``).
    """

    UNKNOWN = "unknown"
    OBSERVED = "observed"
    VERIFIED = "verified"
    TRUSTED = "trusted"
    HIGH_TRUST = "high_trust"

    @property
    def rank(self) -> int:
        """Return the numeric order 0 (least trusted) .. 4 (most trusted)."""
        return _TRUST_ORDER[self]


_TRUST_ORDER: dict[TrustLevel, int] = {
    TrustLevel.UNKNOWN: 0,
    TrustLevel.OBSERVED: 1,
    TrustLevel.VERIFIED: 2,
    TrustLevel.TRUSTED: 3,
    TrustLevel.HIGH_TRUST: 4,
}


class Capability(BaseModel):
    """A named, bounded capability an agent may exercise.

    ``max_level`` is the ceiling of :class:`ActionClass` this capability can
    authorize. Capabilities default to :data:`ActionClass.L0_OBSERVATION`
    (least privilege) so a privilege escalation must always be explicit.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1, description="Unique capability name, e.g. 'inspect'.")
    description: str = Field(default="", description="Human-readable capability description.")
    max_level: ActionClass = Field(
        default=ActionClass.L0_OBSERVATION,
        description="Highest action class this capability can authorize.",
    )

    @field_validator("name")
    @classmethod
    def _normalize_name(cls, value: str) -> str:
        name = value.strip().lower()
        if not name:
            raise ValueError("Capability name must not be empty.")
        return name

    def __str__(self) -> str:
        return self.name


class Agent(BaseModel):
    """An identity within the agency.

    Attributes
    ----------
    id:
        Stable unique identifier; generated as a UUID v4 if not provided.
    name:
        Human-readable agent name.
    domain:
        Operational domain, e.g. ``security``, ``finance``, ``research``.
    capabilities:
        Capabilities claimed by the agent, each bounded by a max action
        class. Plain strings are coerced to default L0 capabilities.
    trust_level:
        Provenance confidence assigned to the identity.
    created_at:
        UTC creation timestamp; set automatically if not provided.
    metadata:
        Arbitrary identity metadata (team, model, owner, ...).
    revoked:
        Set by :meth:`AgentRegistry.revoke`; a revoked identity cannot act.
    """

    model_config = ConfigDict(
        frozen=False,
        extra="forbid",
        json_schema_extra={"description": "An agency agent identity."},
    )

    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str = Field(min_length=1)
    domain: str = Field(default="general")
    capabilities: list[Capability] = Field(default_factory=list)
    trust_level: TrustLevel = Field(default=TrustLevel.UNKNOWN)
    created_at: datetime = Field(default_factory=utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)
    revoked: bool = Field(default=False, description="Identity has been revoked.")

    @field_validator("capabilities", mode="before")
    @classmethod
    def _coerce_capabilities(
        cls, value: list[str | Capability] | Capability | str | None
    ) -> list[Capability]:
        """Coerce plain capability names into default L0 :class:`Capability` objects."""
        if value is None:
            return []
        if isinstance(value, (str, Capability)):
            value = [value]
        result: list[Capability] = []
        for entry in value:
            if isinstance(entry, Capability):
                result.append(entry)
            elif isinstance(entry, str):
                result.append(Capability(name=entry))
            else:
                raise TypeError(f"Unsupported capability entry: {type(entry).__name__}")
        return result

    @field_validator("id")
    @classmethod
    def _normalize_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Agent id must not be empty.")
        return value

    def has_capability(self, name: str) -> bool:
        """Return whether this agent claims the named capability."""
        needle = name.strip().lower()
        return any(cap.name == needle for cap in self.capabilities)

    def capability_level(self, name: str) -> ActionClass | None:
        """Return the max action class bound to a capability, or ``None``."""
        for cap in self.capabilities:
            if cap.name == name.strip().lower():
                return cap.max_level
        return None

    def grant(self, capability: str | Capability) -> Agent:
        """Grant a capability in place; :returns:`self` for chaining."""
        cap = capability if isinstance(capability, Capability) else Capability(name=capability)
        if not self.has_capability(cap.name):
            self.capabilities.append(cap)
        return self

    def revoke_capability(self, name: str) -> Agent:
        """Remove a capability in place; :returns:`self` for chaining."""
        needle = name.strip().lower()
        self.capabilities = [cap for cap in self.capabilities if cap.name != needle]
        return self
