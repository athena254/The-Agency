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
   tests}) for names no other source knows → ``BUILDABLE``. The actual
   build job is dispatched by :func:`build_missing` /
   :func:`provision_and_build` (Phase 5), never by :func:`resolve`.
4. nothing knows the name → ``UNKNOWN`` (fail closed, never dropped).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

from agency.forge.tool_catalog import ToolSpec, get_tool, is_available

if TYPE_CHECKING:
    from agency.autonomy.forge_auto import ToolBuildResult

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


def _spec_fields(spec: Any) -> tuple[str, str]:
    """Read ``(code, tests)`` from a mapping- or attribute-shaped build spec."""
    if isinstance(spec, Mapping):
        return spec["code"], spec["tests"]
    return spec.code, spec.tests


def build_missing(
    report: ToolProvisionReport,
    *,
    build_specs: Mapping[str, Any],
    registry: Any,
    build_tool_fn: Callable[[str, str, str, Any], ToolBuildResult] | None = None,
) -> dict[str, ToolBuildResult]:
    """Attempt Forge builds for exactly the BUILDABLE items of ``report``.

    For each item with status ``BUILDABLE``, call
    ``build_tool_fn(name, code, tests, registry)`` — by default the real
    :func:`agency.autonomy.forge_auto.build_tool` (inspection, security scan
    and pytest gates; registration only on PASS + digest match). Items with
    any other status are never built; a BUILDABLE name without a spec is
    skipped honestly (no build attempted, nothing registered). Results are
    returned as ``{name: ToolBuildResult}`` and are the build function's own
    honest outcomes: a FAILED gate stays FAILED and nothing gets registered.
    ``build_tool_fn`` is injectable so tests stay hermetic.
    """
    if build_tool_fn is None:
        # Lazy import mirrors BlacksmithOps.build_tool_job: keeps
        # agency.tools ↔ agency.autonomy free of module-level cycles.
        from agency.autonomy.forge_auto import build_tool

        build_tool_fn = build_tool
    results: dict[str, ToolBuildResult] = {}
    for item in report.items:
        if item.status != BUILDABLE:
            continue
        spec = build_specs.get(item.tool)
        if spec is None:
            continue
        code, tests = _spec_fields(spec)
        results[item.tool] = build_tool_fn(item.tool, code, tests, registry)
    return results


def provision_and_build(
    requirements: Iterable[str],
    *,
    registry: Any,
    build_specs: Mapping[str, Mapping[str, str]],
    build_tool_fn: Callable[[str, str, str, Any], ToolBuildResult] | None = None,
) -> ToolProvisionReport:
    """Resolve, build the BUILDABLE gaps, then re-resolve post-build reality.

    Convenience over :func:`resolve` + :func:`build_missing`: the first
    resolve classifies the requirements; when anything is ``BUILDABLE`` the
    real build runs (honoring the same honesty contract); a second resolve
    reflects the registry after the builds — tools that registered become
    ``PROVISIONED``, failed builds stay ``BUILDABLE`` with the gate failure
    folded into their detail. Purely additive to the caller's registry:
    nothing is unregistered, denied, or invented here.
    """
    names = _normalize_requirements(requirements)
    initial = resolve(names, registry=registry, build_specs=build_specs)
    builds: dict[str, ToolBuildResult] = {}
    if any(item.status == BUILDABLE for item in initial.items):
        builds = build_missing(
            initial, build_specs=build_specs, registry=registry, build_tool_fn=build_tool_fn
        )
    final = resolve(names, registry=registry, build_specs=build_specs)
    if not builds:
        return final
    items: list[ToolProvisionItem] = []
    for item in final.items:
        result = builds.get(item.tool)
        if item.status == BUILDABLE and result is not None and not result.registered:
            item = item.model_copy(
                update={"detail": f"{item.detail}; build gate {result.gate}: {result.detail}"}
            )
        items.append(item)
    return final.model_copy(update={"items": tuple(items)})


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
    "build_missing",
    "provision_and_build",
    "resolve",
]
