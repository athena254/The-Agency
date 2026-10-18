"""Strict ToolDriver admission must reject every non-concrete model."""

from __future__ import annotations

import pytest

from agency.kernel.audit import BetaAuditLog
from agency.llm.metered import MeteredModel
from agency.tools.base import BetaPrincipal
from agency.tools.driver import ToolDriver
from tests.test_tools_driver import FakeLLM, FakeRegistry, make_ctx


class UntrustedMeteredSubclass(MeteredModel):
    async def generate(self, prompt, context=None):
        self.dispatches += 1
        return '{"final": "unmetered success"}'


@pytest.mark.parametrize("with_audit", [False, True])
@pytest.mark.parametrize("model_kind", ["scripted", "subclass"])
async def test_strict_driver_refuses_nonconcrete_models_before_dispatch(
    tmp_path, with_audit, model_kind
):
    registry = FakeRegistry()
    registry._beta_policy = object()
    registry.add_spec("web_search")
    if model_kind == "scripted":
        model = FakeLLM(['{"final": "unmetered success"}'])
    else:
        model = object.__new__(UntrustedMeteredSubclass)
        model.dispatches = 0
    ctx = make_ctx()
    ctx.beta_principal = BetaPrincipal(telegram_user_id=123, private_chat=True)
    audit = BetaAuditLog(str(tmp_path / "audit.db")) if with_audit else None
    if audit is not None:
        await audit.initialize()
    ctx.audit = audit
    try:
        result = await ToolDriver(registry, model).run("synthetic", "sys", ctx)
        assert result.status == "tool_error"
        assert result.final_answer == "Beta tool unavailable."
        assert result.llm_calls == 0
        assert registry.calls == []
        if model_kind == "scripted":
            assert model.calls == []
        else:
            assert model.dispatches == 0
        if audit is not None:
            assert await audit.count() == 0
    finally:
        if audit is not None:
            await audit.close()
