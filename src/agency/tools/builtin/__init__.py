"""Concrete builtin tools for the Agency tool layer."""

from __future__ import annotations

from typing import Any

import httpx

from agency.tools.base import Tool
from agency.tools.builtin.calculator import CalculatorTool
from agency.tools.builtin.memory import MemoryQueryTool, MemoryWriteTool
from agency.tools.builtin.sandbox import SandboxExecTool
from agency.tools.builtin.utc_time import UtcTimeTool
from agency.tools.builtin.web import WebFetchTool, WebSearchTool


def register_all(registry: Any, transport: httpx.AsyncBaseTransport | None = None) -> list[Tool]:
    """Register the five legacy builtins; new local tools require explicit opt-in.

    ``transport`` (e.g. ``httpx.MockTransport``) is passed to the web tools
    for testing; memory/sandbox tools need nothing at construction — they
    use ``ctx.memory_store`` / ``ctx.sandbox_manager`` at run time.
    """
    tools: list[Tool] = [
        WebSearchTool(transport=transport),
        WebFetchTool(transport=transport),
        MemoryQueryTool(),
        MemoryWriteTool(),
        SandboxExecTool(),
    ]
    for tool in tools:
        registry.register(tool)
    return tools


def register_opt_in_local_tools(registry: Any) -> list[Tool]:
    """Explicitly add safe local tools to a separately constructed nonbeta registry."""
    tools: list[Tool] = [CalculatorTool(), UtcTimeTool()]
    for tool in tools:
        registry.register(tool)
    return tools


def register_beta_search_only(
    registry: Any, transport: httpx.AsyncBaseTransport | None = None
) -> list[Tool]:
    """Populate a beta-policy registry with only the approved search tool."""
    tools: list[Tool] = [WebSearchTool(transport=transport)]
    for tool in tools:
        registry.register(tool)
    return tools


__all__ = [
    "CalculatorTool",
    "MemoryQueryTool",
    "MemoryWriteTool",
    "SandboxExecTool",
    "UtcTimeTool",
    "WebFetchTool",
    "WebSearchTool",
    "register_all",
    "register_beta_search_only",
    "register_opt_in_local_tools",
]
