"""A2 reports survive real consumer boundaries, never become authority."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from agency.agents.assessment import build_assessment, unchecked_subtask
from agency.agents.executor import ExecutionResult, ExecutionStatus
from agency.butler.config import ButlerConfig
from agency.butler.service import ButlerService
from agency.orchestrator import AgencyOrchestrator
from agency.prototype.app import create_app
from agency.telegram.config import TelegramConfig
from agency.telegram.handler import TelegramHandler
from tests.test_autonomy_verification_assessment import FixtureModel
from tests.test_autonomy_verification_assessment import (
    runtime as runtime,  # noqa: PLC0414 — pytest fixture re-export
)


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", ["general", "security"])
async def test_butler_string_reply_appends_real_runtime_notice(runtime, domain):
    orch, model = runtime
    model.output = '{"final":"completeness accuracy safety"}'
    agent = await orch.register_agent("fixture", domain, ["respond"])
    service = ButlerService(
        config=ButlerConfig(_env_file=None, llm_provider="none"), orchestrator=orch
    )
    await service.start()
    try:
        text = await service.execute(agent, "Summarize fixture.", {"sender": "fixture"})
        body, notice = text.rsplit("\n\n", 1)
        assert "completeness accuracy safety" in body
        assert notice.startswith("Runtime assessment: execution completed;")
        assert "No independent verification or authorization." in notice
        assert (
            "checks not performed"
            if domain == "general"
            else "heuristic checks only; accuracy not checked"
        ) in notice
    finally:
        await service.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["missing", "body", "id", "status"])
async def test_butler_never_substitutes_report_identity_for_missing_or_mismatched_result(
    runtime, monkeypatch, mismatch
):
    orch, _ = runtime
    agent = await orch.register_agent("fixture", "general", ["respond"])
    service = ButlerService(
        config=ButlerConfig(_env_file=None, llm_provider="none"), orchestrator=orch
    )
    await service.start()
    try:

        async def execute(task_id, **kwargs):
            report = build_assessment(
                task_id,
                execution_status="completed",
                output="body",
                subtasks=(
                    unchecked_subtask(
                        "tool_loop", path="tool", execution_status="completed", output="body"
                    ),
                ),
            )
            return SimpleNamespace(
                status=ExecutionStatus.COMPLETED,
                output="changed" if mismatch == "body" else "body",
                task_id="other" if mismatch == "id" else task_id,
                assessment=None
                if mismatch == "missing"
                else report.model_copy(update={"execution_status": "failed"})
                if mismatch == "status"
                else report,
            )

        monkeypatch.setattr(orch, "execute_task", execute)
        text = await service.execute(agent, "Summarize fixture.", {"sender": "fixture"})
        assert text.endswith(
            "Runtime assessment: unavailable. No independent verification or authorization."
        )
    finally:
        await service.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["completed", "blank", "failed", "timeout", "cancelled"])
async def test_isolated_turn_reports_status_without_tool_memory_or_lattice_effects(
    runtime, monkeypatch, mode
):
    orch, model = runtime
    agent = await orch.register_agent("fixture", "general", ["respond"])
    if mode == "blank":
        model.output = " \n\t"
    elif mode != "completed":
        monkeypatch.setattr(
            orch._executor,
            "execute",
            AsyncMock(
                return_value=ExecutionResult(
                    task_id="isolated-fixture",
                    status=ExecutionStatus(mode),
                    output=None,
                )
            ),
        )
    probes = []
    for obj, name in (
        (orch._task_manager, "create_task"),
        (orch._lattice, "create_task"),
        (orch._memory_store, "store"),
        (orch._memory_retrieval, "semantic_search"),
        (orch._tool_driver, "run"),
        (orch._evidence_store, "add_finding"),
    ):
        probe = AsyncMock(side_effect=AssertionError("forbidden isolated effect"))
        monkeypatch.setattr(obj, name, probe)
        probes.append(probe)
    result = await orch.converse_isolated("Summarize fixture.", agent.id)
    expected = "failed" if mode == "blank" else mode
    assert result.status.value == expected
    assert result.assessment is not None
    assert result.assessment.execution_status == expected
    assert (
        result.assessment.output_sha256
        == hashlib.sha256((result.output or "").encode()).hexdigest()
    )
    assert result.assessment.subtasks[0].path == "isolated"
    reason = "isolated_path_unchecked" if expected == "completed" else "execution_not_completed"
    assert all(c.reason == reason for c in result.assessment.subtasks[0].checks)
    for probe in probes:
        probe.assert_not_called()


def test_prototype_actual_butler_returns_bound_report_and_rejects_inbound_assessment(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LATTICE_SQLITE_PATH", str(tmp_path / "lattice.db"))
    model = FixtureModel()
    model.output = "  café 🙂\r\n<script>untrusted</script>  "
    orch = AgencyOrchestrator(llm=model, memory_db_path=str(tmp_path / "memory.db"))
    service = ButlerService(
        config=ButlerConfig(_env_file=None, llm_provider="none"), orchestrator=orch
    )
    with TestClient(
        create_app(service), base_url="http://127.0.0.1", client=("127.0.0.1", 50000)
    ) as client:
        token = client.get("/api/session").json()["csrf_token"]
        headers = {"X-Prototype-CSRF": token}
        denied = client.post(
            "/api/chat",
            json={"message": "fixture", "assessment": {"independently_verified": True}},
            headers=headers,
        )
        assert denied.status_code == 422
        assert model.prompts == []
        response = client.post("/api/chat", json={"message": "fixture"}, headers=headers)
        assert response.status_code == 200
        data = response.json()
        assert data["response"] == model.output
        report = data.get("assessment")
        assert report is not None
        assert report["verification"] == "not_performed"
        assert report["risk_status"] == "not_assessed"
        assert report["independently_verified"] is False
        assert report["output_sha256"] == hashlib.sha256(model.output.encode()).hexdigest()
        assert report["subtasks"][0]["path"] == "isolated"
        assert len(model.prompts) == 1


@pytest.mark.asyncio
async def test_telegram_research_reports_execution_not_verified_research(runtime):
    orch, _ = runtime
    service = ButlerService(
        config=ButlerConfig(_env_file=None, llm_provider="none"), orchestrator=orch
    )
    await service.start()
    handler = TelegramHandler(config=TelegramConfig(bot_token="fixture"), butler=service)
    try:
        text = await handler._handle_research("fixture", "fixture")
        assert "Research execution finished" in text
        assert "Research complete" not in text
        assert "checks not performed; risk not assessed" in text
        assert text.endswith("No independent verification or authorization.")
    finally:
        await service.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_type", [ValueError, TimeoutError])
async def test_isolated_report_failure_logs_only_exception_class_not_private_body(
    runtime, monkeypatch, failure_type
):
    from unittest.mock import Mock

    orch, _ = runtime
    service = ButlerService(
        config=ButlerConfig(_env_file=None, llm_provider="none"), orchestrator=orch
    )
    await service.start()
    try:
        log = Mock()
        monkeypatch.setattr(service, "_log", log)
        monkeypatch.setattr(
            orch, "converse_isolated", AsyncMock(side_effect=failure_type("PRIVATE_EXCEPTION_BODY"))
        )
        reply = await service.handle_isolated_message("fixture", "local:prototype")
        assert reply.status == ("timeout" if failure_type is TimeoutError else "failed")
        assert "PRIVATE_EXCEPTION_BODY" not in reply.text
        log.exception.assert_not_called()
        assert "PRIVATE_EXCEPTION_BODY" not in repr(log.mock_calls)
        assert any(
            call.kwargs.get("error_type") == failure_type.__name__
            for call in log.warning.call_args_list
        )
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_a_self_consistent_wrong_task_report_is_still_unavailable_to_butler(
    runtime, monkeypatch
):
    orch, _ = runtime
    agent = await orch.register_agent("fixture", "general", ["respond"])
    service = ButlerService(
        config=ButlerConfig(_env_file=None, llm_provider="none"), orchestrator=orch
    )
    await service.start()
    try:
        report = build_assessment(
            "other-task",
            execution_status="completed",
            output="body",
            subtasks=(
                unchecked_subtask(
                    "tool_loop", path="tool", execution_status="completed", output="body"
                ),
            ),
        )
        result = ExecutionResult(
            task_id="other-task", status=ExecutionStatus.COMPLETED, output="body", assessment=report
        )
        monkeypatch.setattr(orch, "execute_task", AsyncMock(return_value=result))
        text = await service.execute(agent, "fixture", {"sender": "fixture"})
        assert "Runtime assessment: unavailable" in text
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_failed_heuristic_checks_still_deliver_once_without_telegram_retry(
    runtime, monkeypatch
):
    orch, model = runtime
    model.output = "An answer without the criterion keywords."
    agent = await orch.register_agent("fixture", "security", ["respond"])
    service = ButlerService(
        config=ButlerConfig(_env_file=None, llm_provider="none"), orchestrator=orch
    )
    await service.start()
    handler = TelegramHandler(config=TelegramConfig(bot_token="fixture"), butler=service)
    monkeypatch.setattr(service, "route", AsyncMock(return_value=agent))
    send = AsyncMock()
    monkeypatch.setattr(handler._adapter, "send_message", send)
    try:
        result = await handler.handle_update(
            {
                "message": {
                    "from": {"id": 101},
                    "chat": {"id": 101, "type": "private"},
                    "text": "fixture",
                }
            }
        )
        assert result["status"] == "ok"
        send.assert_awaited_once()
        assert "checks failed" in send.await_args.args[1]
        assert len(model.prompts) == 1
    finally:
        await service.stop()


@pytest.mark.parametrize("mismatch", ["missing_id", "body", "status", "dict"])
def test_prototype_downgrades_unbound_reports_to_null(mismatch):
    report = build_assessment(
        "ID",
        execution_status="completed",
        output="body",
        subtasks=(
            unchecked_subtask(
                "isolated", path="isolated", execution_status="completed", output="body"
            ),
        ),
    )
    reply = SimpleNamespace(
        text="changed" if mismatch == "body" else "body",
        status="completed",
        task_id=None if mismatch == "missing_id" else "ID",
        assessment=report.model_dump()
        if mismatch == "dict"
        else report.model_copy(update={"execution_status": "failed"})
        if mismatch == "status"
        else report,
    )
    service = SimpleNamespace(
        start=AsyncMock(), stop=AsyncMock(), handle_isolated_message=AsyncMock(return_value=reply)
    )
    with TestClient(
        create_app(service), base_url="http://127.0.0.1", client=("127.0.0.1", 50000)
    ) as client:
        token = client.get("/api/session").json()["csrf_token"]
        result = client.post(
            "/api/chat", json={"message": "fixture"}, headers={"X-Prototype-CSRF": token}
        )
        assert result.status_code == 200
        assert result.json()["assessment"] is None
