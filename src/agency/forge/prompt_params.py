"""Forge tool availability as factory prompt parameters.

The factory composes prompts from inert text; host tool availability is
runtime state. This module bridges the two: it reports what the Forge catalog
finds on this host and returns values for the ``reap-tool-availability``
segment slots. Only names are reported — never installs, downloads, or runs
anything.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from agency.forge.tool_catalog import ToolSpec, is_available, list_tools

AVAILABILITY_PROMPT_ID = "reap-tool-availability"
REA_TOOL_PARAM_NAMES = ("REA_READY", "RE_TOOLS_AVAILABLE", "RE_TOOLS_MISSING")


def rea_prompt_params(
    *,
    tools: Iterable[ToolSpec] | None = None,
    is_tool_available: Callable[[ToolSpec], bool] | None = None,
) -> dict[str, str]:
    """Return availability slot values for one host snapshot.

    ``tools`` and ``is_tool_available`` are injectable for deterministic tests;
    defaults read the real catalog and probe ``PATH``. Bridge-only catalog
    entries (``command is None``) are excluded because they cannot be probed.
    """
    specs = list(list_tools() if tools is None else tools)
    check = is_available if is_tool_available is None else is_tool_available
    available = [spec.name for spec in specs if spec.command is not None and check(spec)]
    missing = [spec.name for spec in specs if spec.command is not None and not check(spec)]
    return {
        "REA_READY": "yes" if "rea" in available else "no",
        "RE_TOOLS_AVAILABLE": ", ".join(available) or "none",
        "RE_TOOLS_MISSING": ", ".join(missing) or "none",
    }


def forge_tool_params(
    blueprint: Any,
    *,
    is_tool_available: Callable[[ToolSpec], bool] | None = None,
) -> dict[str, str]:
    """Factory ``prompt_param_provider``: fill availability only when asked.

    Returns an empty mapping unless the blueprint pins
    :data:`AVAILABILITY_PROMPT_ID`, so prompts without those slots never see
    unused parameters (composition fails closed on unused params).
    """
    refs = getattr(blueprint, "prompt_refs", ())
    if not any(str(prompt_id) == AVAILABILITY_PROMPT_ID for prompt_id, _ in refs):
        return {}
    return rea_prompt_params(is_tool_available=is_tool_available)


__all__ = [
    "AVAILABILITY_PROMPT_ID",
    "REA_TOOL_PARAM_NAMES",
    "forge_tool_params",
    "rea_prompt_params",
]
