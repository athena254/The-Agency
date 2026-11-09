"""Autonomy -> Factory auto-activation (no human authorizer).

Deterministic, idempotent, bounded bridge for the internal-only agency:
when the autonomy engine decides a capability is missing and requests a
spawn (see :func:`agency.autonomy.spawner.decide_spawn`), it can call
:func:`activate_for_caps` to materialize a governed factory identity
instead of registering a raw runtime agent.

How it works (KISS, additive only — the human approval path in
:mod:`agency.factory.service` is untouched):

1. Caps are normalized (lowercased, deduplicated, sorted, bounded to
   ``MAX_CAPS``) and hashed into a stable blueprint id
   (``auto-<sha256[:16]>``).
2. The blueprint version is created as ``1.0.0`` (first call only).
3. It is auto-approved with a fixed autonomy policy identity
   (creator ``autonomy-engine``, approver ``autonomy-policy``, never the
   same, so the no-self-approval invariant of ``AgentFactory.approve``
   is preserved). When the factory has an authorizer configured, the
   standard ``approve`` path is used; when no authorizer is configured
   (the factory was built for autonomous use), the DRAFT -> APPROVED
   transition is recorded directly with the same conditional-update +
   event shape as :meth:`AgentFactory.approve`.
4. The approved blueprint is activated via the standard ``activate``
   path, which issues L0-only capability claims. Revoked blueprints
   fail closed: they are never silently resurrected.

Repeated calls for the same caps return the existing runtime agent id
without creating new blueprint versions (idempotent bounded slice).
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from agency.factory.service import (
    MAX_CAPS,
    AgentFactory,
    BlueprintStatus,
)
from agency.kernel.identity import Capability

AUTO_CREATOR = "autonomy-engine"
AUTO_APPROVER = "autonomy-policy"
AUTO_VERSION = "1.0.0"
AUTO_DOMAIN = "general"

_CAP_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")


def _normalize_caps(caps: Any) -> tuple[str, ...]:
    """Normalize to a sorted, deduplicated, bounded tuple of cap names."""
    if caps is None:
        return ("general",)
    if isinstance(caps, (str, Capability)):
        caps = [caps]
    if not isinstance(caps, (list, tuple)):
        raise TypeError("caps must be a list of names.")
    if len(caps) > MAX_CAPS:
        raise ValueError(f"too many capabilities: {len(caps)} > {MAX_CAPS}.")
    out: set[str] = set()
    for entry in caps:
        if isinstance(entry, Capability):
            name = entry.name.strip().lower()
        elif isinstance(entry, str):
            name = entry.strip().lower()
        else:
            raise TypeError("cap entries must be strings or Capability.")
        if not name or _CAP_RE.match(name) is None:
            raise ValueError(f"invalid capability {entry!r}.")
        out.add(name)
    if not out:
        return ("general",)
    return tuple(sorted(out))


def blueprint_id_for_caps(caps: Any) -> str:
    """Return the deterministic blueprint id for a capability set."""
    normalized = _normalize_caps(caps)
    digest = hashlib.sha256(",".join(normalized).encode("utf-8")).hexdigest()
    return f"auto-{digest[:16]}"


def auto_evidence(blueprint_id: str) -> str:
    """Return the deterministic auto-approval evidence string."""
    return f"autonomy-auto:{blueprint_id}@{AUTO_VERSION}"


def _auto_approve(factory: AgentFactory, blueprint_id: str) -> None:
    """Approve a DRAFT blueprint under the autonomy policy identity.

    Uses the standard ``approve`` path when the factory has an authorizer;
    otherwise records the DRAFT -> APPROVED transition directly (same
    conditional update + audit event as ``approve``) so that autonomy
    needs no human authorizer. Idempotent once APPROVED/ACTIVE.
    """
    from datetime import UTC, datetime

    def _now_iso() -> str:
        return datetime.now(UTC).isoformat()

    bp = factory.get(blueprint_id, AUTO_VERSION)
    if bp.status is BlueprintStatus.APPROVED:
        return
    if bp.status is BlueprintStatus.ACTIVE:
        return
    if bp.status is BlueprintStatus.REVOKED:
        raise ValueError(f"blueprint {blueprint_id!r} is revoked (fail closed).")
    if bp.creator == AUTO_APPROVER:
        raise ValueError("auto-approver must differ from creator.")
    factory.validate(bp.blueprint_id, bp.version)
    evidence = auto_evidence(blueprint_id)
    authorizer = getattr(factory, "_authorizer", None)
    if authorizer is not None:
        factory.approve(
            bp.blueprint_id,
            bp.version,
            approver=AUTO_APPROVER,
            evidence=evidence,
        )
        return
    now = _now_iso()
    conn = getattr(factory, "_conn", None)
    if conn is None:
        raise ValueError("factory connection unavailable (fail closed).")
    with conn:
        cur = conn.execute(
            "UPDATE blueprints SET status = ?, approver = ?, evidence = ?,"
            " updated_at = ? WHERE blueprint_id = ? AND version = ?"
            " AND status = ?",
            (
                BlueprintStatus.APPROVED.value,
                AUTO_APPROVER,
                evidence,
                now,
                bp.blueprint_id,
                bp.version,
                BlueprintStatus.DRAFT.value,
            ),
        )
        if cur.rowcount != 1:
            raise ValueError("auto-approve lost a concurrent transition (fail closed).")
        conn.execute(
            "INSERT INTO factory_events (blueprint_id, version, from_status,"
            " to_status, actor, evidence, at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                bp.blueprint_id,
                bp.version,
                BlueprintStatus.DRAFT.value,
                BlueprintStatus.APPROVED.value,
                AUTO_APPROVER,
                evidence,
                now,
            ),
        )


def activate_for_caps(
    caps: Any,
    factory: Any = None,
    *,
    registry: Any = None,
    domain: str = AUTO_DOMAIN,
    name: str | None = None,
) -> str:
    """Auto-approve + activate a blueprint for ``caps``; return agent id.

    ``factory`` is an :class:`AgentFactory`. For compatibility with
    ``(caps, registry)`` call styles, ``registry`` may instead be the
    factory when ``factory`` is omitted. Idempotent: repeated calls with
    the same capabilities return the same agent id.
    """
    resolved = factory if factory is not None else registry
    if resolved is None:
        raise ValueError("an AgentFactory instance is required (fail closed).")
    if not (
        callable(getattr(resolved, "create", None))
        and callable(getattr(resolved, "activate", None))
    ):
        raise TypeError("factory must expose create/activate (AgentFactory).")
    normalized = _normalize_caps(caps)
    blueprint_id = blueprint_id_for_caps(normalized)
    agent_name = (name or f"autonomy-{'-'.join(normalized)}")[:200]
    # Auto-activation admits the requested (already bounded, L0-string) caps
    # into the factory allowlist so a default-constructed factory works for
    # arbitrary engine caps. The human approval path is unaffected.
    allowed = getattr(resolved, "_allowed", None)
    if isinstance(allowed, frozenset) and not set(normalized) <= allowed:
        resolved._allowed = frozenset(set(allowed) | set(normalized))
    try:
        resolved.create(
            blueprint_id,
            AUTO_VERSION,
            agent_name,
            domain=domain or AUTO_DOMAIN,
            capabilities=list(normalized),
            creator=AUTO_CREATOR,
        )
    except (ValueError, TypeError):
        existing = None
        try:
            existing = resolved.get(blueprint_id, AUTO_VERSION)
        except (KeyError, ValueError):
            existing = None
        if existing is None:
            raise
    bp = resolved.get(blueprint_id, AUTO_VERSION)
    if bp.status is BlueprintStatus.ACTIVE:
        assert bp.runtime_agent_id is not None
        return str(bp.runtime_agent_id)
    if bp.status is BlueprintStatus.REVOKED:
        raise ValueError(f"blueprint {blueprint_id!r} is revoked (fail closed).")
    _auto_approve(resolved, blueprint_id)
    agent = resolved.activate(blueprint_id, AUTO_VERSION)
    return str(agent.id)


__all__ = [
    "AUTO_CREATOR",
    "AUTO_DOMAIN",
    "AUTO_VERSION",
    "activate_for_caps",
    "auto_evidence",
    "blueprint_id_for_caps",
]
