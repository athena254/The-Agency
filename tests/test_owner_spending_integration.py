"""Offline spending tracer bullets over concrete owner services and HTTP leaves."""

import asyncio
import inspect
from decimal import Decimal

import httpx
import pytest

from tests.test_owner_metering import runtime as runtime_fixture
from tests.test_owner_pilot_api import api as api_fixture
from tests.test_owner_service import concrete as concrete_fixture

concrete = concrete_fixture
api = api_fixture
runtime = runtime_fixture


async def test_disabled_spending_has_separate_frozen_shape(api):
    client, c, _ = api
    response = await client.get("/api/pilot/spending")
    assert response.status_code == 200
    assert response.json() == {
        "enabled": False,
        "currency": "USD",
        "limit_usd": None,
        "reserved_usd": "0",
        "reported_cost_usd": "0",
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "unresolved_calls": 0,
        "pricing_source": None,
    }
    assert c["requests"] == []


async def test_configured_zero_ceiling_prevents_real_metered_dispatch(concrete, tmp_path):
    from agency.admission.models import OwnerTaskRequest
    from agency.admission.service import OwnerPilotService
    from tests.test_owner_service import terminal

    c = concrete
    kwargs = {}
    if "spending" in inspect.signature(OwnerPilotService).parameters:
        from agency.admission.spending import SpendingConfig, SpendingLedger

        kwargs["spending"] = SpendingLedger(
            tmp_path / "spend.db",
            SpendingConfig(
                limit_usd="0",
                input_usd_per_million="1",
                output_usd_per_million="2",
                pricing_source="offline synthetic rates",
            ),
            identity="synthetic-owner-model",
        )
    service = OwnerPilotService(
        butler=c["butler"],
        store=c["store"],
        budget=c["budget"],
        audit=c["audit"],
        provider_config=c["config"],
        owner_id="owner",
        **kwargs,
    )
    try:
        await service.start()
        _, thread = await service.create_thread(c["owner"], "thread", "Cap")
        task = await service.submit(
            c["owner"],
            OwnerTaskRequest(request_id="turn", thread_id=thread.id, message="calculate 2+3"),
        )
        result = await terminal(service, c["owner"], task.task_id)
        assert c["requests"] == [], "Configured zero-dollar ceiling must prevent provider dispatch"
        assert result.state != "COMPLETED"
    finally:
        await service.stop()


@pytest.mark.parametrize("fault", ["missing", "boolean", "total", "http", "cancel", "content"])
async def test_unknown_dispatch_keeps_durable_reservation(runtime, tmp_path, monkeypatch, fault):
    from agency.admission.spending import SpendingConfig, SpendingLedger
    from agency.llm.metered import MeteredError, MeteredModel

    r = runtime
    config = SpendingConfig(
        limit_usd="1",
        input_usd_per_million="1",
        output_usd_per_million="2",
        pricing_source="offline synthetic rates",
    )
    ledger = SpendingLedger(tmp_path / "spend.db", config, identity="owner")
    model = MeteredModel(r["inner"], r["binding"], r["budget"], r["audit"], spending=ledger)

    async def handle(request):
        r["requests"].append(request)
        if fault == "cancel":
            raise asyncio.CancelledError()
        if fault == "http":
            return httpx.Response(500)
        body = {"choices": [{"message": {"content": None if fault == "content" else "answer"}}]}
        if fault != "missing":
            body["usage"] = {
                "prompt_tokens": True if fault == "boolean" else 10,
                "completion_tokens": 4,
                "total_tokens": 15 if fault == "total" else 14,
            }
        return httpx.Response(200, json=body)

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: r["client"](transport=httpx.MockTransport(handle), **kw)
    )
    with pytest.raises(MeteredError):
        await model.generate("prompt", {"owner_grant": r["grant"]})
    summary = ledger.snapshot()
    assert summary["unresolved_calls"] == 1
    assert Decimal(summary["reserved_usd"]) > 0
    assert Decimal(summary["reported_cost_usd"]) == 0
    assert len(r["requests"]) == 1 and await r["audit"].has_pending_calls()
    assert SpendingLedger(tmp_path / "spend.db", config, identity="owner").snapshot() == summary


async def test_reported_usage_settles_metered_call_and_survives_restart(
    concrete, tmp_path, monkeypatch
):
    from agency.admission.models import OwnerTaskRequest
    from agency.admission.service import OwnerPilotService
    from agency.admission.spending import SpendingConfig, SpendingLedger
    from tests.test_owner_service import terminal

    c = concrete
    config = SpendingConfig(
        limit_usd="1",
        input_usd_per_million="1",
        output_usd_per_million="2",
        pricing_source="offline synthetic rates",
    )
    ledger = SpendingLedger(tmp_path / "spend.db", config, identity="synthetic-owner-model")
    service = OwnerPilotService(
        butler=c["butler"],
        store=c["store"],
        budget=c["budget"],
        audit=c["audit"],
        provider_config=c["config"],
        owner_id="owner",
        spending=ledger,
    )

    async def handle(request):
        c["requests"].append(request)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"final":"five"}'}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
            },
        )

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: c["client"](transport=httpx.MockTransport(handle), **kw)
    )
    try:
        await service.start()
        _, thread = await service.create_thread(c["owner"], "thread", "Usage")
        payload = OwnerTaskRequest(request_id="turn", thread_id=thread.id, message="answer")
        task = await service.submit(c["owner"], payload)
        assert (await terminal(service, c["owner"], task.task_id)).state == "COMPLETED"
        summary = await service.spending(c["owner"])
        assert summary["enabled"] is True
        assert summary["prompt_tokens"] == 10 and summary["completion_tokens"] == 4
        assert Decimal(summary["reported_cost_usd"]) == Decimal("0.000018")
        assert Decimal(summary["reserved_usd"]) == 0 and summary["unresolved_calls"] == 0
        assert (await service.submit(c["owner"], payload)).replayed
        assert len(c["requests"]) == 1
        reopened = SpendingLedger(tmp_path / "spend.db", config, identity="synthetic-owner-model")
        assert reopened.snapshot() == summary
    finally:
        await service.stop()
