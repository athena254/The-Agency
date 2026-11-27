"""Focused tests for the human approval surface exposed over MCP."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from agency.forge.mcp_server import ForgeMcpServer
from agency.forge.orchestration import BlacksmithOps, JobState


def _awaiting_job(tmp_path: Path) -> tuple[str, str]:
    """Create one QUEUED job in a private DB and move it to AWAITING_HUMAN."""
    db = str(tmp_path / "approvals_jobs.db")
    ops = BlacksmithOps(db)
    try:
        job = ops.submit("build slice", "do the work", "C:/repo")
        ops._transition(job, JobState.AWAITING_HUMAN, "test_setup")
    finally:
        ops.close()
    return db, job.job_id


def _call(server: ForgeMcpServer, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    async def run() -> dict[str, Any]:
        response = await server.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        assert response is not None and "result" in response, response
        return response["result"]  # type: ignore[return-value]

    return asyncio.run(run())


def _list(db: str) -> dict[str, Any]:
    return _call(ForgeMcpServer(), "forge_approvals_list", {"db": db})["structuredContent"]


def test_approvals_list_is_read_only_and_carries_authority_caveats(tmp_path: Path) -> None:
    db, job_id = _awaiting_job(tmp_path)
    result = _call(ForgeMcpServer(), "forge_approvals_list", {"db": db})
    assert result["isError"] is False
    body = result["structuredContent"]
    assert body["ok"] is True
    assert [brief["job_id"] for brief in body["briefs"]] == [job_id]
    assert body["briefs"][0]["state"] == JobState.AWAITING_HUMAN.value
    assert body["count"] == 1
    assert "no_merge_authorized" in body["caveats"]
    assert "no_release_authorized" in body["caveats"]


def test_approval_decide_approve_records_without_shipping(tmp_path: Path) -> None:
    db, job_id = _awaiting_job(tmp_path)
    result = _call(
        ForgeMcpServer(),
        "forge_approval_decide",
        {
            "db": db,
            "job_id": job_id,
            "approver": "alice",
            "decision": "approve",
            "note": "looks good",
        },
    )
    assert result["isError"] is False
    body = result["structuredContent"]
    assert body["ok"] is True
    record = body["record"]
    assert record["job_id"] == job_id
    assert record["approver"] == "alice"
    assert record["decision"] == "approve"
    assert record["note"] == "looks good"
    assert record["state_after"] == JobState.AWAITING_HUMAN.value
    assert "no_auto_merge" in body["caveats"]
    assert "no_merge_authorized" in body["caveats"]
    assert "no_release_authorized" in body["caveats"]
    # Approval is not a ship: the job is still awaiting a human decision.
    assert [brief["job_id"] for brief in _list(db)["briefs"]] == [job_id]


def test_approval_decide_reject_blocks_the_job(tmp_path: Path) -> None:
    db, job_id = _awaiting_job(tmp_path)
    result = _call(
        ForgeMcpServer(),
        "forge_approval_decide",
        {"db": db, "job_id": job_id, "approver": "alice", "decision": "reject"},
    )
    assert result["isError"] is False
    body = result["structuredContent"]
    assert body["ok"] is True
    assert body["record"]["decision"] == "reject"
    assert body["record"]["state_after"] == JobState.BLOCKED.value
    assert "no_release_authorized" in body["caveats"]
    assert _list(db)["briefs"] == []


def test_generic_approver_is_refused_failed_closed(tmp_path: Path) -> None:
    db, job_id = _awaiting_job(tmp_path)
    result = _call(
        ForgeMcpServer(),
        "forge_approval_decide",
        {"db": db, "job_id": job_id, "approver": "agent", "decision": "approve"},
    )
    assert result["isError"] is True
    body = result["structuredContent"]
    assert body["ok"] is False
    assert "named human approver" in body["error"]
    assert "no_merge_authorized" in body["caveats"]
    assert "no_release_authorized" in body["caveats"]
    # The refusal changed nothing: no decision was recorded.
    assert [brief["job_id"] for brief in _list(db)["briefs"]] == [job_id]
