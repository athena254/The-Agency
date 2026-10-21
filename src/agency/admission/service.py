"""Durable single-owner gateway. No legacy lifecycle or provider fallback."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Coroutine
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, TypeVar, cast
from uuid import uuid4

from agency.admission.budget import OwnerBudgetAdapter
from agency.admission.models import (
    AdmissionRecord,
    MessageView,
    OwnerExecutionGrant,
    OwnerMeteredBinding,
    OwnerPrincipal,
    OwnerTaskRequest,
    OwnerTaskView,
    PilotCapabilities,
    PilotError,
    PilotLimits,
    PilotUsage,
    RequestLease,
    RequestReceipt,
    ThreadView,
)
from agency.admission.policy import OwnerToolPolicy
from agency.admission.store import OwnerPilotStore
from agency.agents.assessment import ExecutionAssessment, bound_assessment
from agency.butler.service import ButlerService
from agency.butler.threads import ThreadStore
from agency.kernel.audit import AuditEntry, BetaAuditLog
from agency.llm.adapter import LLMAdapter
from agency.llm.config import LLMConfig
from agency.llm.metered import MeteredDenied, MeteredError, MeteredModel
from agency.tools.builtin.calculator import CalculatorTool
from agency.tools.builtin.utc_time import UtcTimeTool
from agency.tools.builtin.web import WebSearchTool
from agency.tools.registry import ToolRegistry

_T = TypeVar("_T")
_State = Literal["ADMITTED", "RUNNING", "COMPLETED", "FAILED", "DENIED", "CANCELLED", "UNCERTAIN"]


class OwnerPilotService:
    def __init__(
        self,
        *,
        butler: ButlerService,
        store: OwnerPilotStore,
        budget: OwnerBudgetAdapter,
        audit: BetaAuditLog,
        provider_config: LLMConfig,
        owner_id: str,
        workspace_id: str = "",
    ) -> None:
        if (
            type(butler) is not ButlerService
            or type(store) is not OwnerPilotStore
            or type(budget) is not OwnerBudgetAdapter
            or type(audit) is not BetaAuditLog
            or type(provider_config) is not LLMConfig
            or budget._store is not store
        ):
            raise PilotError("invalid_request")
        if type(owner_id) is not str or not owner_id.strip() or len(owner_id) > 128:
            raise PilotError("invalid_request")
        if type(workspace_id) is not str or len(workspace_id) > 128:
            raise PilotError("invalid_request")
        threads = butler._threads
        if (
            type(threads) is not ThreadStore
            or butler._owns_threads
            or Path(threads.db_path).resolve() != Path(store._db_path).resolve()
        ):
            raise PilotError("invalid_request")
        try:
            self._dispatch = LLMAdapter(
                config=provider_config.model_copy(deep=True)
            ).freeze_dispatch()
        except (ValueError, TypeError):
            raise PilotError("invalid_request") from None
        self._butler, self._store, self._budget, self._audit = butler, store, budget, audit
        self._owner_id, self._workspace_id = owner_id, workspace_id
        self._config_id = uuid4().hex
        self._ready = False
        self._closed = False
        self._stopping = False
        self._blocked = False
        self._gate = asyncio.Lock()
        self._execution: asyncio.Task[BaseException | None] | None = None
        self._lease: RequestLease | None = None
        self._active_task_id: str | None = None
        self._operations: set[asyncio.Task[Any]] = set()

    async def _retain(self, operation: Coroutine[Any, Any, _T]) -> _T:
        task = asyncio.create_task(operation)
        self._operations.add(task)
        cancelled: asyncio.CancelledError | None = None
        try:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError as exc:
                    cancelled = exc
                except Exception:  # noqa: BLE001 -- storage/child failures must fail closed
                    break
            if cancelled is not None:
                if not task.cancelled():
                    task.exception()
                raise cancelled
            return task.result()
        finally:
            self._operations.discard(task)

    def _owner(self, principal: OwnerPrincipal) -> None:
        if type(principal) is not OwnerPrincipal or principal.owner_id != self._owner_id:
            raise PilotError("unauthorized")
        principal.__post_init__()
        if not self._ready:
            raise PilotError("disabled")

    async def _scope(self, principal: OwnerPrincipal) -> None:
        self._owner(principal)
        workspace = await self._store.ensure_workspace(principal)
        self._owner(principal)
        if self._workspace_id and self._workspace_id != workspace:
            raise PilotError("forbidden")
        self._workspace_id = workspace

    async def start(self) -> None:
        await self._retain(self._start())

    async def _start(self) -> None:
        async with self._gate:
            if self._closed or self._stopping:
                raise PilotError("disabled")
            if self._ready:
                return
            phase = "store"
            try:
                await self._store.initialize()
                await self._budget.initialize()
                phase = "audit"
                await self._audit.initialize()
                await self._audit.has_pending_calls()
                phase = "store"
                await self._store.recover_interrupted()
            except (Exception, asyncio.CancelledError) as exc:
                try:
                    await self._close_owned()
                except PilotError:
                    self._blocked = True
                if isinstance(exc, (PilotError, asyncio.CancelledError)):
                    raise
                raise PilotError(phase) from None
            self._ready = True

    async def _close_owned(self) -> None:
        self._ready = False
        self._closed = True
        error: Exception | None = None
        for close in (self._audit.close, self._budget.close, self._store.close):
            try:
                await close()
            except Exception as exc:  # noqa: BLE001 -- finish all owned cleanup
                error = error or exc
        if error is not None:
            raise PilotError("store", uncertain=True) from None

    async def stop(self) -> None:
        self._stopping = True  # Synchronous fence before any lifecycle await.
        if self._lease is not None:
            self._budget.revoke(self._lease)
        await self._retain(self._stop())

    async def _stop(self) -> None:
        async with self._gate:
            if self._closed:
                return
            if self._execution is not None:
                if self._lease is not None:
                    self._budget.revoke(self._lease)
                if not self._execution.done() and not self._execution.cancelling():
                    self._execution.cancel()
                await asyncio.gather(self._execution, return_exceptions=True)
                # Drain the actual task and inspect its typed result, not an
                # outer cancelled waiter. No pending audit is ever cleared.
                if not self._execution.cancelled():
                    self._execution.result()
            audit_failed = False
            if self._ready:
                try:
                    await self._audit.has_pending_calls()
                except Exception:  # noqa: BLE001 -- audit outage must not abandon cleanup
                    audit_failed = True
                    self._blocked = True
            await self._close_owned()
            if audit_failed:
                raise PilotError("audit", uncertain=True) from None

    @staticmethod
    def _view(row: AdmissionRecord) -> OwnerTaskView:
        result = json.loads(row.result_json) if row.state == "COMPLETED" and row.result_json else {}
        return OwnerTaskView(
            request_id=row.request_id,
            task_id=row.task_id or "",
            thread_id=row.thread_id or "",
            state=cast(_State, row.state),
            replayed=row.replayed,
            cancel_requested=row.cancel_requested,
            response=result.get("response"),
            assessment=ExecutionAssessment.model_validate(result["assessment"])
            if result.get("assessment")
            else None,
            error_code=row.error_code,
            uncertain=row.state == "UNCERTAIN",
            usage=PilotUsage(model_attempts=row.model_attempts, tool_attempts=row.tool_attempts),
            created_at=datetime.fromtimestamp(row.created_at, UTC).isoformat(),
            updated_at=datetime.fromtimestamp(row.updated_at, UTC).isoformat(),
        )

    async def _uncertainty(self, principal: OwnerPrincipal) -> bool:
        durable = await self._store.has_uncertainty(principal)
        try:
            pending = await self._audit.has_pending_calls()
        except Exception:  # noqa: BLE001 -- storage/child failures must fail closed
            raise PilotError("audit", uncertain=True) from None
        return durable or pending or self._blocked or self._stopping

    async def capabilities(self, principal: OwnerPrincipal) -> PilotCapabilities:
        await self._scope(principal)
        from dataclasses import asdict

        return PilotCapabilities(
            enabled=True,
            ready=True,
            mode="single_owner_pilot",
            tools=["calculator", "utc_time", "web_search"],
            disabled=["shell", "files", "web_fetch", "memory", "governance", "bridges", "agents"],
            limits=PilotLimits(**asdict(self._budget.limits), tool_calls_per_request=4),
            provider_ready=True,
            uncertainty_blocked=await self._uncertainty(principal),
        )

    async def list_tasks(
        self, principal: OwnerPrincipal, *, limit: int = 50
    ) -> list[OwnerTaskView]:
        await self._scope(principal)
        return [self._view(row) for row in await self._store.list_tasks(principal, limit=limit)]

    async def list_threads(self, principal: OwnerPrincipal) -> list[ThreadView]:
        await self._scope(principal)
        return await self._store.list_threads(principal)

    async def create_thread(
        self,
        principal: OwnerPrincipal,
        request_id: str,
        title: str,
        parent_thread_id: str | None = None,
    ) -> tuple[RequestReceipt, ThreadView]:
        await self._scope(principal)
        return await self._store.create_thread(principal, request_id, title, parent_thread_id)

    async def list_messages(self, principal: OwnerPrincipal, thread_id: str) -> list[MessageView]:
        await self._scope(principal)
        return await self._store.list_messages(principal, thread_id)

    async def get_task(self, principal: OwnerPrincipal, task_id: str) -> OwnerTaskView:
        await self._scope(principal)
        return self._view(await self._store.get(principal, task_id))

    async def cancel(
        self, principal: OwnerPrincipal, task_id: str, request_id: str
    ) -> OwnerTaskView:
        await self._scope(principal)
        principal = OwnerPrincipal(principal.owner_id, principal.transport, principal.subject_id)
        return await self._retain(self._cancel(principal, task_id, request_id))

    async def _cancel(
        self, principal: OwnerPrincipal, task_id: str, request_id: str
    ) -> OwnerTaskView:
        async with self._gate:
            await self._scope(principal)
            row = await self._store.request_cancel(principal, task_id, request_id)
            if task_id == self._active_task_id and self._execution is not None:
                if self._lease is not None:
                    self._budget.revoke(self._lease)
                if not self._execution.done():
                    self._execution.cancel()
                await asyncio.shield(self._execution)
            from dataclasses import replace

            return self._view(
                replace(await self._store.get(principal, task_id), replayed=row.replayed)
            )

    async def submit(self, principal: OwnerPrincipal, payload: OwnerTaskRequest) -> OwnerTaskView:
        self._owner(principal)
        if type(payload) is not OwnerTaskRequest:
            raise PilotError("invalid_request")
        snapshot = OwnerTaskRequest.model_validate(payload.model_dump())
        principal = OwnerPrincipal(principal.owner_id, principal.transport, principal.subject_id)
        return await self._retain(self._submit(principal, snapshot))

    async def _submit(self, principal: OwnerPrincipal, payload: OwnerTaskRequest) -> OwnerTaskView:
        values = payload.model_dump()
        async with self._gate:
            await self._scope(principal)
            if self._stopping:
                raise PilotError("disabled")
            try:
                existing = await self._store.get_request(principal, values["request_id"])
            except PilotError as exc:
                if exc.code != "not_found":
                    raise
            else:
                if existing.operation != "turn" or json.loads(existing.input_json) != {
                    "thread_id": values["thread_id"],
                    "message": values["message"],
                }:
                    raise PilotError("conflict")
                from dataclasses import replace

                return self._view(replace(existing, replayed=True))
            if self._execution is not None and not self._execution.done():
                raise PilotError("conflict")
            if await self._uncertainty(principal):
                raise PilotError("uncertainty", uncertain=True)
            row = await self._store.admit(
                principal,
                values["request_id"],
                operation="turn",
                payload={"thread_id": values["thread_id"], "message": values["message"]},
            )
            if not row.replayed:
                self._active_task_id = row.task_id
                self._execution = asyncio.create_task(self._execute(principal, row))
            return self._view(row)

    async def _execute(
        self, principal: OwnerPrincipal, row: AdmissionRecord
    ) -> BaseException | None:
        try:
            async with asyncio.timeout(max(0.0, row.created_at + 120.0 - time.time())):
                await self._perform(principal, row)
        except (Exception, asyncio.CancelledError) as exc:  # noqa: BLE001 -- retain real outcome
            try:
                # Always inspect durable audit even for forbidden/denied errors.
                try:
                    pending = await self._audit.has_pending_calls()
                except Exception:  # noqa: BLE001 -- storage/child failures must fail closed
                    pending = True
                    self._blocked = True
                current = await self._store.get(principal, row.task_id or "")
                if current.state in {"ADMITTED", "RUNNING"}:
                    uncertain = (
                        pending
                        or current.result_json is not None
                        or isinstance(exc, (MeteredError, asyncio.CancelledError))
                        or isinstance(exc, PilotError)
                        and exc.uncertain
                    )
                    if not uncertain and self._lease is not None and self._budget.owns(self._lease):
                        try:
                            await self._budget.finish_request(self._lease, success=False)
                        except Exception:  # noqa: BLE001 -- storage/child failures must fail closed
                            uncertain = True
                    code = (
                        "uncertainty"
                        if uncertain
                        else exc.code
                        if isinstance(exc, PilotError)
                        else "quota"
                        if isinstance(exc, MeteredDenied)
                        else "disabled"
                    )
                    state = (
                        "UNCERTAIN"
                        if uncertain
                        else "DENIED"
                        if current.state == "ADMITTED"
                        else "FAILED"
                    )
                    await self._store.transition(
                        row.sequence,
                        expected=current.state,
                        state=state,
                        phase="uncertain" if uncertain else "failed",
                        error_code=code,
                    )
            except (Exception, asyncio.CancelledError):  # noqa: BLE001 -- uncertainty cannot reopen dispatch
                # A failed disposition must block dispatch until explicit recovery.
                self._blocked = True
            return exc
        finally:
            if self._lease is not None:
                self._budget.revoke(self._lease)
        return None

    async def _perform(self, principal: OwnerPrincipal, row: AdmissionRecord) -> None:
        assert row.task_id is not None and row.thread_id is not None
        if self._stopping or await self._uncertainty(principal):
            raise PilotError("uncertainty", uncertain=True)
        lease = await self._budget.reserve_request(principal, row.request_id, row.task_id)
        self._lease = lease
        await self._budget.mark_running(lease)
        if await self._uncertainty(principal):
            raise PilotError("uncertainty", uncertain=True)
        grant = OwnerExecutionGrant(
            lease, self._budget, max_model_calls=self._budget.limits.model_calls_per_request
        )
        config = self._dispatch
        inner = LLMAdapter(
            provider=config.provider,
            model=config.model,
            api_key=config.api_key,
            base_url=config.base_url,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
            timeout=config.timeout,
        )
        model = MeteredModel(
            inner,
            OwnerMeteredBinding(grant, config.provider, config.model, self._config_id),
            self._budget,
            self._audit,
            timeout_s=config.timeout,
        )
        registry = ToolRegistry(owner_policy=OwnerToolPolicy(), audit=self._audit)
        registry.register(CalculatorTool())
        registry.register(UtcTimeTool())
        registry.register(WebSearchTool())
        payload = json.loads(row.input_json)
        reply = await self._butler.handle_owner_message(
            payload["message"], row.thread_id, grant=grant, model=model, registry=registry
        )
        report = bound_assessment(
            reply.assessment, task_id=row.task_id, execution_status=reply.status, output=reply.text
        )
        if reply.task_id != row.task_id or reply.status != "completed" or report is None:
            raise PilotError("conflict")
        await self._store.stage_result(
            row.sequence,
            {
                "task_id": row.task_id,
                "response": reply.text,
                "assessment": report.model_dump(mode="json"),
            },
        )
        entry = AuditEntry(
            agent="owner-gateway",
            task="owner-pilot",
            action="owner.gateway_result",
            result="completed",
            evidence={"phase": "gateway_result"},
        )
        if await self._audit.append_beta(entry) != entry.entry_id:
            raise PilotError("audit", uncertain=True)
        if await self._uncertainty(principal):
            raise PilotError("uncertainty", uncertain=True)
        await self._budget.finish_request(lease, success=True)
        if await self._uncertainty(principal):
            raise PilotError("uncertainty", uncertain=True)
        await self._store.commit_turn(row.sequence, text=reply.text, assessment=report)
