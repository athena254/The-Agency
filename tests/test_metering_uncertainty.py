"""Coordinator regression: unknown model attempts remain durably blocking."""

from __future__ import annotations

import asyncio

import pytest

from agency.kernel.audit import BetaAuditLog
from agency.llm.adapter import LLMAdapter
from agency.llm.metered import MeteredBinding, MeteredDenied, MeteredError, MeteredModel
from agency.telegram.budget_store import BetaBudgetStore
from agency.tools.base import BetaPrincipal


@pytest.mark.parametrize("failure", ["exception", "cancel", "empty"])
async def test_unknown_attempt_cannot_dispatch_again_after_audit_reopen(
    tmp_path, monkeypatch, failure
):
    store = BetaBudgetStore(str(tmp_path / "quota.db"))
    audit_path = str(tmp_path / "audit.db")
    audit = BetaAuditLog(audit_path)
    await store.initialize()
    await audit.initialize()
    try:
        request = await store.reserve_request("bot", 1, 7)
        assert request.allowed and request.capability
        await store.mark_running("bot", 1, 7, capability=request.capability)
        principal = BetaPrincipal(telegram_user_id=7, private_chat=True)
        inner = LLMAdapter(provider="echo", model="echo")
        binding = MeteredBinding("bot", 1, 7, request.capability, principal, "echo", "echo")
        context = {"beta_principal": principal}
        calls = []

        async def uncertain(self, prompt, context=None):
            calls.append(prompt)
            if failure == "exception":
                raise RuntimeError("private provider error")
            if failure == "cancel":
                raise asyncio.CancelledError()
            return ""

        monkeypatch.setattr(LLMAdapter, "generate", uncertain)
        model = MeteredModel(inner, binding, store, audit)
        with pytest.raises(MeteredError):
            await model.generate("synthetic", context)
        assert await audit.has_pending_calls() is True
        rows = await audit.query()
        assert any(row.result == "unknown" for row in rows)
        await audit.close()
        audit = BetaAuditLog(audit_path)
        await audit.initialize()
        assert await audit.has_pending_calls() is True
        model = MeteredModel(inner, binding, store, audit)
        with pytest.raises(MeteredDenied):
            await model.generate("synthetic retry", context)
        assert calls == ["synthetic"]
    finally:
        await store.close()
        await audit.close()


async def test_adapter_drift_during_reservation_never_dispatches(tmp_path, monkeypatch):
    from tests.test_model_metering import _close, _setup

    store, audit, principal, inner, _binding, model = await _setup(tmp_path)
    calls = []
    reserve = store.reserve_model_call

    async def drifting_reserve(*args, **kwargs):
        allowed = await reserve(*args, **kwargs)
        inner._provider = "unapproved-provider"
        return allowed

    async def counting(self, prompt, context=None):
        calls.append(prompt)
        return "must not dispatch"

    monkeypatch.setattr(store, "reserve_model_call", drifting_reserve)
    monkeypatch.setattr(LLMAdapter, "generate", counting)
    try:
        with pytest.raises(MeteredDenied):
            await model.generate("synthetic", {"beta_principal": principal})
        assert calls == []
        assert await audit.has_pending_calls() is True
    finally:
        await _close(store, audit)
