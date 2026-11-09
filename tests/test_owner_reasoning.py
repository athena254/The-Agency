"""Explicit, frozen reasoning budgets for bounded owner model calls."""

import pytest
from pydantic import SecretStr

from agency.llm.config import LLMConfig, ProviderKind
from tests.test_owner_service import concrete

__all__ = ["concrete"]  # Register the existing pytest fixture without duplicating its setup.


def test_reasoning_setting_is_explicit_validated_configuration():
    config = LLMConfig(
        provider=ProviderKind.OPENAI,
        model="mercury-3",
        api_key=SecretStr("SYNTHETIC_KEY"),
        base_url="https://provider.invalid/v1",
        timeout=12.0,
        reasoning_effort="instant",
    )
    assert config.reasoning_effort == "instant"


async def test_reasoning_budget_reaches_the_frozen_http_request(monkeypatch):
    import json

    import httpx

    from agency.llm.adapter import LLMAdapter

    config = LLMConfig(
        provider=ProviderKind.OPENAI,
        model="mercury-3",
        api_key=SecretStr("SYNTHETIC_KEY"),
        base_url="https://provider.invalid/v1",
        timeout=12.0,
        reasoning_effort="instant",
    )
    requests = []
    client = httpx.AsyncClient

    def handle(request):
        requests.append(request)
        payload = json.loads(request.content)
        text = (
            '{"final":"cobalt-cedar-842"}' if payload.get("reasoning_effort") == "instant" else None
        )
        return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: client(transport=httpx.MockTransport(handle), **kw)
    )
    adapter = LLMAdapter(config=config)
    snapshot = adapter.freeze_dispatch()
    adapter._reasoning_effort = "high"
    assert (
        await adapter.generate_bound("Synthetic tool result: UTC time read successfully", snapshot)
        == '{"final":"cobalt-cedar-842"}'
    )
    assert len(requests) == 1
    assert json.loads(requests[0].content)["reasoning_effort"] == "instant"


@pytest.mark.parametrize("bad", [True, 1, {}, "INSTANT", " instant", "none"])
def test_frozen_snapshot_rejects_invalid_reasoning_values(bad):
    from dataclasses import replace

    from agency.llm.adapter import LLMAdapter

    adapter = LLMAdapter(provider="echo", model="echo", timeout=12.0)
    with pytest.raises(ValueError):
        replace(adapter.freeze_dispatch(), reasoning_effort=bad)


def test_launcher_preserves_the_explicit_reasoning_setting(tmp_path):
    from agency.llm.adapter import LLMAdapter
    from agency.prototype.pilot_launcher import validate
    from tests.test_pilot_launcher import config_file, protect, snapshot, token_file

    protect(tmp_path)
    path = config_file(tmp_path, reasoning_effort="instant")
    token_file(tmp_path)
    before = snapshot(tmp_path)
    settings = validate(path, tmp_path, check=True)
    assert settings.config.reasoning_effort == "instant"
    assert settings.provider_config.reasoning_effort == "instant"
    assert (
        LLMAdapter(config=settings.provider_config).freeze_dispatch().reasoning_effort == "instant"
    )
    assert snapshot(tmp_path) == before


async def test_reasoning_budget_survives_the_real_owner_service_tool_loop(concrete, monkeypatch):
    import json

    import httpx

    from agency.admission.models import OwnerTaskRequest
    from tests.test_owner_service import make_service, terminal

    c = concrete
    c["config"] = c["config"].model_copy(update={"reasoning_effort": "instant"})
    requests = []

    def handle(request):
        requests.append(request)
        payload = json.loads(request.content)
        text = None
        if payload.get("reasoning_effort") == "instant":
            text = (
                '{"action":{"name":"utc_time","args":{}}}'
                if len(requests) == 1
                else '{"final":"UTC tool completed"}'
            )
        return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: c["client"](transport=httpx.MockTransport(handle), **kw)
    )
    service = make_service(c)
    try:
        await service.start()
        _, thread = await service.create_thread(c["owner"], "reasoning-thread", "Reasoning budget")
        task = await service.submit(
            c["owner"],
            OwnerTaskRequest(
                request_id="reasoning-turn", thread_id=thread.id, message="Call utc_time"
            ),
        )
        result = await terminal(service, c["owner"], task.task_id)
        assert result.state == "COMPLETED"
        assert result.response == "UTC tool completed"
        assert result.usage.model_attempts == 2 and result.usage.tool_attempts == 1
        assert len(requests) == 2
        assert all(json.loads(r.content)["reasoning_effort"] == "instant" for r in requests)
        assert not await c["audit"].has_pending_calls()
    finally:
        await service.stop()
