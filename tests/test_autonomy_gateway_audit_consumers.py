"""A3.1 real gateway/transport regressions; all provider and Telegram effects offline."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from agency.butler.config import ButlerConfig
from agency.butler.service import ButlerService
from agency.kernel.audit import AuditLog
from agency.lattice import config as lattice_config
from agency.lattice.models import LatticeConfig
from agency.llm.adapter import LLMAdapter
from agency.orchestrator import AgencyOrchestrator
from agency.prototype.app import create_app

NOTICE = "Audit unavailable; execution outcome unconfirmed. Do not automatically retry."


@pytest.fixture(autouse=True)
def isolated_lattice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Do not rely on the outer runner to protect the runtime Lattice."""
    monkeypatch.setattr(
        lattice_config, "_config_singleton", LatticeConfig(sqlite_path=str(tmp_path / "lattice.db"))
    )


def gateway(tmp_path: Path) -> ButlerService:
    model = LLMAdapter(provider="echo", model="echo")
    orchestrator = AgencyOrchestrator(llm=model, memory_db_path=str(tmp_path / "memory.db"))
    return ButlerService(
        config=ButlerConfig(llm_provider="none"),
        orchestrator=orchestrator,
        audit_log=AuditLog(tmp_path / "gateway-audit.db"),
    )


async def test_gateway_helper_binds_its_own_lattice_database(tmp_path: Path) -> None:
    service = gateway(tmp_path)
    await service.start()
    try:
        assert (tmp_path / "lattice.db").is_file()
    finally:
        await service.stop()
        await service._audit.close()


@pytest.mark.parametrize("phase", ["butler.isolated_request", "butler.handle_isolated_message"])
def test_prototype_audit_failure_is_fixed_503_without_success_or_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    service = gateway(tmp_path)
    dispatch = MagicMock(return_value="SYNTHETIC_PRIVATE_PROVIDER_OUTPUT")
    monkeypatch.setattr(service.orchestrator._llm_adapter, "_echo_generate", dispatch)
    append = service._audit.append

    async def inject(entry):
        if entry.action == phase:
            raise OSError("SYNTHETIC_PRIVATE_STORAGE_ERROR")
        return await append(entry)

    monkeypatch.setattr(service._audit, "append", inject)
    with TestClient(
        create_app(service),
        base_url="http://127.0.0.1",
        client=("127.0.0.1", 50000),
        raise_server_exceptions=False,
    ) as client:
        try:
            token = client.get("/api/session").json()["csrf_token"]
            with capture_logs() as logs:
                reply = client.post(
                    "/api/chat",
                    json={"message": "SYNTHETIC_PRIVATE_INPUT"},
                    headers={"Origin": "http://127.0.0.1", "X-Prototype-CSRF": token},
                )
            assert reply.status_code == 503
            assert reply.json() == {"error": NOTICE}
            assert "assessment" not in reply.json()
            assert dispatch.call_count == int(phase == "butler.handle_isolated_message")
            assert not any(
                r.get("exc_info") or r["event"] == "prototype.api_unhandled" for r in logs
            )
            assert all("SYNTHETIC_PRIVATE_STORAGE_ERROR" not in repr(r) for r in logs)
        finally:
            assert client.portal is not None
            client.portal.call(service._audit.close)


@pytest.mark.parametrize("path", ["chat", "thread", "process", "process_thread"])
@pytest.mark.parametrize("late", [False, True])
async def test_telegram_reports_audit_failure_without_fallback_or_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str, late: bool
) -> None:
    from agency.telegram.config import TelegramConfig
    from agency.telegram.handler import TelegramHandler

    service = gateway(tmp_path)
    dispatch = MagicMock(return_value='{"final":"PRIVATE_TELEGRAM_OUTPUT"}')
    monkeypatch.setattr(service.orchestrator._llm_adapter, "_echo_generate", dispatch)
    monkeypatch.setattr(service, "_llm", AsyncMock(return_value="general"))
    handler = TelegramHandler(
        TelegramConfig(bot_token="synthetic", beta_mode=False), butler=service
    )
    send = AsyncMock(return_value={"message_id": 1})
    monkeypatch.setattr(handler._adapter, "send_message", send)
    fallback = AsyncMock()
    monkeypatch.setattr(handler._demo, "handle", fallback)
    phase = (
        "butler.request"
        if not late
        else "butler.command"
        if "thread" in path
        else "butler.handle_message"
    )
    append = service._audit.append

    async def inject(entry):
        if entry.action == phase:
            raise OSError("PRIVATE_TELEGRAM_STORAGE_ERROR")
        return await append(entry)

    monkeypatch.setattr(service._audit, "append", inject)
    await service.start()
    message = {
        "from": {"id": 101},
        "chat": {"id": 101, "type": "private"},
        "text": "/new PRIVATE_TELEGRAM_TITLE" if "thread" in path else "PRIVATE_TELEGRAM_INPUT",
    }
    try:
        with capture_logs() as logs:
            if path.startswith("process"):
                result = await handler.process_message(message)
                assert result == NOTICE
                send.assert_not_awaited()
            else:
                result = await handler.handle_update({"update_id": 10, "message": message})
                assert result["status"] == "rejected"
                assert result["reason"] == "audit unavailable"
                send.assert_awaited_once_with(101, NOTICE)
        fallback.assert_not_awaited()
        assert dispatch.call_count == int(late and "thread" not in path)
        assert not any(r.get("exc_info") for r in logs)
        assert all("PRIVATE_TELEGRAM_STORAGE_ERROR" not in repr(r) for r in logs)
    finally:
        await handler.close()
        await service.stop()
        await service._audit.close()


@pytest.mark.parametrize("text", ["hello fixture", "/new fixture"])
@pytest.mark.parametrize("late", [False, True])
@pytest.mark.parametrize("send_fails", [False, True])
async def test_real_legacy_poller_separates_audit_failure_from_delivery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str, late: bool, send_fails: bool
) -> None:
    from agency.telegram.config import TelegramConfig
    from telegram_bot import TelegramBot

    monkeypatch.setenv("REMEX_PROFILE_DB_PATH", str(tmp_path / "profiles.db"))
    service = gateway(tmp_path)
    dispatch = MagicMock(return_value='{"final":"fixture"}')
    monkeypatch.setattr(service.orchestrator._llm_adapter, "_echo_generate", dispatch)
    phase = (
        "butler.request"
        if not late
        else "butler.command"
        if text.startswith("/")
        else "butler.handle_message"
    )
    append = service._audit.append

    async def inject(entry):
        if entry.action == phase:
            raise OSError("fixture audit failure")
        return await append(entry)

    monkeypatch.setattr(service._audit, "append", inject)
    bot = TelegramBot(
        token="synthetic", config=TelegramConfig(bot_token="synthetic", beta_mode=False)
    )
    bot._butler = service
    bot._orchestrator = service.orchestrator
    bot._handler._butler = service
    send = AsyncMock(side_effect=RuntimeError("fixture delivery failure") if send_fails else None)
    monkeypatch.setattr(bot._handler._adapter, "send_message", send)
    update = {
        "update_id": 75,
        "message": {"from": {"id": 101}, "chat": {"id": 101, "type": "private"}, "text": text},
    }
    polling = AsyncMock(return_value=[update])
    monkeypatch.setattr(bot._handler._adapter, "get_updates", polling)
    handling = AsyncMock(wraps=bot._handler.handle_update)
    monkeypatch.setattr(bot._handler, "handle_update", handling)
    await service.start()
    bot._running = True
    try:
        outcome = await bot._poll_batch()
        assert outcome == ("transient" if send_fails else "ok")
        send.assert_awaited_once_with(101, NOTICE)
        assert dispatch.call_count == int(late and not text.startswith("/"))
        if send_fails:
            assert bot._offset is None
        else:
            assert bot._offset == 76
            assert await bot._poll_batch() == "ok"
            assert polling.await_args.kwargs["offset"] == 76
            handling.assert_awaited_once()
            send.assert_awaited_once()
            assert dispatch.call_count == int(late and not text.startswith("/"))
    finally:
        await bot.stop()
        await service._audit.close()


@pytest.mark.parametrize("unknown", [False, True])
def test_prototype_blocking_metered_error_never_logs_request_or_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unknown: bool
) -> None:
    from agency.llm.metered import MeteredDenied, MeteredError

    service = gateway(tmp_path)
    error = (
        MeteredError("PRIVATE_METERED_ERROR_A31")
        if unknown
        else MeteredDenied("PRIVATE_METERED_ERROR_A31")
    )
    monkeypatch.setattr(service, "_llm", AsyncMock(side_effect=error))
    dispatch = MagicMock()
    monkeypatch.setattr(service.orchestrator._llm_adapter, "_echo_generate", dispatch)
    with TestClient(
        create_app(service),
        base_url="http://127.0.0.1",
        client=("127.0.0.1", 50000),
        raise_server_exceptions=False,
    ) as client:
        try:
            token = client.get("/api/session").json()["csrf_token"]
            with capture_logs() as logs:
                response = client.post(
                    "/api/chat",
                    json={"message": "PRIVATE_METERED_INPUT_A31"},
                    headers={"Origin": "http://127.0.0.1", "X-Prototype-CSRF": token},
                )
            assert response.status_code == 500
            assert response.json() == {"error": "Internal server error."}
            dispatch.assert_not_called()
            assert not any(r.get("exc_info") for r in logs)
            assert all("PRIVATE_METERED_ERROR_A31" not in repr(r) for r in logs)
        finally:
            assert client.portal is not None
            client.portal.call(service._audit.close)
