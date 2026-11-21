"""Agency tool-availability slots as factory prompt parameters.

Generalizes ``agency/forge/prompt_params.py``: a
:class:`~agency.tools.provision.ToolProvisionReport` becomes the slot values
of the ``agency-tool-availability`` segment. Only names are reported — the
provisioner never installs, builds, or registers anything, and neither does
this module.

Slot mapping (kept honest):

- ``TOOLS_PROVISIONED`` — PROVISIONED and CATALOG_AVAILABLE (usable now).
- ``TOOLS_MISSING_INSTALL`` — NEEDS_INSTALL and MCP_BRIDGE_UNCONFIGURED
  (missing; an install/setup hint exists).
- ``TOOLS_MISSING_BUILD`` — BUILDABLE and UNKNOWN (missing with no install
  hint to advertise; closing them means manufacturing the tool, which is the
  Forge's software path — wired in a later phase).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from agency.tools.provision import (
    BUILDABLE,
    CATALOG_AVAILABLE,
    MCP_BRIDGE_UNCONFIGURED,
    NEEDS_INSTALL,
    PROVISIONED,
    UNKNOWN,
    ToolProvisionReport,
    resolve,
)

AVAILABILITY_PROMPT_ID = "agency-tool-availability"
PROVISIONED_SLOT = "TOOLS_PROVISIONED"
MISSING_INSTALL_SLOT = "TOOLS_MISSING_INSTALL"
MISSING_BUILD_SLOT = "TOOLS_MISSING_BUILD"

_PROVISIONED_STATUSES = frozenset({PROVISIONED, CATALOG_AVAILABLE})
_MISSING_INSTALL_STATUSES = frozenset({NEEDS_INSTALL, MCP_BRIDGE_UNCONFIGURED})
_MISSING_BUILD_STATUSES = frozenset({BUILDABLE, UNKNOWN})


def _names(report: ToolProvisionReport, statuses: frozenset[str]) -> str:
    return ", ".join(item.tool for item in report.items if item.status in statuses) or "none"


def tool_availability_params(report: ToolProvisionReport) -> dict[str, str]:
    """Return the segment's three slot values from a provision report.

    Every slot is always present: empty buckets render as ``"none"`` so a
    pinned segment never composes with unfilled slots (composition fails
    closed on unfilled slots).
    """
    return {
        PROVISIONED_SLOT: _names(report, _PROVISIONED_STATUSES),
        MISSING_INSTALL_SLOT: _names(report, _MISSING_INSTALL_STATUSES),
        MISSING_BUILD_SLOT: _names(report, _MISSING_BUILD_STATUSES),
    }


def tool_param_provider_for(
    requirements: Iterable[str],
) -> Callable[[Any], dict[str, str]]:
    """Factory ``prompt_param_provider`` bound to a fixed requirements tuple.

    The returned callable resolves the requirements (fresh host facts, at
    activation time) and fills the availability slots — but only when the
    blueprint pins :data:`AVAILABILITY_PROMPT_ID`; otherwise it returns an
    empty mapping so prompts without those slots never see unused parameters
    (composition fails closed on unused params).
    """

    def provider(blueprint: Any) -> dict[str, str]:
        refs = getattr(blueprint, "prompt_refs", ())
        if not any(str(prompt_id) == AVAILABILITY_PROMPT_ID for prompt_id, _ in refs):
            return {}
        report = resolve(requirements)
        return tool_availability_params(report)

    return provider


__all__ = [
    "AVAILABILITY_PROMPT_ID",
    "MISSING_BUILD_SLOT",
    "MISSING_INSTALL_SLOT",
    "PROVISIONED_SLOT",
    "tool_availability_params",
    "tool_param_provider_for",
]
