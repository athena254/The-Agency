"""MCP authority boundary for the opt-in remote merge / GitHub release tools."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agency.forge.mcp_server import ForgeMcpServer


def _call(server: ForgeMcpServer, name: str, arguments: dict) -> dict:
    async def run() -> dict:
        response = await server.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        assert response is not None and "result" in response
        return response["result"]

    return asyncio.run(run())


def _caps(server: ForgeMcpServer) -> dict:
    async def run() -> dict:
        response = await server.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "forge_capabilities", "arguments": {}},
            }
        )
        assert response is not None
        return response["result"]["structuredContent"]

    return asyncio.run(run())


def test_calibration_caveat_reflects_owner_waiver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = Path("C:/Users/PC/The-Agency/data/forge_calibration_v2_result.json")
    monkeypatch.setenv("AGENCY_FORGE_CALIBRATION_FILE", str(result))
    caps = _caps(ForgeMcpServer())
    assert caps["calibration"]["calibrated"] is True
    assert "ai_quorum_calibrated" in caps["caveats"]
    assert "human_calibration_pending" not in caps["caveats"]


def test_remote_ops_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENCY_FORGE_ENABLE_REMOTE_OPS", raising=False)
    caps = _caps(ForgeMcpServer())
    assert caps["authority"]["merge"] == "not_authorized"
    assert caps["authority"]["release"] == "not_authorized"
    assert caps["authority"]["deploy"] == "not_authorized"
    assert caps["authority"]["remote_ops_enabled"] is False
    assert caps["authority"]["ship_exposed"] is False


def test_remote_ops_opt_in_changes_authority(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENCY_FORGE_ENABLE_REMOTE_OPS", "1")
    caps = _caps(ForgeMcpServer())
    assert caps["authority"]["merge"] == "approval_gated_remote_push"
    assert caps["authority"]["release"] == "approval_gated_github_release"
    assert caps["authority"]["deploy"] == "not_authorized"
    assert caps["authority"]["remote_ops_enabled"] is True


def test_disabled_handler_fails_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("AGENCY_FORGE_ENABLE_REMOTE_OPS", raising=False)
    result = _call(
        ForgeMcpServer(),
        "forge_remote_merge",
        {
            "root": str(tmp_path),
            "changed_paths": ["a.py"],
            "test_paths": ["tests/test_a.py"],
            "remote": "origin",
            "target_branch": "main",
            "approved_by": "alice",
        },
    )
    assert result["isError"] is True
    body = result["structuredContent"]
    assert body["ok"] is False
    assert "disabled" in body["error"]


def test_enabled_handler_requires_real_gate_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("AGENCY_FORGE_ENABLE_REMOTE_OPS", "1")
    result = _call(
        ForgeMcpServer(),
        "forge_remote_merge",
        {
            "root": str(tmp_path),
            "changed_paths": ["a.py"],
            "remote": "origin",
            "target_branch": "main",
            "approved_by": "alice",
        },
    )
    body = result["structuredContent"]
    assert body["ok"] is False
    assert body["caveats"] == ["no_verified_gate_evidence"]
