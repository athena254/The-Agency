"""Core tool types for The Agency tool layer."""

from __future__ import annotations

import re
from collections.abc import Iterable
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


@dataclass(frozen=True)
class BetaPrincipal:
    """Trusted beta identity bound by the integration layer.

    Must be constructed by trusted code (orchestrator/Telegram admission)
    from a validated positive Telegram ``from.id`` allowlist entry in a
    private chat. Never derive this from LLM output, tool args, or
    ``ctx.metadata`` strings: metadata alone never grants beta access and
    ``BetaToolPolicy`` ignores it.
    """

    telegram_user_id: int
    private_chat: bool = True

    def __post_init__(self) -> None:
        uid = self.telegram_user_id
        if isinstance(uid, bool) or not isinstance(uid, int) or uid <= 0:
            raise ValueError(f"invalid telegram_user_id {uid!r}: must be a positive int")


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
    beta_principal: BetaPrincipal | None = None


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


@dataclass(frozen=True)
class ToolPolicyDecision:
    """Small typed policy verdict; ``allowed=False`` must fail closed."""

    allowed: bool
    reason: str = ""


class ToolPolicy(Protocol):
    """Per-call preflight hook checked after validation, before ``tool.run``."""

    def __call__(
        self, spec: ToolSpec, args: dict[str, Any], ctx: ToolContext
    ) -> bool | ToolPolicyDecision:
        """Return True/allowed to proceed, False/denied to refuse."""
        ...


class BetaToolPolicy:
    """Explicit fail-closed beta allowlist (B-04; B-05 keeps web_fetch off).

    Allows only pre-reviewed harmless tool names when ``ctx.beta_principal``
    carries trusted Telegram identity in a private chat. Denies absent or
    invalid identity, unclassified names, any ``MUTATES_STATE`` /
    ``EXECUTES_CODE`` risk, memory tools (``memory_query`` can search all
    owners, ``memory_write`` binds ``ctx.agent_id``), and ``web_fetch``
    until B-05 is independently closed. ``web_search`` is permitted only
    with a bounded query and result count.

    Orchestrator seam (not wired in this slice): the trusted Telegram
    integration must build ``ToolContext(beta_principal=BetaPrincipal(...))``
    from validated admission state. Untrusted ``ctx.metadata`` strings alone
    never grant access.
    """

    _MEMORY_TOOLS = frozenset({"memory_query", "memory_write"})

    def __init__(
        self,
        allowed_tools: Iterable[str] | None = None,
        max_query_chars: int = 200,
        max_results: int = 5,
    ) -> None:
        self._allowed = (
            frozenset(allowed_tools) if allowed_tools is not None else frozenset({"web_search"})
        )
        self._max_query_chars = max_query_chars
        self._max_results = max_results

    def __call__(
        self, spec: ToolSpec, args: dict[str, Any], ctx: ToolContext
    ) -> ToolPolicyDecision:
        principal = ctx.beta_principal
        if principal is None:
            return ToolPolicyDecision(False, "beta: missing trusted identity")
        uid = principal.telegram_user_id
        if isinstance(uid, bool) or not isinstance(uid, int) or uid <= 0:
            return ToolPolicyDecision(False, "beta: missing trusted identity")
        if not principal.private_chat:
            return ToolPolicyDecision(False, "beta: group/channel scope denied")
        if spec.name in self._MEMORY_TOOLS:
            return ToolPolicyDecision(False, f"beta: memory tools disabled in beta: {spec.name}")
        if spec.name == "web_fetch":
            return ToolPolicyDecision(False, "beta: web_fetch disabled until B-05 closed")
        if spec.risk is not ToolRisk.READ_ONLY:
            return ToolPolicyDecision(
                False, f"beta: risk {spec.risk.value} denied in beta: {spec.name}"
            )
        if spec.name not in self._allowed:
            return ToolPolicyDecision(False, f"beta: tool {spec.name!r} not allowlisted in beta")
        if spec.name == "web_search":
            query = args.get("query")
            if not isinstance(query, str) or not query.strip():
                return ToolPolicyDecision(False, "beta: web_search query required")
            if len(query.strip()) > self._max_query_chars:
                return ToolPolicyDecision(False, "beta: web_search query too long")
            if "max_results" in args:
                mr = args.get("max_results")
                if (
                    not isinstance(mr, int)
                    or isinstance(mr, bool)
                    or mr < 1
                    or mr > self._max_results
                ):
                    return ToolPolicyDecision(False, "beta: web_search max_results out of bounds")
        return ToolPolicyDecision(True, f"beta: allowed {spec.name}")


__all__ = [
    "BetaPrincipal",
    "BetaToolPolicy",
    "Tool",
    "ToolContext",
    "ToolError",
    "ToolPolicy",
    "ToolPolicyDecision",
    "ToolResult",
    "ToolRisk",
    "ToolSpec",
]
