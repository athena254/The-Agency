"""Concrete builtin tools for the Agency tool layer."""

from __future__ import annotations

from typing import Any

import httpx

from agency.tools.base import Tool
from agency.tools.builtin.memory import MemoryQueryTool, MemoryWriteTool
from agency.tools.builtin.sandbox import SandboxExecTool
from agency.tools.builtin.web import WebFetchTool, WebSearchTool


def register_all(registry: Any, transport: httpx.BaseTransport | None = None) -> list[Tool]:
    """Instantiate the 5 builtin tools and register them.

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


__all__ = [
    "MemoryQueryTool",
    "MemoryWriteTool",
    "SandboxExecTool",
    "WebFetchTool",
    "WebSearchTool",
    "register_all",
]
