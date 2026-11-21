"""Minimal MCP stdio bridge: MCP servers become Agency tools (Phase 4b).

Implements PLAN_TOOL_PROVISIONING §8.1 (owner decision: BOTH plugin formats).
One MCP server configured as ``{name, command, args, env, ...`` becomes a
family of ordinary registered Agency tools named ``"<server>__<tool>"`` and
enforced by exactly the same surfaces as builtin tools (registry schema
validation, per-agent ``allowed_tools`` allowlist, policies, audit).

Trust model (same security posture as the tool-import path):

- The server subprocess is untrusted code the operator explicitly chose.
  Spawned from ``command + args`` with ``config.env`` merged over the ambient
  environment; nothing else is ever executed in-process.
- **Descriptions are DATA** (anti tool-poisoning, plan rule 4): server
  descriptions are never logged at any level, never echoed into errors, and
  are only carried in the quoted ``ToolSpec.description`` field.
  :class:`McpDiscoveredTool` pins ``description_sha256`` so callers can pin
  and re-verify the text across sessions (a changed hash = re-vet).
- Fail closed: timeouts, bad handshakes, malformed responses, oversized
  listings, and unserializable arguments all raise :class:`McpBridgeError`
  (or ``ValueError`` for registry-unsafe names); a killed/failed session
  never yields a successful result.
- Env policy: the subprocess environment is the ambient ``os.environ`` with
  secret-looking variable names stripped (patterns aligned with
  ``agency/forge/security_scan.py``), overlaid with ``config.env``. Ambient
  ``.env``-style secrets are NOT auto-forwarded; only explicitly configured
  keys reach the server.

KISS v1 choices, deliberately: a FRESH subprocess session per tool call
(spawn -> handshake -> tools/call -> kill; no persistent connections), stdio
only, no auth/OAuth, and server->client requests (sampling/roots) are not
answered. Deferred until a real need appears.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from agency.tools.base import ToolContext, ToolResult, ToolRisk, ToolSpec
from agency.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from collections.abc import Iterator

PROTOCOL_VERSION = "2025-06-18"
CLIENT_NAME = "the-agency"
CLIENT_VERSION = "0.1.0"

DEFAULT_TIMEOUT_S = 30.0
KILL_GRACE_TIMEOUT_S = 15.0
MAX_CONFIG_BYTES = 64 * 1024
MAX_SERVER_NAME_CHARS = 64
MAX_TOOL_NAME_CHARS = 128
MAX_DESCRIPTION_CHARS = 2048
MAX_DISCOVERED_TOOLS = 64
MAX_COMMAND_CHARS = 2048

SERVER_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
# Namespaced names become ToolSpec names, so they must satisfy the stricter
# registry regex (same pattern as agency/tools/base.py and importing.py).
_REGISTRY_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_DOMAIN_RE = re.compile(r"^[a-z0-9*][a-z0-9*.-]*$")
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Ambient variables whose name looks like a credential are never inherited by
# the server subprocess (aligned with forge/security_scan.py SEC100 patterns).
_SECRET_ENV_NAME_RE = re.compile(
    r"(?i)(?:^|[_-])(?:api_?key|access_?key|secret|token|password|passwd|credential|"
    r"private_?key|auth)(?:[_-]|$)"
)


class McpBridgeError(Exception):
    """Fail-closed bridge failure: timeout, bad handshake, malformed response."""


class McpServerConfig(BaseModel):
    """Frozen configuration for one stdio MCP server (untrusted once spawned)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    transport: Literal["stdio"] = "stdio"
    command: str
    args: tuple[str, ...] = ()
    env: dict[str, str] = Field(default_factory=dict)
    # Declared egress; stored for policy use, not enforced by the bridge itself.
    allowed_domains: tuple[str, ...] = ()
    # Empty = all server tools; non-empty = only these raw server tool names.
    tool_allowlist: tuple[str, ...] = ()

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not isinstance(value, str) or SERVER_NAME_RE.match(value) is None:
            raise ValueError(f"server name {value!r} must match ^[a-z][a-z0-9_-]*$.")
        if len(value) > MAX_SERVER_NAME_CHARS:
            raise ValueError(f"server name exceeds {MAX_SERVER_NAME_CHARS} characters.")
        return value

    @field_validator("command")
    @classmethod
    def _command(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("command must be a nonblank string.")
        if "\x00" in value or "\n" in value or "\r" in value:
            raise ValueError("command must be a single executable path (no NUL/newlines).")
        if len(value) > MAX_COMMAND_CHARS:
            raise ValueError(f"command exceeds {MAX_COMMAND_CHARS} characters.")
        return value

    @field_validator("args")
    @classmethod
    def _args(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            if not isinstance(item, str) or "\x00" in item:
                raise ValueError("args entries must be NUL-free strings.")
        return value

    @field_validator("env")
    @classmethod
    def _env(cls, value: dict[str, str]) -> dict[str, str]:
        for key, val in value.items():
            if not isinstance(key, str) or _ENV_NAME_RE.match(key) is None:
                raise ValueError(f"env keys must be variable names; invalid key {key!r}.")
            if not isinstance(val, str) or "\x00" in val:
                raise ValueError(f"env values must be NUL-free strings; invalid value for {key!r}.")
        return value

    @field_validator("allowed_domains")
    @classmethod
    def _domains(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            if not isinstance(item, str) or _DOMAIN_RE.match(item) is None or len(item) > 253:
                raise ValueError(
                    "allowed_domains entries must be bare lowercase hostnames "
                    "(optionally '*.'-prefixed)."
                )
        return value

    @field_validator("tool_allowlist")
    @classmethod
    def _allowlist(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("tool_allowlist entries must be nonblank strings.")
            if len(item) > MAX_TOOL_NAME_CHARS:
                raise ValueError(f"tool_allowlist entry exceeds {MAX_TOOL_NAME_CHARS} characters.")
        return value


class McpDiscoveredTool(BaseModel):
    """One tool as advertised by the server (all fields untrusted DATA).

    ``description_sha256`` is derived from the description text so callers can
    pin it at review time and re-verify on every later session; a mismatch
    means the text changed and the tool must be re-vetted before trust.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)

    @property
    def description_sha256(self) -> str:
        """sha256 of the description text; pin it and re-verify across sessions."""
        return hashlib.sha256(self.description.encode("utf-8")).hexdigest()


def load_mcp_configs(path: str | Path) -> tuple[McpServerConfig, ...]:
    """Load server configs from a JSON array file; absent file -> empty tuple."""
    config_path = Path(path)
    if not config_path.exists():
        return ()
    if config_path.stat().st_size > MAX_CONFIG_BYTES:
        raise ValueError(f"mcp config exceeds {MAX_CONFIG_BYTES} bytes (fail closed).")
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"mcp config {config_path} is not valid JSON: {exc}") from None
    if not isinstance(data, list):
        # Malformed config is a ValueError by contract (Phase 4b spec + tests),
        # not the TypeError TRY004 would prefer.
        raise ValueError(  # noqa: TRY004
            f"mcp config {config_path} must be a JSON array of server objects."
        )
    try:
        return tuple(McpServerConfig.model_validate(entry) for entry in data)
    except ValidationError as exc:
        raise ValueError(f"mcp config {config_path} has invalid server entries: {exc}") from exc


def _bridge_env(configured: Mapping[str, str]) -> dict[str, str]:
    """Ambient env minus secret-looking names, overlaid with explicit config."""
    env = {k: v for k, v in os.environ.items() if _SECRET_ENV_NAME_RE.search(k) is None}
    env.update(configured)  # explicit operator config wins; that is its purpose
    return env


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _kill_process_tree(proc: subprocess.Popen[str]) -> None:
    """Best-effort kill of the server and any children it spawned.

    Minimal replica of ``agency/forge/external_agent.py::_kill_process_tree``
    (kept local so the tools layer stays decoupled from the forge layer).
    """
    if _is_windows():
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=KILL_GRACE_TIMEOUT_S,
                check=False,
            )
        return
    killpg = getattr(os, "killpg", None)
    getpgid = getattr(os, "getpgid", None)
    sigkill = getattr(signal, "SIGKILL", signal.SIGTERM)
    if killpg is not None and getpgid is not None:
        with contextlib.suppress(OSError):
            killpg(getpgid(proc.pid), sigkill)
    with contextlib.suppress(OSError):
        proc.kill()


_EOF = object()  # sentinel: the server stream ended; never a real response


@dataclass(frozen=True)
class _CallOutcome:
    """Mapped ``tools/call`` result: joined text content + optional structured."""

    text: str
    is_error: bool
    structured: dict[str, Any] | None = None


class _StdioSession:
    """One live JSON-RPC 2.0 session over a spawned stdio MCP server.

    A daemon reader thread feeds a queue because Windows cannot ``select`` on
    pipes; every request is bounded by a monotonic deadline. Server-initiated
    requests and notifications are dropped (never answered) — see module
    docstring for the deferred corners.
    """

    def __init__(self, config: McpServerConfig, timeout_s: float) -> None:
        self._deadline = time.monotonic() + timeout_s
        self._responses: queue.Queue[Any] = queue.Queue()
        self._closed = True
        self._next_id = 0
        self._proc: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None
        try:
            self._proc = subprocess.Popen(
                [config.command, *config.args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,  # deadlocks impossible, noise unread
                env=_bridge_env(config.env),
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except OSError as exc:
            raise McpBridgeError(
                f"failed to spawn mcp server {config.name!r}: {type(exc).__name__}"
            ) from exc
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self._closed = False

    def _read_loop(self) -> None:
        try:
            proc = self._proc
            assert proc is not None and proc.stdout is not None
            for line in proc.stdout:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    msg = json.loads(stripped)
                except json.JSONDecodeError:
                    continue  # unparseable noise is dropped, never trusted
                if isinstance(msg, dict) and "id" in msg and ("result" in msg or "error" in msg):
                    self._responses.put(msg)
        except (OSError, ValueError):
            pass  # pipe closed (process killed or exited)
        finally:
            self._responses.put(_EOF)  # fail fast: no waiter can outlive the server

    def _send(self, msg: dict[str, Any]) -> None:
        proc = self._proc
        if self._closed or proc is None or proc.stdin is None:
            raise McpBridgeError("mcp session is closed")
        try:
            proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
            proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise McpBridgeError(f"mcp server closed stdin: {type(exc).__name__}") from exc

    def notify(self, method: str) -> None:
        self._send({"jsonrpc": "2.0", "method": method})

    def request(self, method: str, params: dict[str, Any]) -> Any:
        self._next_id += 1
        request_id = self._next_id
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise McpBridgeError(f"mcp session timeout before {method} response")
        try:
            response = self._responses.get(timeout=remaining)
        except queue.Empty:
            raise McpBridgeError(f"mcp session timeout waiting for {method} response") from None
        if response is _EOF:
            raise McpBridgeError(f"mcp server exited before the {method} response")
        if response.get("id") != request_id:
            raise McpBridgeError(f"mcp {method}: malformed response (mismatched id)")
        if "error" in response:
            err = response["error"]
            message = (
                err.get("message")
                if isinstance(err, dict) and isinstance(err.get("message"), str)
                else ""
            )
            raise McpBridgeError(f"mcp {method} failed: {message or 'server error'}")
        if "result" not in response:
            raise McpBridgeError(f"mcp {method}: malformed response (missing result)")
        return response["result"]

    def initialize(self) -> None:
        result = self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION},
            },
        )
        if not isinstance(result, dict) or not isinstance(result.get("serverInfo"), dict):
            raise McpBridgeError("mcp initialize: bad handshake (missing serverInfo)")
        self.notify("notifications/initialized")

    def list_tools(self) -> tuple[McpDiscoveredTool, ...]:
        tools: list[McpDiscoveredTool] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"cursor": cursor} if cursor else {}
            result = self.request("tools/list", params)
            if not isinstance(result, dict):
                raise McpBridgeError("mcp tools/list: malformed response")
            tools.extend(_parse_tools_page(result))
            next_cursor = result.get("nextCursor")
            if not isinstance(next_cursor, str) or not next_cursor:
                return tuple(tools)
            if len(tools) > MAX_DISCOVERED_TOOLS:
                raise McpBridgeError(
                    f"mcp tools/list exceeds {MAX_DISCOVERED_TOOLS} tools (fail closed)"
                )
            cursor = next_cursor

    def call_tool(self, tool_name: str, args: Mapping[str, Any]) -> _CallOutcome:
        result = self.request("tools/call", {"name": tool_name, "arguments": dict(args)})
        if not isinstance(result, dict):
            raise McpBridgeError("mcp tools/call: malformed response")
        content = result.get("content", [])
        if not isinstance(content, list):
            raise McpBridgeError("mcp tools/call: malformed content")
        parts = [
            block["text"]
            for block in content
            if isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        ]
        structured = result.get("structuredContent")
        if structured is not None and not isinstance(structured, dict):
            raise McpBridgeError("mcp tools/call: malformed structuredContent")
        return _CallOutcome(
            text="\n".join(parts), is_error=result.get("isError") is True, structured=structured
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._proc is not None:
            _kill_process_tree(self._proc)
            with contextlib.suppress(subprocess.SubprocessError):
                self._proc.wait(timeout=KILL_GRACE_TIMEOUT_S)
            for stream in (self._proc.stdin, self._proc.stdout):
                with contextlib.suppress(OSError, ValueError):
                    if stream is not None:
                        stream.close()
        if self._reader is not None and self._reader.is_alive():
            self._reader.join(timeout=1.0)


def _parse_tools_page(result: dict[str, Any]) -> list[McpDiscoveredTool]:
    raw = result.get("tools")
    if not isinstance(raw, list):
        raise McpBridgeError("mcp tools/list: malformed tools array")
    out: list[McpDiscoveredTool] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise McpBridgeError("mcp tools/list: malformed tool entry")
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip() or len(name) > MAX_TOOL_NAME_CHARS:
            raise McpBridgeError("mcp tools/list: invalid tool name")
        description = entry.get("description")
        if description is None:
            description = ""
        if not isinstance(description, str):
            raise McpBridgeError("mcp tools/list: invalid tool description")
        if len(description) > MAX_DESCRIPTION_CHARS:
            raise McpBridgeError(
                f"mcp tools/list: tool description exceeds {MAX_DESCRIPTION_CHARS} chars"
            )
        schema = entry.get("inputSchema")
        if schema is None:
            schema = {}
        if not isinstance(schema, dict):
            raise McpBridgeError("mcp tools/list: invalid tool inputSchema")
        out.append(McpDiscoveredTool(name=name, description=description, input_schema=schema))
    return out


@contextlib.contextmanager
def _open_session(config: McpServerConfig, timeout_s: float) -> Iterator[_StdioSession]:
    session = _StdioSession(config, timeout_s)
    try:
        yield session
    finally:
        session.close()


def discover_tools(
    config: McpServerConfig, *, timeout_s: float = DEFAULT_TIMEOUT_S
) -> tuple[McpDiscoveredTool, ...]:
    """Spawn the server, handshake, and return its tools (raw, untrusted names).

    The subprocess is always killed in a ``finally`` block. Any timeout, bad
    handshake, or malformed response raises :class:`McpBridgeError`.
    """
    with _open_session(config, timeout_s) as session:
        session.initialize()
        return session.list_tools()


def _namespaced_name(server_name: str, tool_name: str) -> str:
    namespaced = f"{server_name}__{tool_name}"
    if _REGISTRY_NAME_RE.match(namespaced) is None:
        raise ValueError(
            f"namespaced mcp tool name {namespaced!r} is not registry-safe "
            "(^[a-z][a-z0-9_]*$); server and tool names must use [a-z0-9_], "
            "e.g. name the server 'my_server' instead of 'my-server'."
        )
    return namespaced


def build_bridge_tools(
    config: McpServerConfig, *, discovered: tuple[McpDiscoveredTool, ...] | None = None
) -> tuple[McpBridgeTool, ...]:
    """Wrap discovered server tools as ordinary Agency tools.

    With ``discovered=None`` a fresh discovery session is spawned; otherwise
    the given results are wrapped as-is (lets callers pin the description
    hashes they reviewed). ``tool_allowlist`` filters here, so both the build
    and registration paths honor it.
    """
    tools = discover_tools(config) if discovered is None else tuple(discovered)
    if config.tool_allowlist:
        allowed = frozenset(config.tool_allowlist)
        tools = tuple(tool for tool in tools if tool.name in allowed)
    wrapped: list[McpBridgeTool] = []
    for tool in tools:
        spec = ToolSpec(
            name=_namespaced_name(config.name, tool.name),
            description=tool.description,
            parameters=dict(tool.input_schema),
            risk=ToolRisk.EXECUTES_CODE,  # the server executes code on this host
        )
        wrapped.append(
            McpBridgeTool(
                spec=spec,
                description_sha256=tool.description_sha256,
                config=config,
                tool_name=tool.name,
            )
        )
    return tuple(wrapped)


def register_bridge_tools(
    registry: ToolRegistry,
    config: McpServerConfig,
    *,
    discovered: tuple[McpDiscoveredTool, ...] | None = None,
) -> list[str]:
    """Register the bridged tools into ``registry``; returns registered names.

    Bridged tools are ordinary registered tools, so the Phase 3 per-agent
    allowlist (``ctx.metadata["allowed_tools"]``) and every other registry
    surface enforce them unchanged.
    """
    names: list[str] = []
    for tool in build_bridge_tools(config, discovered=discovered):
        registry.register(tool)
        names.append(tool.spec.name)
    return names


class McpBridgeTool:
    """Agency :class:`~agency.tools.base.Tool` backed by one MCP server tool.

    Every call spawns a FRESH session (spawn -> initialize -> tools/call ->
    kill). The blocking session runs in a worker thread via
    ``asyncio.to_thread`` so the event loop stays responsive. MCP ``isError``
    maps to ``ToolResult(ok=False)`` with the server-provided message; content
    text becomes ``output`` (plus ``structured`` when the server sent
    ``structuredContent``).
    """

    def __init__(
        self,
        spec: ToolSpec,
        *,
        description_sha256: str,
        config: McpServerConfig,
        tool_name: str,
    ) -> None:
        self.spec = spec
        self.description_sha256 = description_sha256
        self._config = config
        self._tool_name = tool_name

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        _ = ctx  # the registry contract already validated/allowlisted this call
        try:
            outcome = await asyncio.to_thread(
                _call_tool_sync, self._config, self._tool_name, args, DEFAULT_TIMEOUT_S
            )
        except (McpBridgeError, TypeError) as exc:
            return ToolResult(tool=self.spec.name, ok=False, error=f"mcp bridge: {exc}")
        if outcome.is_error:
            return ToolResult(
                tool=self.spec.name,
                ok=False,
                error=outcome.text or "mcp tool reported an error without content",
            )
        output: Any = outcome.text
        if outcome.structured is not None:
            output = {"text": outcome.text, "structured": outcome.structured}
        return ToolResult(tool=self.spec.name, ok=True, output=output)


def _call_tool_sync(
    config: McpServerConfig, tool_name: str, args: Mapping[str, Any], timeout_s: float
) -> _CallOutcome:
    with _open_session(config, timeout_s) as session:
        session.initialize()
        return session.call_tool(tool_name, args)


__all__ = [
    "CLIENT_NAME",
    "CLIENT_VERSION",
    "DEFAULT_TIMEOUT_S",
    "MAX_DESCRIPTION_CHARS",
    "MAX_DISCOVERED_TOOLS",
    "PROTOCOL_VERSION",
    "McpBridgeError",
    "McpBridgeTool",
    "McpDiscoveredTool",
    "McpServerConfig",
    "build_bridge_tools",
    "discover_tools",
    "load_mcp_configs",
    "register_bridge_tools",
]
