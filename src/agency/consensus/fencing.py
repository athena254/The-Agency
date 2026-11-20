"""Resource-side actuator fencing and idempotent finality receipts (A4/P2).

This is the actuator-side companion to :mod:`agency.consensus.grant_guard`: a
caller-supplied actuator is wrapped so that each grant is verified at the point
of use, each resource advances a monotonic fencing token, and each executed
grant digest replays its recorded outcome exactly once. Verification is fully
delegated to :func:`agency.consensus.grant_guard.enforce_grant` and the real
grant types; no parallel grant model is introduced.

Deterministic and local: no wall clock, no randomness, no network and no side
effect beyond the single actuator call. Committed time is supplied by the caller
as ``bft_time_ms``. Receipts are append-only in process; durable storage and
external-resource reconciliation remain follow-ups.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any, Final

from agency.consensus.grant_guard import enforce_grant
from agency.consensus.grants import ExecutionGrant, GrantLedger, GrantVerification
from agency.peers.canonical import (
    SchemaError,
    canonical_json,
    digest_hex,
    validate_string,
    validate_u64,
)

__all__ = [
    "FencedActuator",
    "FencingAttempt",
    "FencingInput",
    "FencingRejection",
    "FinalityReceipt",
    "fencing_from_payload",
    "fencing_from_state_token",
]

_FINALITY_DOMAIN: Final = "actuator-finality-v1"


class FencingRejection(str, Enum):
    """Closed set of deterministic fencing-layer failures."""

    STALE_TOKEN = "stale_token"
    CONFLICTING_OUTCOME = "conflicting_outcome"
    CONFLICTING_RESOURCE = "conflicting_resource"


@dataclass(frozen=True, slots=True)
class FencingInput:
    """Resource-scoped fencing values passed into one actuator call."""

    expected_current: int
    next_token: int

    def __post_init__(self) -> None:
        validate_u64(self.expected_current, "expected_current")
        validate_u64(self.next_token, "next_token")
        if self.next_token != self.expected_current + 1:
            raise SchemaError("next_token must be expected_current + 1")


def fencing_from_payload(payload: Mapping[str, Any]) -> FencingInput:
    """Read fencing values from a validated grant payload when one is available."""

    return FencingInput(
        expected_current=validate_u64(payload.get("expected_current"), "expected_current"),
        next_token=validate_u64(payload.get("next_token"), "next_token"),
    )


def fencing_from_state_token(state_token: int) -> FencingInput:
    """Derive fencing values from an explicit caller-supplied resource state token."""

    current = validate_u64(state_token, "state_token")
    return FencingInput(expected_current=current, next_token=current + 1)


@dataclass(frozen=True, slots=True)
class FinalityReceipt:
    """Append-only outcome record binding a grant digest to one fenced effect."""

    grant_digest: str
    resource_id: str
    fencing_token: int
    outcome: str

    def __post_init__(self) -> None:
        validate_string(self.grant_digest, "grant_digest", maximum=128)
        validate_string(self.resource_id, "resource_id", maximum=128)
        validate_u64(self.fencing_token, "fencing_token")
        validate_string(self.outcome, "outcome", maximum=4096)

    def body(self) -> dict[str, Any]:
        """Canonical projection; excludes no field, including the effect outcome."""

        return {
            "domain": _FINALITY_DOMAIN,
            "grant_digest": self.grant_digest,
            "resource_id": self.resource_id,
            "fencing_token": self.fencing_token,
            "outcome": self.outcome,
        }

    def canonical(self) -> bytes:
        return canonical_json(self.body())

    def digest(self) -> str:
        return digest_hex(self.canonical())


@dataclass(frozen=True, slots=True)
class FencingAttempt:
    """What the wrapper did with one grant, including verification and rejection."""

    executed: bool
    verification: GrantVerification | None
    receipt: FinalityReceipt | None
    rejection: FencingRejection | None

    @property
    def accepted(self) -> bool:
        return self.rejection is None and (self.verification is None or self.verification.ok)

    @property
    def outcome(self) -> str | None:
        return self.receipt.outcome if self.receipt is not None else None


class FencedActuator:
    """Verify, fence and execute a grant at most once per digest."""

    def __init__(self, ledger: GrantLedger, *, actuator: Callable[[ExecutionGrant], str]) -> None:
        self._ledger = ledger
        self._actuator = actuator
        self._tokens: dict[str, int] = {}
        self._outcomes: dict[str, FinalityReceipt] = {}
        self._receipts: list[FinalityReceipt] = []

    @property
    def receipts(self) -> tuple[FinalityReceipt, ...]:
        """Append-only receipt log in execution order."""

        return tuple(self._receipts)

    def receipt(self, grant_digest: str) -> FinalityReceipt | None:
        return self._outcomes.get(grant_digest)

    def token(self, resource_id: str) -> int:
        return self._tokens.get(resource_id, 0)

    def execute(
        self,
        grant: ExecutionGrant,
        *,
        bft_time_ms: int,
        resource_id: str,
        fencing: FencingInput,
        expected_outcome: str | None = None,
    ) -> FencingAttempt:
        """Verify, replay or execute ``grant`` against one fenced resource."""

        validate_string(resource_id, "resource_id", maximum=128)
        digest = grant.digest
        existing = self._outcomes.get(digest)
        if existing is not None:
            if expected_outcome is not None and expected_outcome != existing.outcome:
                return FencingAttempt(False, None, existing, FencingRejection.CONFLICTING_OUTCOME)
            if existing.resource_id != resource_id:
                return FencingAttempt(False, None, existing, FencingRejection.CONFLICTING_RESOURCE)
            return FencingAttempt(False, None, existing, None)

        verification = enforce_grant(grant, ledger=self._ledger, bft_time_ms=bft_time_ms)
        if not verification.ok:
            return FencingAttempt(False, verification, None, None)

        if resource_id not in self._tokens:
            self._tokens[resource_id] = fencing.expected_current
        if self._tokens[resource_id] != fencing.expected_current:
            return FencingAttempt(False, verification, None, FencingRejection.STALE_TOKEN)

        outcome = self._actuator(grant)
        receipt = FinalityReceipt(digest, resource_id, fencing.next_token, outcome)
        self._tokens[resource_id] = fencing.next_token
        self._outcomes[digest] = receipt
        self._receipts.append(receipt)
        return FencingAttempt(True, verification, receipt, None)
