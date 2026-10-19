"""Offline request-bound model leaf regressions; fakes stay below MeteredModel."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from structlog.testing import capture_logs

from agency.agents.executor import AgentExecutor, ExecutionContext, ExecutionResult, ExecutionStatus
from agency.kernel.audit import AuditEntry, AuditLog, BetaAuditLog
from agency.llm.adapter import LLMAdapter
from agency.llm.metered import MeteredBinding, MeteredDenied, MeteredError, MeteredModel
from agency.telegram.budget_store import BetaBudgetStore, BudgetLimits
from agency.tools.base import BetaPrincipal, BetaToolPolicy, ToolContext, ToolResult, ToolSpec
from agency.tools.driver import ToolDriver, ToolLoopResult
from agency.tools.registry import ToolRegistry


@dataclass
class RequestLeaf:
    store: BetaBudgetStore
    audit: BetaAuditLog
    principal: BetaPrincipal
    inner: LLMAdapter
    model: MeteredModel
    reservations: list[object] = field(default_factory=list)
    dispatches: list[str] = field(default_factory=list)
    provider_error: bool = False
    provider_error_at: int | None = None
    responses: list[str] = field(default_factory=list)


@pytest_asyncio.fixture
async def leaf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[RequestLeaf]:
    store = BetaBudgetStore(
        str(tmp_path / "quota.db"), limits=BudgetLimits(model_calls_per_request=4)
    )
    audit = BetaAuditLog(str(tmp_path / "model-audit.db"))
    await store.initialize()
    try:
        await audit.initialize()
        reservation = await store.reserve_request("synthetic-leaf", 1, 7)
        assert reservation.allowed and reservation.capability
        await store.mark_running("synthetic-leaf", 1, 7, capability=reservation.capability)
        principal = BetaPrincipal(telegram_user_id=7, private_chat=True)
        inner = LLMAdapter(provider="echo", model="echo")
        binding = MeteredBinding(
            bot_id="synthetic-leaf",
            update_id=1,
            user_id=7,
            capability=reservation.capability,
            principal=principal,
            provider=inner.provider,
            model=inner.model,
        )
        request = RequestLeaf(
            store, audit, principal, inner, MeteredModel(inner, binding, store, audit)
        )
        reserve = store.reserve_model_call

        async def record_reservation(*args: Any, **kwargs: Any) -> bool:
            request.reservations.append(args)
            return await reserve(*args, **kwargs)

        def provider(prompt: str) -> str:
            request.dispatches.append(prompt)
            if request.provider_error or request.provider_error_at == len(request.dispatches):
                raise OSError("SYNTHETIC_PRIVATE_PROVIDER_ERROR")
            if request.responses:
                return request.responses.pop(0)
            return '{"final": "authorized answer"}'

        monkeypatch.setattr(store, "reserve_model_call", record_reservation)
        monkeypatch.setattr(inner, "_echo_generate", provider)
        yield request
    finally:
        await audit.close()
        await store.close()


@pytest.mark.parametrize("failure", ["quota", "provider"])
@pytest.mark.parametrize("bound_method", [False, True])
async def test_executor_preserves_model_blocker_without_retry(
    leaf: RequestLeaf, failure: str, bound_method: bool
) -> None:
    if failure == "quota":
        binding = leaf.model.binding
        for _ in range(4):
            assert await leaf.store.reserve_model_call(
                binding.bot_id, binding.update_id, binding.user_id, capability=binding.capability
            )
        leaf.reservations.clear()
    else:
        leaf.provider_error = True
    executor = AgentExecutor(llm=leaf.model.generate if bound_method else leaf.model)
    error = MeteredDenied if failure == "quota" else MeteredError
    with capture_logs() as logs, pytest.raises(error):
        await executor.execute(
            "SYNTHETIC_PRIVATE_PROMPT",
            {
                "max_retries": 3,
                "backoff_base_s": 0.001,
                "metadata": {"beta_principal": leaf.principal},
            },
            task_id="leaf-task",
        )
    assert executor.get_status("leaf-task") is ExecutionStatus.FAILED
    assert not executor.cancel("leaf-task")
    assert len(leaf.reservations) == 1
    assert len(leaf.dispatches) == int(failure == "provider")
    assert await leaf.audit.has_pending_calls() is (failure == "provider")
    assert not any(row["event"] == "executor.retry" for row in logs)
    assert [row["attempt"] for row in logs if row["event"] == "executor.metered_blocked"] == [1]
    assert executor._running == {}
    assert all("SYNTHETIC_PRIVATE" not in repr(row) for row in logs)


class OfflineSearch:
    spec = ToolSpec(name="web_search", description="Synthetic offline search")

    def __init__(self) -> None:
        self.calls = 0

    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        self.calls += 1
        return ToolResult(tool="web_search", ok=True, output="synthetic result")


def make_driver(leaf: RequestLeaf) -> tuple[ToolDriver, ToolContext, OfflineSearch]:
    registry = ToolRegistry(audit=leaf.audit, beta_policy=BetaToolPolicy())
    search = OfflineSearch()
    registry.register(search)
    ctx = ToolContext(
        agent_id="synthetic-agent",
        task_id="leaf-task",
        audit=leaf.audit,
        beta_principal=leaf.principal,
    )
    return ToolDriver(registry, leaf.model, max_tool_iterations=1), ctx, search


@pytest.mark.parametrize("forced", [False, True])
@pytest.mark.parametrize("failure", ["quota", "provider"])
async def test_driver_preserves_model_blocker_including_forced_final(
    leaf: RequestLeaf, failure: str, forced: bool
) -> None:
    if failure == "quota":
        binding = leaf.model.binding
        for _ in range(3 if forced else 4):
            assert await leaf.store.reserve_model_call(
                binding.bot_id, binding.update_id, binding.user_id, capability=binding.capability
            )
        leaf.reservations.clear()
    else:
        leaf.provider_error_at = 2 if forced else 1
    if forced:
        leaf.responses.append('{"action": {"name": "web_search", "args": {"query": "fixture"}}}')
    driver, ctx, tool = make_driver(leaf)
    error = MeteredDenied if failure == "quota" else MeteredError
    with pytest.raises(error):
        await driver.run("SYNTHETIC_PRIVATE_PROMPT", "sys", ctx)
    assert len(leaf.reservations) == (2 if forced else 1)
    assert len(leaf.dispatches) == int(forced) + int(failure == "provider")
    assert tool.calls == int(forced)
    assert await leaf.audit.has_pending_calls() is (failure == "provider")


@pytest.mark.parametrize("consumer", ["executor", "driver", "forced"])
@pytest.mark.parametrize("provider_fails", [False, True])
@pytest.mark.parametrize("audit_error", [OSError, ValueError, TimeoutError])
async def test_post_dispatch_audit_failure_is_typed_unknown_without_retry(
    leaf: RequestLeaf,
    monkeypatch: pytest.MonkeyPatch,
    consumer: str,
    provider_fails: bool,
    audit_error: type[Exception],
) -> None:
    expected_calls = 2 if consumer == "forced" else 1
    if provider_fails:
        leaf.provider_error_at = expected_calls
    if consumer == "forced":
        leaf.responses.append('{"action": {"name": "web_search", "args": {"query": "fixture"}}}')
    append = leaf.audit.append_beta

    async def fail_model_outcome(entry: AuditEntry) -> str:
        if entry.action == "model.generate" and len(leaf.dispatches) == expected_calls:
            raise audit_error("SYNTHETIC_PRIVATE_AUDIT_ERROR")
        return await append(entry)

    monkeypatch.setattr(leaf.audit, "append_beta", fail_model_outcome)
    with capture_logs() as logs, pytest.raises(MeteredError, match="^metered: model unavailable$"):
        if consumer == "executor":
            executor = AgentExecutor(llm=leaf.model.generate)
            await executor.execute(
                "private input",
                {
                    "max_retries": 3,
                    "backoff_base_s": 0.001,
                    "metadata": {"beta_principal": leaf.principal},
                },
                task_id="leaf-task",
            )
        else:
            driver, ctx, tool = make_driver(leaf)
            await driver.run("private input", "sys", ctx)
    if consumer == "executor":
        assert executor.get_status("leaf-task") is ExecutionStatus.FAILED
        assert not executor.cancel("leaf-task")
    else:
        assert tool.calls == int(consumer == "forced")
    assert len(leaf.dispatches) == expected_calls
    assert len(leaf.reservations) == expected_calls
    assert await leaf.audit.has_pending_calls() is True
    assert not any(row["event"] == "executor.retry" for row in logs)
    assert all("SYNTHETIC_PRIVATE" not in repr(row) for row in logs)


@pytest.mark.parametrize("cancel", [False, True])
async def test_executor_outcome_ack_interruption_preserves_unknown_without_retry(
    leaf: RequestLeaf, monkeypatch: pytest.MonkeyPatch, cancel: bool
) -> None:
    acknowledging = asyncio.Event()

    async def blocked_outcome(entry: AuditEntry) -> str:
        acknowledging.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    monkeypatch.setattr(leaf.audit, "append_beta", blocked_outcome)
    executor = AgentExecutor(llm=leaf.model.generate)
    task = asyncio.create_task(
        executor.execute(
            "fixture",
            {
                "timeout_s": 5 if cancel else 2,
                "max_retries": 3,
                "backoff_base_s": 0.001,
                "metadata": {"beta_principal": leaf.principal},
            },
            task_id="leaf-task",
        )
    )
    try:
        await asyncio.wait_for(acknowledging.wait(), timeout=5)
        if cancel:
            assert executor.cancel("leaf-task")
        with pytest.raises(MeteredError, match="^metered: model unavailable$"):
            await task
        assert executor.get_status("leaf-task") is ExecutionStatus.FAILED
        assert len(leaf.reservations) == len(leaf.dispatches) == 1
        assert await leaf.audit.has_pending_calls() is True
        assert executor._running == {}
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("forced", [False, True])
async def test_driver_outcome_ack_cancellation_preserves_unknown(
    leaf: RequestLeaf, monkeypatch: pytest.MonkeyPatch, forced: bool
) -> None:
    expected_calls = 2 if forced else 1
    if forced:
        leaf.responses.append('{"action": {"name": "web_search", "args": {"query": "fixture"}}}')
    acknowledging = asyncio.Event()
    append = leaf.audit.append_beta

    async def blocked_outcome(entry: AuditEntry) -> str:
        if entry.action == "model.generate" and len(leaf.dispatches) == expected_calls:
            acknowledging.set()
            await asyncio.Event().wait()
        return await append(entry)

    monkeypatch.setattr(leaf.audit, "append_beta", blocked_outcome)
    driver, ctx, tool = make_driver(leaf)
    task = asyncio.create_task(driver.run("fixture", "sys", ctx))
    try:
        await asyncio.wait_for(acknowledging.wait(), timeout=5)
        task.cancel()
        with pytest.raises(MeteredError, match="^metered: model unavailable$"):
            await task
        assert len(leaf.reservations) == len(leaf.dispatches) == expected_calls
        assert tool.calls == int(forced)
        assert await leaf.audit.has_pending_calls() is True
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("consumer", ["executor", "driver", "forced"])
async def test_authorized_final_answers_still_cross_real_metering(
    leaf: RequestLeaf, consumer: str
) -> None:
    if consumer == "executor":
        executor = AgentExecutor(llm=leaf.model.generate)
        result = await executor.execute(
            "fixture", {"metadata": {"beta_principal": leaf.principal}}, task_id="leaf-task"
        )
        assert result.status is ExecutionStatus.COMPLETED
        assert result.attempts == 1
        assert result.output == '{"final": "authorized answer"}'
    else:
        if consumer == "forced":
            leaf.responses.append(
                '{"action": {"name": "web_search", "args": {"query": "fixture"}}}'
            )
        driver, ctx, tool = make_driver(leaf)
        result = await driver.run("fixture", "sys", ctx)
        assert result.status == "completed"
        assert result.final_answer == "authorized answer"
        assert result.llm_calls == (2 if consumer == "forced" else 1)
        assert tool.calls == int(consumer == "forced")
    assert len(leaf.dispatches) == len(leaf.reservations) == (2 if consumer == "forced" else 1)
    assert await leaf.audit.has_pending_calls() is False


@pytest.mark.parametrize("consumer", ["executor", "driver"])
@pytest.mark.parametrize("failure", ["budget", "audit", "claim"])
async def test_pre_dispatch_store_failure_preserves_denial_with_zero_provider_calls(
    leaf: RequestLeaf, monkeypatch: pytest.MonkeyPatch, consumer: str, failure: str
) -> None:
    if failure == "budget":
        await leaf.store.close()
    elif failure == "audit":
        await leaf.audit.close()
    else:

        async def fail_claim(entry: AuditEntry) -> bool:
            raise OSError("SYNTHETIC_PRIVATE_INTENT_ERROR")

        monkeypatch.setattr(leaf.audit, "claim_intent", fail_claim)
    with capture_logs() as logs, pytest.raises(MeteredDenied):
        if consumer == "executor":
            executor = AgentExecutor(llm=leaf.model.generate)
            await executor.execute(
                "fixture", {"metadata": {"beta_principal": leaf.principal}}, task_id="leaf-task"
            )
        else:
            driver, ctx, tool = make_driver(leaf)
            await driver.run("fixture", "sys", ctx)
    if consumer == "executor":
        assert executor.get_status("leaf-task") is ExecutionStatus.FAILED
    else:
        assert tool.calls == 0
    assert len(leaf.reservations) == 1
    assert leaf.dispatches == []
    assert not any(row["event"] == "executor.retry" for row in logs)
    assert all("SYNTHETIC_PRIVATE" not in repr(row) for row in logs)


@pytest.mark.parametrize("consumer", ["executor", "driver", "forced"])
async def test_timeout_keeps_uncertain_intent_blocking_after_audit_reopen(
    leaf: RequestLeaf, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, consumer: str
) -> None:
    leaf.model = MeteredModel(
        leaf.inner, leaf.model.binding, leaf.store, leaf.audit, timeout_s=0.01
    )

    async def timed_provider(prompt: str, context: dict[str, Any] | None = None) -> str:
        leaf.dispatches.append(prompt)
        if consumer == "forced" and len(leaf.dispatches) == 1:
            return '{"action": {"name": "web_search", "args": {"query": "fixture"}}}'
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    # Async provider simulation below the unchanged concrete request wrapper.
    monkeypatch.setattr(leaf.inner, "generate", timed_provider)
    with pytest.raises(MeteredError):
        if consumer == "executor":
            executor = AgentExecutor(llm=leaf.model.generate)
            await executor.execute(
                "fixture",
                {"max_retries": 3, "metadata": {"beta_principal": leaf.principal}},
                task_id="leaf-task",
            )
        else:
            driver, ctx, _tool = make_driver(leaf)
            await driver.run("fixture", "sys", ctx)
    expected_calls = 2 if consumer == "forced" else 1
    assert len(leaf.dispatches) == len(leaf.reservations) == expected_calls
    assert await leaf.audit.has_pending_calls() is True
    await leaf.audit.close()
    reopened = BetaAuditLog(str(tmp_path / "model-audit.db"))
    await reopened.initialize()
    try:
        assert await reopened.has_pending_calls() is True
        rebound = MeteredModel(leaf.inner, leaf.model.binding, leaf.store, reopened)
        with pytest.raises(MeteredDenied):
            await AgentExecutor(llm=rebound.generate).execute(
                "explicit replay", {"metadata": {"beta_principal": leaf.principal}}
            )
        assert len(leaf.dispatches) == expected_calls
        assert len(leaf.reservations) == expected_calls + 1
        assert await reopened.has_pending_calls() is True
    finally:
        await reopened.close()


@pytest.mark.parametrize("consumer", ["executor", "isolated", "driver", "forced"])
@pytest.mark.parametrize("failure", ["quota", "provider", "outcome"])
async def test_real_gateway_receives_leaf_blocker_without_fallback_success(
    leaf: RequestLeaf, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, consumer: str, failure: str
) -> None:
    from agency.butler.config import ButlerConfig
    from agency.butler.service import ButlerService
    from agency.orchestrator import AgencyOrchestrator
    from agency.security.sandbox.manager import SandboxManager

    # Synthetic trusted in-process binding only: no beta/local ingress is enabled.
    class BoundExecutor(AgentExecutor):
        async def execute(
            self,
            task: str | dict[str, Any],
            context: ExecutionContext | dict[str, Any] | None = None,
            *,
            task_id: str | None = None,
        ) -> ExecutionResult:
            ctx = self._coerce_context(context)
            ctx.metadata = {**ctx.metadata, "beta_principal": leaf.principal}
            return await super().execute(task, ctx, task_id=task_id)

    class BoundDriver(ToolDriver):
        async def run(
            self,
            task: str,
            system_prompt: str,
            ctx: ToolContext,
            max_tool_iterations: int | None = None,
        ) -> ToolLoopResult:
            return await super().run(
                task,
                system_prompt,
                replace(ctx, audit=leaf.audit, beta_principal=leaf.principal),
                max_tool_iterations=1 if consumer == "forced" else max_tool_iterations,
            )

    expected_dispatches = int(consumer == "forced") + int(failure != "quota")
    if failure == "quota":
        binding = leaf.model.binding
        for _ in range(3 if consumer == "forced" else 4):
            assert await leaf.store.reserve_model_call(
                binding.bot_id, binding.update_id, binding.user_id, capability=binding.capability
            )
        leaf.reservations.clear()
    elif failure == "provider":
        leaf.provider_error_at = expected_dispatches
    else:
        append = leaf.audit.append_beta

        async def fail_outcome(entry: AuditEntry) -> str:
            if entry.action == "model.generate" and len(leaf.dispatches) == expected_dispatches:
                raise OSError("SYNTHETIC_PRIVATE_OUTCOME_ERROR")
            return await append(entry)

        monkeypatch.setattr(leaf.audit, "append_beta", fail_outcome)
    if consumer == "forced":
        leaf.responses.append('{"action": {"name": "web_search", "args": {"query": "fixture"}}}')
    monkeypatch.setenv("LATTICE_SQLITE_PATH", str(tmp_path / "gateway-lattice.db"))
    orchestrator = AgencyOrchestrator(llm=leaf.inner, memory_db_path=str(tmp_path / "memory.db"))
    orchestrator._sandbox_manager = SandboxManager(base_workspace_dir=tmp_path / "sandbox")
    gateway_audit = AuditLog(tmp_path / "gateway-audit.db")
    service = ButlerService(
        config=ButlerConfig(llm_provider="none"), orchestrator=orchestrator, audit_log=gateway_audit
    )
    try:
        await service.start()
        executor = BoundExecutor(llm=leaf.model.generate)
        orchestrator._executor = executor
        driver, _ctx, tool = make_driver(leaf)
        orchestrator._tool_driver = BoundDriver(driver._registry, leaf.model)
        error = MeteredDenied if failure == "quota" else MeteredError
        with capture_logs() as logs, pytest.raises(error):
            if consumer == "isolated":
                await service.handle_isolated_message("fixture", "synthetic-owner")
            else:
                await service.handle_message(
                    "security fixture" if consumer == "executor" else "fixture",
                    "synthetic-owner",
                    {},
                    memory_enabled=False,
                )
        tasks = await orchestrator.list_tasks()
        assert len(tasks) == int(consumer != "isolated")
        assert all(str(task.status.value) == "failed" for task in tasks)
        assert len(leaf.dispatches) == expected_dispatches
        assert len(leaf.reservations) == (2 if consumer == "forced" else 1)
        assert tool.calls == int(consumer == "forced")
        assert await leaf.audit.has_pending_calls() is (failure != "quota")
        rows = await gateway_audit.query()
        assert not any(
            row.action in {"butler.handle_message", "butler.handle_isolated_message"}
            for row in rows
        )
        assert not any(row["event"] == "executor.retry" or row.get("exc_info") for row in logs)
        assert all("SYNTHETIC_PRIVATE" not in repr(row) for row in logs)
    finally:
        await service.stop()
        await gateway_audit.close()
