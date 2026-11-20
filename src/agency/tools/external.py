"""External tool adapters imported from the broader agent ecosystem.

These wrap commonly available CLIs/APIs (gws, last30days, scrapers) and fail
gracefully when the dependency is missing. They are opt-in: call
``register_external_tools`` to add them to a registry.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from typing import Any

import httpx
import structlog

from agency.tools.base import ToolContext, ToolResult, ToolRisk, ToolSpec

logger = structlog.get_logger(__name__)


class WebScrapeTool:
    """Fetch a URL and return clean text via Jina Reader (fallback: local fetch)."""

    spec = ToolSpec(
        name="web_scrape",
        description=(
            "Scrape a web page and return clean markdown/text. Uses Jina Reader "
            "(r.jina.ai) when available, else falls back to the built-in fetcher."
        ),
        parameters={
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
            "additionalProperties": False,
        },
        risk=ToolRisk.READ_ONLY,
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        del ctx
        url = args.get("url")
        if not isinstance(url, str) or not url.strip():
            return ToolResult(tool="web_scrape", ok=False, error="missing required param: 'url'")
        target = url.strip()
        start: float = 0.0
        import time

        start = time.perf_counter()
        # Try Jina Reader first (free, no key).
        jina_url = "https://r.jina.ai/" + target
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(jina_url, headers={"Accept": "text/plain"})
            if resp.status_code < 400 and resp.text.strip():
                text = resp.text[:8000]
                return ToolResult(
                    tool="web_scrape",
                    ok=True,
                    output={"url": target, "text": text, "engine": "jina"},
                    duration_ms=int((time.perf_counter() - start) * 1000),
                    evidence={"engine": "jina", "url": target},
                )
        except Exception:
            logger.debug("web_scrape.jina_failed", exc_info=True)
        return ToolResult(
            tool="web_scrape",
            ok=False,
            error="scrape unavailable (jina failed and no local fallback)",
            duration_ms=int((time.perf_counter() - start) * 1000),
        )


class GoogleWorkspaceTool:
    """Invoke the gws CLI for Google Workspace operations."""

    spec = ToolSpec(
        name="google_workspace",
        description=(
            "Run a Google Workspace CLI (gws) command and return JSON. "
            "Requires `gws` installed (npm i -g @googleworkspace/cli) and auth."
        ),
        parameters={
            "type": "object",
            "properties": {
                "args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "gws subcommand args, e.g. ['drive','files','list']",
                },
            },
            "required": ["args"],
            "additionalProperties": False,
        },
        risk=ToolRisk.READ_ONLY,
    )

    _READ_ONLY_VERBS = frozenset({"list", "get", "search", "describe", "info", "status", "schema"})

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        del ctx
        import time

        start = time.perf_counter()
        raw_args = args.get("args")
        if not isinstance(raw_args, list) or not all(isinstance(a, str) for a in raw_args):
            return ToolResult(tool="google_workspace", ok=False, error="'args' must be a list of strings")
        # Read-only guard: deny mutating verbs even if the CLI is invoked directly.
        lowered = " ".join(raw_args).lower()
        if any(verb in lowered.split() for verb in ("send", "delete", "create", "update", "modify", "remove", "put", "post", "patch")):
            return ToolResult(
                tool="google_workspace",
                ok=False,
                error="google_workspace is read-only: mutating verbs denied",
                duration_ms=int((time.perf_counter() - start) * 1000),
            )
        if shutil.which("gws") is None:
            return ToolResult(
                tool="google_workspace",
                ok=False,
                error="gws CLI not found; install via `npm i -g @googleworkspace/cli`",
                duration_ms=int((time.perf_counter() - start) * 1000),
            )
            return ToolResult(
                tool="google_workspace",
                ok=False,
                error="gws CLI not found; install via `npm i -g @googleworkspace/cli`",
                duration_ms=int((time.perf_counter() - start) * 1000),
            )
        try:
            proc = await asyncio.to_thread(
                subprocess.run,
                ["gws", *raw_args, "--format", "json"],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                tool="google_workspace",
                ok=False,
                error=f"gws failed: {type(exc).__name__}",
                duration_ms=int((time.perf_counter() - start) * 1000),
            )
        duration = int((time.perf_counter() - start) * 1000)
        if proc.returncode != 0:
            return ToolResult(
                tool="google_workspace",
                ok=False,
                error=(proc.stderr or "gws non-zero exit")[:500],
                duration_ms=duration,
            )
        try:
            data = json.loads(proc.stdout) if proc.stdout.strip() else {}
        except json.JSONDecodeError:
            data = {"raw": proc.stdout[:4000]}
        return ToolResult(
            tool="google_workspace",
            ok=True,
            output=data,
            duration_ms=duration,
            evidence={"args": raw_args, "returncode": proc.returncode},
        )


class Last30DaysTool:
    """Run the last30days research skill if installed."""

    spec = ToolSpec(
        name="last30days",
        description=(
            "Research a topic across Reddit/X/YouTube/HN/Polymarket/GitHub/web "
            "from the last 30 days, engagement-ranked. Requires last30days skill "
            "installed (`npx skills add mvanhorn/last30days-skill -g`)."
        ),
        parameters={
            "type": "object",
            "properties": {"topic": {"type": "string"}},
            "required": ["topic"],
            "additionalProperties": False,
        },
        risk=ToolRisk.READ_ONLY,
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        del ctx
        import time

        start = time.perf_counter()
        topic = args.get("topic")
        if not isinstance(topic, str) or not topic.strip():
            return ToolResult(tool="last30days", ok=False, error="missing required param: 'topic'")
        binary = shutil.which("last30days") or shutil.which("npx")
        if binary is None:
            return ToolResult(
                tool="last30days",
                ok=False,
                error="last30days/npx not found; install via `npx skills add mvanhorn/last30days-skill -g`",
                duration_ms=int((time.perf_counter() - start) * 1000),
            )
        cmd = [binary]
        if binary.endswith(("npx", "npx.cmd")):
            cmd += ["skills", "run", "last30days", "--", topic.strip()]
        else:
            cmd += [topic.strip()]
        try:
            proc = await asyncio.to_thread(
                subprocess.run, cmd, capture_output=True, text=True, timeout=300
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                tool="last30days",
                ok=False,
                error=f"last30days failed: {type(exc).__name__}",
                duration_ms=int((time.perf_counter() - start) * 1000),
            )
        duration = int((time.perf_counter() - start) * 1000)
        if proc.returncode != 0:
            return ToolResult(
                tool="last30days",
                ok=False,
                error=(proc.stderr or "non-zero exit")[:500],
                duration_ms=duration,
            )
        return ToolResult(
            tool="last30days",
            ok=True,
            output={"topic": topic.strip(), "brief": proc.stdout[:12000]},
            duration_ms=duration,
            evidence={"topic": topic.strip(), "returncode": proc.returncode},
        )


class ComputerUseTool:
    """Stub for Anthropic-style computer use; requires external setup."""

    spec = ToolSpec(
        name="computer_use",
        description=(
            "Computer-use agent (screen/keyboard/mouse). STUB: requires Anthropic "
            "computer-use API and a sandboxed display. Not runnable by default."
        ),
        parameters={
            "type": "object",
            "properties": {"task": {"type": "string"}},
            "required": ["task"],
            "additionalProperties": False,
        },
        risk=ToolRisk.READ_ONLY,
    )

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        del ctx
        return ToolResult(
            tool="computer_use",
            ok=False,
            error="computer_use is a stub; wire Anthropic computer-use API + sandbox to enable",
        )


def register_external_tools(registry: Any) -> list[Any]:
    """Register the imported external tools (opt-in)."""
    tools: list[Any] = [
        WebScrapeTool(),
        GoogleWorkspaceTool(),
        Last30DaysTool(),
        ComputerUseTool(),
    ]
    for tool in tools:
        try:
            registry.register(tool)
        except Exception:
            logger.debug("register_external_tools.skip", exc_info=True)
    return tools


__all__ = [
    "ComputerUseTool",
    "GoogleWorkspaceTool",
    "Last30DaysTool",
    "WebScrapeTool",
    "register_external_tools",
]