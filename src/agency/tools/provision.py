"""ToolProvisioner: declared tool requirements -> honest provision statuses.

Phase 2 of the tool-provisioning plan (PLAN_TOOL_PROVISIONING §3.3). The
provisioner is a *reporter only*: per required tool name it emits exactly one
status backed by evidence. It never installs anything, never builds anything,
and never mutates a registry — dispatch decisions stay with the Blacksmith
and the human.

Sources, in precedence order per name:

1. ``registry`` — anything exposing ``list_specs()`` (registered builtin or
   imported tools) → ``PROVISIONED``.
2. the Forge tool catalog (:mod:`agency.forge.tool_catalog`) →
   ``CATALOG_AVAILABLE`` when the host command is present, ``NEEDS_INSTALL``
   with the install hint when missing, and ``MCP_BRIDGE_UNCONFIGURED`` for
   catalog entries without a command (MCP bridges; bridging lands later).
3. ``build_specs`` — caller-supplied Forge build specs (name -> {code,
   tests}) for names no other source knows → ``BUILDABLE`` (Phase 5 wires
   the actual build job).
4. nothing knows the name → ``UNKNOWN`` (fail closed, never dropped).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from agency.forge.tool_catalog import ToolSpec, get_tool, is_available

MAX_REQUIREMENTS = 64

PROVISIONED = "PROVISIONED"
CATALOG_AVAILABLE = "CATALOG_AVAILABLE"
NEEDS_INSTALL = "NEEDS_INSTALL"
MCP_BRIDGE_UNCONFIGURED = "MCP_BRIDGE_UNCONFIGURED"
BUILDABLE = "BUILDABLE"
UNKNOWN = "UNKNOWN"

_UNKNOWN_DETAIL = "no registered, catalog, or build-spec source knows this tool"
_MCP_DETAIL = "MCP server entry; bridging lands in Phase 4b"
_BUILDABLE_DETAIL = "forge build spec available"


class ToolProvisionItem(BaseModel):
    """One required tool's resolved status and evidence."""

    model_config = ConfigDict(frozen=True)

    tool: str
    status: str
    detail: str


class ToolProvisionReport(BaseModel):
    """Immutable per-activation provisioning report."""

    model_config = ConfigDict(frozen=True)

    items: tuple[ToolProvisionItem, ...]
    report_id: str
    created_at: str


def _normalize_requirements(requirements: Iterable[str]) -> tuple[str, ...]:
    """Dedupe order-preserving; fail closed on non-string/blank entries."""
    if isinstance(requirements, str):
        raise TypeError("requirements must be a sequence of tool names.")
    try:
        entries = list(requirements)
    except TypeError as exc:
        raise TypeError("requirements must be a sequence of tool names.") from exc
    if len(entries) > MAX_REQUIREMENTS:
        raise ValueError(f"too many tool requirements: {len(entries)} > {MAX_REQUIREMENTS}.")
    out: list[str] = []
    for entry in entries:
        if not isinstance(entry, str):
            raise TypeError("tool requirement entries must be strings.")
        name = entry.strip()
        if not name:
            raise ValueError("tool requirement entries must be nonblank strings.")
        if name not in out:
            out.append(name)
    return tuple(out)


def _registry_names(registry: Any) -> frozenset[str]:
    specs = registry.list_specs()
    return frozenset(str(spec.name) for spec in specs)


def _catalog_item(
    name: str,
    probe: Callable[[ToolSpec], bool],
    /,
) -> ToolProvisionItem | None:
    """Resolve a name against the Forge catalog; None when unknown there."""
    spec: ToolSpec | None = get_tool(name)
    if spec is None:
        return None
    if spec.command is None:
        return ToolProvisionItem(tool=name, status=MCP_BRIDGE_UNCONFIGURED, detail=_MCP_DETAIL)
    if probe(spec):
        return ToolProvisionItem(
            tool=name,
            status=CATALOG_AVAILABLE,
            detail=f"host command {spec.command!r} present (source: tool_catalog)",
        )
    return ToolProvisionItem(tool=name, status=NEEDS_INSTALL, detail=spec.install)


def resolve(
    requirements: Iterable[str],
    *,
    registry: Any = None,
    catalog_probe: Any = None,
    build_specs: Mapping[str, Mapping[str, str]] | None = None,
) -> ToolProvisionReport:
    """Resolve required tool names into one honest status each.

    ``registry`` is a :class:`~agency.tools.registry.ToolRegistry` or any
    object exposing ``list_specs()``. ``catalog_probe`` is an injectable
    ``(ToolSpec) -> bool`` (defaults to the real :func:`is_available` PATH
    probe) for deterministic tests. ``build_specs`` maps a name to a Forge
    build spec ({code, tests}); it only applies to names no registered or
    catalog source knows. Purely read-only: nothing is installed, built, or
    registered here.
    """
    names = _normalize_requirements(requirements)
    probe = is_available if catalog_probe is None else catalog_probe
    specs = build_specs if build_specs is not None else {}
    registered = _registry_names(registry) if registry is not None else frozenset()
    items: list[ToolProvisionItem] = []
    for name in names:
        if name in registered:
            items.append(
                ToolProvisionItem(
                    tool=name,
                    status=PROVISIONED,
                    detail="registered tool available (source: registry)",
                )
            )
            continue
        catalog_item = _catalog_item(name, probe)
        if catalog_item is not None:
            items.append(catalog_item)
            continue
        if name in specs:
            items.append(ToolProvisionItem(tool=name, status=BUILDABLE, detail=_BUILDABLE_DETAIL))
            continue
        items.append(ToolProvisionItem(tool=name, status=UNKNOWN, detail=_UNKNOWN_DETAIL))
    return ToolProvisionReport(
        items=tuple(items),
        report_id=str(uuid.uuid4()),
        created_at=datetime.now(UTC).isoformat(),
    )


__all__ = [
    "BUILDABLE",
    "CATALOG_AVAILABLE",
    "MAX_REQUIREMENTS",
    "MCP_BRIDGE_UNCONFIGURED",
    "NEEDS_INSTALL",
    "PROVISIONED",
    "UNKNOWN",
    "ToolProvisionItem",
    "ToolProvisionReport",
    "resolve",
]
