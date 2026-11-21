"""Security tests for the minimal MCP stdio bridge (PLAN_TOOL_PROVISIONING Phase 4b).

A FAKE MCP server — a small Python script written into tmp that speaks
newline-delimited JSON-RPC 2.0 over stdio — stands in for real servers.
No network, no real servers, no ``mcp`` pip package.

The bridge must: treat server descriptions as untrusted DATA (never logged,
only carried in the quoted ``ToolSpec.description`` field with a pinned
sha256), fail closed on timeouts / bad handshakes / malformed responses,
forward only explicitly configured env keys (ambient secret-looking variables
are stripped), namespace tool names into registry-safe names, respect the
server tool allowlist, and ride the Phase 3 per-agent allowlist unchanged.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from structlog.testing import capture_logs

from agency.tools.base import ToolContext, ToolError, ToolRisk
from agency.tools.mcp_bridge import (
    McpBridgeError,
    McpDiscoveredTool,
    McpServerConfig,
    build_bridge_tools,
    discover_tools,
    load_mcp_configs,
    register_bridge_tools,
)
from agency.tools.registry import ToolRegistry

FAKE_SERVER = r'''
"""Fake MCP server: newline-delimited JSON-RPC 2.0 over stdio (test double)."""
import json
import os
import sys

MODE = sys.argv[1] if len(sys.argv) > 1 else "normal"

LONG_DESC = "Trust me, this tool is totally safe and will never change its mind. " * 5


def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


if MODE == "exit":
    sys.exit(0)

if MODE == "silent":
    for _line in sys.stdin:
        pass
    sys.exit(0)


def tools_page(cursor):
    if cursor == "p2":
        return [
            {
                "name": "long_description_tool",
                "description": LONG_DESC,
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "structured_tool",
                "description": "Echoes arguments via structuredContent.",
                "inputSchema": {"type": "object", "properties": {}},
            },
        ], None
    return [
        {
            "name": "echo_tool",
            "description": "Echo the arguments and selected env keys back as JSON text.",
            "inputSchema": {"type": "object", "properties": {"msg": {"type": "string"}}},
        },
        {
            "name": "fail_tool",
            "description": "Always answers with isError true.",
            "inputSchema": {"type": "object", "properties": {}},
        },
    ], "p2"


for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        continue
    if msg.get("method") == "initialize":
        if MODE == "badhandshake":
            send({"jsonrpc": "2.0", "id": msg["id"], "result": {"unexpected": True}})
        else:
            send({
                "jsonrpc": "2.0",
                "id": msg["id"],
                "result": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fake-mcp", "version": "0.1.0"},
                },
            })
    elif "id" not in msg:
        continue  # notification (e.g. notifications/initialized): never reply
    elif msg.get("method") == "tools/list":
        page, cursor = tools_page((msg.get("params") or {}).get("cursor"))
        result = {"tools": page}
        if cursor:
            result["nextCursor"] = cursor
        send({"jsonrpc": "2.0", "id": msg["id"], "result": result})
    elif msg.get("method") == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        if name == "fail_tool":
            send({
                "jsonrpc": "2.0",
                "id": msg["id"],
                "result": {
                    "content": [{"type": "text", "text": "boom: injected failure"}],
                    "isError": True,
                },
            })
        elif name == "structured_tool":
            send({
                "jsonrpc": "2.0",
                "id": msg["id"],
                "result": {
                    "content": [{"type": "text", "text": "structured echo"}],
                    "structuredContent": {"echo": args},
                },
            })
        else:
            payload = {
                "args": args,
                "env": {
                    "MCP_FAKE_MARKER": os.environ.get("MCP_FAKE_MARKER"),
                    "AGENCY_TEST_SECRET_TOKEN": os.environ.get("AGENCY_TEST_SECRET_TOKEN"),
                },
            }
            send({
                "jsonrpc": "2.0",
                "id": msg["id"],
                "result": {"content": [{"type": "text", "text": json.dumps(payload)}]},
            })
    else:
        send({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "nope"}})
'''

_EXPECTED_NAMESPACED = frozenset(
    {
        "fake_mcp__echo_tool",
        "fake_mcp__fail_tool",
        "fake_mcp__long_description_tool",
        "fake_mcp__structured_tool",
    }
)


def _write_server(tmp_path: Path) -> Path:
    script = tmp_path / "fake_mcp_server.py"
    script.write_text(FAKE_SERVER, encoding="utf-8")
    return script


def _config(script: Path, mode: str = "normal", **overrides: Any) -> McpServerConfig:
    values: dict[str, Any] = {
        "name": "fake_mcp",
        "command": sys.executable,
        "args": (str(script), mode),
        "env": {"MCP_FAKE_MARKER": "marker-value"},
    }
    values.update(overrides)
    return McpServerConfig(**values)


def _ctx(**metadata: Any) -> ToolContext:
    return ToolContext(agent_id="agent-1", task_id="task-1", metadata=dict(metadata))


def _discovered(*names: str, description: str = "d") -> tuple[McpDiscoveredTool, ...]:
    """Hand-built discovery results so non-discovery tests skip subprocesses."""
    return tuple(
        McpDiscoveredTool(name=name, description=description, input_schema={"type": "object"})
        for name in names
    )


# --- config loading -----------------------------------------------------------


def test_load_mcp_configs_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_mcp_configs(tmp_path / "absent.json") == ()


def test_load_mcp_configs_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "mcp_servers.json"
    path.write_text(
        json.dumps(
            [
                {
                    "name": "fake_mcp",
                    "transport": "stdio",
                    "command": "srv.exe",
                    "args": ["--port", "1"],
                    "env": {"MCP_FAKE_MARKER": "x"},
                    "allowed_domains": ["example.com", "*.example.org"],
                    "tool_allowlist": ["echo_tool"],
                }
            ]
        ),
        encoding="utf-8",
    )
    (config,) = load_mcp_configs(path)
    assert config.name == "fake_mcp"
    assert config.command == "srv.exe"
    assert config.args == ("--port", "1")
    assert config.env == {"MCP_FAKE_MARKER": "x"}
    assert config.allowed_domains == ("example.com", "*.example.org")
    assert config.tool_allowlist == ("echo_tool",)


def test_load_mcp_configs_malformed_json_raises_value_error(tmp_path: Path) -> None:
    path = tmp_path / "mcp_servers.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="valid JSON"):
        load_mcp_configs(path)


def test_load_mcp_configs_rejects_non_list_and_bad_entries(tmp_path: Path) -> None:
    path = tmp_path / "mcp_servers.json"
    path.write_text('{"name": "x"}', encoding="utf-8")
    with pytest.raises(ValueError, match="array"):
        load_mcp_configs(path)
    path.write_text('[{"name": "Bad Name", "command": "x"}]', encoding="utf-8")
    with pytest.raises(ValueError):
        load_mcp_configs(path)


def test_mcp_server_config_validation_fails_closed() -> None:
    with pytest.raises(ValidationError):
        McpServerConfig(name="Bad Name", command="x")
    with pytest.raises(ValidationError):
        McpServerConfig(name="ok", command="  ")
    with pytest.raises(ValidationError):
        McpServerConfig(name="ok", command="x", transport="http")
    with pytest.raises(ValidationError):
        McpServerConfig(name="ok", command="x", unknown_field=1)


def test_shipped_default_config_is_empty() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    assert load_mcp_configs(repo_root / "data" / "mcp_servers.json") == ()


# --- discovery ----------------------------------------------------------------


def test_discover_tools_returns_raw_names_and_hashes(tmp_path: Path) -> None:
    config = _config(_write_server(tmp_path))
    tools = discover_tools(config, timeout_s=15)
    # Raw server names at discovery; namespacing happens at build time.
    assert [t.name for t in tools] == [
        "echo_tool",
        "fail_tool",
        "long_description_tool",
        "structured_tool",
    ]
    long_tool = tools[2]
    assert len(long_tool.description) >= 200
    assert (
        long_tool.description_sha256
        == hashlib.sha256(long_tool.description.encode("utf-8")).hexdigest()
    )
    assert tools[0].input_schema["properties"]["msg"]["type"] == "string"


def test_description_sha256_stable_across_sessions(tmp_path: Path) -> None:
    config = _config(_write_server(tmp_path))
    first = discover_tools(config, timeout_s=15)
    second = discover_tools(config, timeout_s=15)
    assert [t.description_sha256 for t in first] == [t.description_sha256 for t in second]
    assert (
        first[0].description_sha256
        == hashlib.sha256(first[0].description.encode("utf-8")).hexdigest()
    )


def test_descriptions_never_logged(tmp_path: Path) -> None:
    config = _config(_write_server(tmp_path))
    with capture_logs() as events:
        tools = discover_tools(config, timeout_s=15)
        registry = ToolRegistry()
        register_bridge_tools(registry, config, discovered=tools)
    blob = json.dumps(events)
    assert tools[2].description not in blob
    assert "Echo the arguments" not in blob


# --- build: namespacing, allowlist, risk ---------------------------------------


def test_build_bridge_tools_namespaces_and_carries_data() -> None:
    config = McpServerConfig(name="fake_mcp", command="whatever")
    tools = build_bridge_tools(config, discovered=_discovered("echo_tool", "fail_tool"))
    assert [t.spec.name for t in tools] == ["fake_mcp__echo_tool", "fake_mcp__fail_tool"]
    # The server description is quoted data in the spec field; the hash pins it.
    assert tools[0].spec.description == "d"
    assert tools[0].spec.parameters == {"type": "object"}
    assert tools[0].description_sha256 == hashlib.sha256(b"d").hexdigest()


def test_namespaced_name_rejects_registry_unsafe_tool_name() -> None:
    config = McpServerConfig(name="fake_mcp", command="whatever")
    with pytest.raises(ValueError, match="registry-safe"):
        build_bridge_tools(config, discovered=_discovered("bad-name"))


def test_hyphenated_server_name_loads_but_fails_at_build() -> None:
    # The config regex allows hyphens; the registry-safe namespaced name does
    # not, so the failure surfaces at build with an actionable message.
    config = McpServerConfig(name="fake-mcp", command="whatever")
    with pytest.raises(ValueError, match="registry-safe"):
        build_bridge_tools(config, discovered=_discovered("echo_tool"))


def test_tool_allowlist_filters_at_build() -> None:
    config = McpServerConfig(name="fake_mcp", command="whatever", tool_allowlist=("echo_tool",))
    tools = build_bridge_tools(config, discovered=_discovered("echo_tool", "fail_tool"))
    assert [t.spec.name for t in tools] == ["fake_mcp__echo_tool"]


def test_register_bridge_tools_respects_allowlist() -> None:
    registry = ToolRegistry()
    config = McpServerConfig(name="fake_mcp", command="whatever", tool_allowlist=("fail_tool",))
    names = register_bridge_tools(
        registry, config, discovered=_discovered("echo_tool", "fail_tool")
    )
    assert names == ["fake_mcp__fail_tool"]
    assert registry.get("fake_mcp__fail_tool") is not None
    with pytest.raises(ToolError):
        registry.get("fake_mcp__echo_tool")


def test_bridged_tool_risk_is_executes_code() -> None:
    config = McpServerConfig(name="fake_mcp", command="whatever")
    (tool,) = build_bridge_tools(config, discovered=_discovered("echo_tool"))
    assert tool.spec.risk is ToolRisk.EXECUTES_CODE


# --- end-to-end through the registry -------------------------------------------


async def test_registry_roundtrip_and_env_passthrough(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("AGENCY_TEST_SECRET_TOKEN", "s3cret-value")
    registry = ToolRegistry()
    names = register_bridge_tools(registry, _config(_write_server(tmp_path)))
    assert set(names) == _EXPECTED_NAMESPACED
    result = await registry.call("fake_mcp__echo_tool", {"msg": "hello"}, _ctx())
    assert result.ok is True
    payload = json.loads(result.output)
    # The fake server echoing the arguments proves it received tools/call.
    assert payload["args"] == {"msg": "hello"}
    # Explicitly configured env key is forwarded; the ambient secret is not.
    assert payload["env"]["MCP_FAKE_MARKER"] == "marker-value"
    assert payload["env"]["AGENCY_TEST_SECRET_TOKEN"] is None


async def test_iserror_maps_to_failed_tool_result(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_bridge_tools(registry, _config(_write_server(tmp_path)))
    result = await registry.call("fake_mcp__fail_tool", {}, _ctx())
    assert result.ok is False
    assert result.error == "boom: injected failure"


async def test_structured_content_maps_into_output(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_bridge_tools(registry, _config(_write_server(tmp_path)))
    result = await registry.call("fake_mcp__structured_tool", {"a": 1}, _ctx())
    assert result.ok is True
    assert result.output == {"text": "structured echo", "structured": {"echo": {"a": 1}}}


async def test_per_agent_allowlist_denial_fixed_error(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_bridge_tools(registry, _config(_write_server(tmp_path)))
    denied = await registry.call(
        "fake_mcp__echo_tool", {"msg": "hi"}, _ctx(allowed_tools=frozenset({"other_tool"}))
    )
    assert denied.ok is False
    assert denied.error == "tool not provisioned for this agent"
    allowed = await registry.call(
        "fake_mcp__echo_tool",
        {"msg": "hi"},
        _ctx(allowed_tools=frozenset({"fake_mcp__echo_tool"})),
    )
    assert allowed.ok is True


# --- fail-closed session failures ----------------------------------------------


def test_server_exit_fails_closed(tmp_path: Path) -> None:
    config = _config(_write_server(tmp_path), "exit")
    with pytest.raises(McpBridgeError):
        discover_tools(config, timeout_s=10)


def test_bad_handshake_fails_closed(tmp_path: Path) -> None:
    config = _config(_write_server(tmp_path), "badhandshake")
    with pytest.raises(McpBridgeError, match="bad handshake"):
        discover_tools(config, timeout_s=10)


def test_silent_server_times_out_fails_closed(tmp_path: Path) -> None:
    config = _config(_write_server(tmp_path), "silent")
    with pytest.raises(McpBridgeError, match="timeout"):
        discover_tools(config, timeout_s=1)
