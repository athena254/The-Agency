"""Core tool types for The Agency tool layer."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class ToolRisk(str, Enum):
    """Risk class for a tool; gates risk-engine sign-off."""

    READ_ONLY = "read_only"
    MUTATES_STATE = "mutates"
    EXECUTES_CODE = "executes"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class ToolSpec:
    """Static metadata describing a tool.

    Parameters
    ----------
    name:
        Snake-case identifier, e.g. ``"web_search"``.
    description:
        Human/LLM-readable description shown to the model.
    parameters:
        JSON Schema dict for the tool's args (default empty = any args).
    risk:
        Risk class used by the risk engine.
    """

    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)
    risk: ToolRisk = ToolRisk.READ_ONLY

    def __post_init__(self) -> None:
        if not self.name or not _NAME_RE.match(self.name):
            raise ValueError(f"invalid tool name {self.name!r}: must match ^[a-z][a-z0-9_]*$")


@dataclass
class ToolContext:
    """Per-call context injected by the executor; never global."""

    agent_id: str
    task_id: str
    memory_store: Any = None
    sandbox_manager: Any = None
    lattice: Any = None
    audit: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)


class ToolResult(BaseModel):
    """Serialized outcome of a single tool call."""

    model_config = ConfigDict(extra="forbid")

    tool: str
    ok: bool
    output: Any = None
    error: str | None = None
    duration_ms: int = 0
    evidence: dict[str, Any] = Field(default_factory=dict)


class Tool(Protocol):
    """Uniform interface every tool must implement."""

    spec: ToolSpec

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """Execute the tool with validated JSON args."""
        ...


class ToolError(Exception):
    """Raised by the registry on unknown tool, invalid args, or duplicates."""

    def __init__(self, message: str, tool: str | None = None) -> None:
        super().__init__(message)
        self.tool = tool


__all__ = [
    "Tool",
    "ToolContext",
    "ToolError",
    "ToolResult",
    "ToolRisk",
    "ToolSpec",
]
