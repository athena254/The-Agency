"""Vertical owner runtime: one admitted, bound model generation over a fake HTTP leaf."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from agency.admission.budget import OwnerBudgetAdapter
from agency.admission.models import OwnerExecutionGrant, OwnerMeteredBinding, OwnerPrincipal
from agency.admission.store import OwnerPilotStore
from agency.kernel.audit import BetaAuditLog
from agency.llm.adapter import LLMAdapter
from agency.llm.config import LLMConfig
from agency.llm.metered import MeteredModel
from agency.telegram.budget_store import BetaBudgetStore


async def test_admitted_owner_grant_reaches_one_exact_metered_http_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = OwnerPrincipal("owner", "local", "local")
    store = OwnerPilotStore(str(tmp_path / "pilot.db"))
    backend = BetaBudgetStore(str(tmp_path / "budget.db"))
    budget = OwnerBudgetAdapter(backend, store)
    audit = BetaAuditLog(str(tmp_path / "owner-model-audit.db"))
    original_async_client = httpx.AsyncClient
    options: list[dict[str, Any]] = []
    requests: list[httpx.Request] = []
    prompt = "synthetic owner prompt"
    answer = "synthetic owner model answer"

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": answer}}]})

    def client_factory(**kwargs: Any) -> httpx.AsyncClient:
        options.append(dict(kwargs))
        return original_async_client(transport=httpx.MockTransport(handle), **kwargs)

    try:
        await store.initialize()
        await backend.initialize()
        await audit.initialize()
        _, thread = await store.create_thread(owner, "owner-thread", "Owner runtime")
        admitted = await store.admit(
            owner,
            "owner-turn",
            operation="turn",
            payload={"thread_id": thread.id, "message": prompt},
        )
        assert admitted.task_id is not None
        lease = await budget.reserve_request(owner, admitted.request_id, admitted.task_id)
        await budget.mark_running(lease)
        grant = OwnerExecutionGrant(lease, budget)
        inner = LLMAdapter(
            config=LLMConfig(
                provider="openai",
                model="synthetic-owner-model",
                base_url="https://provider.invalid/v1",
                api_key=SecretStr("SYNTHETIC_TEST_ONLY"),
                max_tokens=64,
                temperature=0.0,
                timeout=10.0,
            )
        )
        binding = OwnerMeteredBinding(
            grant, "openai", "synthetic-owner-model", "synthetic-owner-config"
        )
        monkeypatch.setattr(httpx, "AsyncClient", client_factory)
        model = MeteredModel(inner, binding, budget, audit)
        assert await model.generate(prompt, {"owner_grant": grant}) == answer

        assert len(requests) == 1 and len(options) == 1
        request = requests[0]
        assert request.method == "POST"
        assert str(request.url) == "https://provider.invalid/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer SYNTHETIC_TEST_ONLY"
        assert json.loads(request.content) == {
            "model": "synthetic-owner-model",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 64,
            "temperature": 0.0,
        }
        assert request.extensions["timeout"] == {
            "connect": 10.0,
            "read": 10.0,
            "write": 10.0,
            "pool": 10.0,
        }
        assert options == [{"timeout": 10.0, "trust_env": False, "follow_redirects": False}]

        current = await store.get(owner, admitted.task_id)
        assert current.state == "RUNNING"
        assert current.model_attempts == 1

        entries = await audit.query()
        phases = [
            (entry.result, entry.evidence["phase"], entry.evidence["call_id"])
            for entry in entries
            if entry.action == "model.generate" and entry.evidence is not None
        ]
        assert len(phases) == 2
        assert {(result, phase) for result, phase, _ in phases} == {
            ("pending", "intent"),
            ("ok", "outcome"),
        }
        assert len({call_id for _, _, call_id in phases}) == 1
        assert await audit.has_pending_calls() is False
        assert await store.list_messages(owner, thread.id) == []
    finally:
        await audit.close()
        await budget.close()
        await backend.close()
        await store.close()
