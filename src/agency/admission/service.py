"""Durable single-owner gateway. No legacy lifecycle or provider fallback."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, TypeVar, cast
from uuid import uuid4

from pydantic import SecretStr

from agency.admission.budget import OwnerBudgetAdapter
from agency.admission.calculator_verification import (
    admitted_expression,
    make_receipt,
    validate_receipt,
)
from agency.admission.coding_store import CodingEvidence, CodingRecord
from agency.admission.coding_store import model_snapshot as _model_snapshot
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
from agency.admission.owner_recovery import OwnerRecovery
from agency.admission.policy import OwnerToolPolicy
from agency.admission.spending import SpendingError, SpendingLedger
from agency.admission.store import OwnerPilotStore
from agency.agents.assessment import ExecutionAssessment, bound_assessment, output_digest
from agency.agents.factory_coding import (
    NativeCodingDenied,
    NativeCodingExecutor,
    NativeCodingHooks,
    NativeCodingOutcome,
    NativeCodingRequest,
    NativeCodingUncertain,
)
from agency.butler.service import ButlerService
from agency.butler.threads import ThreadStore
from agency.factory.coding_models import CodingBinding, CodingEdit, FactoryCodingRequest
from agency.kernel.audit import AuditEntry, BetaAuditLog
from agency.llm.adapter import LLMAdapter
from agency.llm.config import LLMConfig
from agency.llm.metered import MeteredDenied, MeteredError, MeteredModel
from agency.memory.sms.scope import PrincipalMemoryScope, PrincipalScope
from agency.memory.sms.store import MemoryStore
from agency.tools.builtin.calculator import CalculatorTool
from agency.tools.builtin.memory import MemoryQueryTool, MemoryWriteTool
from agency.tools.builtin.utc_time import UtcTimeTool
from agency.tools.builtin.web import WebFetchTool, WebSearchTool
from agency.tools.registry import ToolRegistry

_T = TypeVar("_T")
_State = Literal["ADMITTED", "RUNNING", "COMPLETED", "FAILED", "DENIED", "CANCELLED", "UNCERTAIN"]
_snapshot_model = cast(Callable[[type[_T], object], _T], _model_snapshot)


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
        spending: SpendingLedger | None = None,
        coding: NativeCodingExecutor | None = None,
        memory_db_path: str | None = None,
        enable_memory_tools: bool = False,
        enable_web_fetch: bool = False,
        recovery_key: SecretStr | None = None,
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
        if spending is not None and type(spending) is not SpendingLedger:
            raise PilotError("invalid_request")
        if coding is not None:
            if type(coding) is not NativeCodingExecutor:
                raise PilotError("invalid_request")
            try:
                profile = coding.profile
                profile.__post_init__()
                facts = {
                    k: getattr(self._dispatch, k)
                    for k in (
                        "provider",
                        "model",
                        "base_url",
                        "max_tokens",
                        "temperature",
                        "timeout",
                    )
                }
                if (
                    not workspace_id
                    or (owner_id, workspace_id) != (profile.owner_id, profile.workspace_id)
                    or (profile.provider, profile.model)
                    != (self._dispatch.provider, self._dispatch.model)
                    or json.loads(profile.dispatch_json) != facts
                    or not profile.provider_config_id.strip()
                ):
                    raise ValueError
            except (ValueError, TypeError, AttributeError):
                raise PilotError("invalid_request") from None
        if type(enable_memory_tools) is not bool or type(enable_web_fetch) is not bool:
            raise PilotError("invalid_request")
        if type(memory_db_path) is not str and memory_db_path is not None:
            raise PilotError("invalid_request")
        if enable_memory_tools and (memory_db_path is None or not memory_db_path.strip()):
            raise PilotError("invalid_request")
        if memory_db_path is not None and not enable_memory_tools:
            raise PilotError("invalid_request")
        self._enable_memory_tools = enable_memory_tools
        self._enable_web_fetch = enable_web_fetch
        self._memory_store = (
            MemoryStore(memory_db_path) if enable_memory_tools and memory_db_path else None
        )
        self._coding = coding
        self._coding_profile = coding.profile if coding is not None else None
        self._coding_sha = coding.profile.sha256 if coding is not None else None
        self._coding_uncertain: asyncio.Task[None] | None = None
        self._spending = spending
        if recovery_key is not None and (
            type(recovery_key) is not SecretStr or spending is not None
        ):
            raise PilotError("invalid_request")
        self._recovery_key = recovery_key
        self._recovery: OwnerRecovery | None = None
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
                if self._memory_store is not None:
                    await self._memory_store.initialize()
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
        # A cancelled waiter is not a stopped host thread/model/hook. Keep this
        # creating loop and all owned stores alive until actual settlement.
        if self._coding is not None:
            while not await self._coding.stop(drain_timeout_s=0.05):
                await asyncio.sleep(0.01)
        if self._coding_uncertain is not None:
            await asyncio.gather(self._coding_uncertain, return_exceptions=True)
        self._ready = False
        self._closed = True
        error: Exception | None = None
        closers: list[Any] = [self._audit.close, self._budget.close, self._store.close]
        if self._memory_store is not None:
            closers.append(self._memory_store.close)
        for close in closers:
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
        self._store.fence_publication()
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
        if self._recovery_key is not None:
            recovery = self._owner_recovery(principal)
            assert recovery is not None
            return await recovery.blocked() or self._blocked or self._stopping
        durable = await self._store.has_uncertainty(principal)
        try:
            pending = await self._audit.has_pending_calls()
        except Exception:  # noqa: BLE001 -- storage/child failures must fail closed
            raise PilotError("audit", uncertain=True) from None
        return durable or pending or self._blocked or self._stopping

    def _owner_recovery(self, principal: OwnerPrincipal) -> OwnerRecovery | None:
        if self._recovery_key is None:
            return None
        self._owner(principal)
        if self._recovery is None:
            self._recovery = OwnerRecovery(
                store=self._store,
                budget=self._budget,
                audit=self._audit,
                principal=principal,
                key=self._recovery_key,
                workspace_id=self._workspace_id,
                spending=self._spending,
            )
        if self._recovery._principal != principal:
            raise PilotError("forbidden")
        self._recovery._validate()
        return self._recovery

    async def spending(self, principal: OwnerPrincipal) -> dict[str, Any]:
        await self._scope(principal)
        if self._spending is not None:
            try:
                return self._spending.snapshot()
            except SpendingError:
                raise PilotError("store", uncertain=True) from None
        return {
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

    async def capabilities(self, principal: OwnerPrincipal) -> PilotCapabilities:
        await self._scope(principal)
        from dataclasses import asdict

        tools = ["calculator", "utc_time", "web_search"]
        disabled = ["shell", "files", "web_fetch", "memory", "governance", "bridges", "agents"]
        if self._enable_web_fetch:
            tools.append("web_fetch")
            disabled.remove("web_fetch")
        if self._enable_memory_tools:
            tools.extend(["memory_query", "memory_write"])
            disabled.remove("memory")
        return PilotCapabilities(
            enabled=True,
            ready=True,
            mode="single_owner_pilot",
            tools=tools,
            disabled=disabled,
            limits=PilotLimits(**asdict(self._budget.limits), tool_calls_per_request=4),
            provider_ready=True,
            uncertainty_blocked=await self._uncertainty(principal),
        )

    async def tool_status(self, principal: OwnerPrincipal) -> dict[str, Any]:
        """Honest per-tool availability and the reason a guard is off."""
        self._owner(principal)
        return {
            "memory_tools": {
                "enabled": self._enable_memory_tools,
                "guard": "R-04",
                "reason": "configured" if self._enable_memory_tools else "opt_in_disabled",
            },
            "web_fetch": {
                "enabled": self._enable_web_fetch,
                "guard": "B-05",
                "reason": "configured" if self._enable_web_fetch else "opt_in_disabled",
            },
        }

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

    async def get_verification(self, principal: OwnerPrincipal, task_id: str) -> dict[str, Any]:
        # Unlike _scope, this path never provisions a workspace or writes rows.
        self._owner(principal)
        row = await self._store.verification_record(principal, task_id, self._workspace_id)
        self._owner(principal)
        try:
            expression = admitted_expression(row.input_json, row.input_digest, row.thread_id or "")
            receipt = None
            if row.result_json is not None:
                if (
                    type(row.result_json) is not str
                    or len(row.result_json) > 524288
                    or hashlib.sha256(row.result_json.encode("utf-8")).hexdigest()
                    != row.result_digest
                ):
                    raise ValueError("result digest")
                result = json.loads(row.result_json)
                if (
                    type(result) is not dict
                    or set(result)
                    not in (
                        {"task_id", "response", "assessment"},
                        {"task_id", "response", "assessment", "verification"},
                    )
                    or result["task_id"] != row.task_id
                ):
                    raise ValueError("result identity")
                if type(result["response"]) is not str or not result["response"].strip():
                    raise ValueError("result response")
                if result["assessment"] is not None:
                    report = ExecutionAssessment.model_validate(result["assessment"])
                    if (
                        report.task_id != row.task_id
                        or report.execution_status != "completed"
                        or report.output_sha256 != output_digest(result["response"])
                    ):
                        raise ValueError("assessment binding")
                if "verification" in result:
                    validate_receipt(
                        result["verification"],
                        task_id=row.task_id or "",
                        request_id=row.request_id,
                        thread_id=row.thread_id or "",
                        expression=expression,
                        response=result["response"],
                    )
                    # Return the historical object, not a replacement checker issuance.
                    receipt = result["verification"]
            elif row.result_digest is not None or row.state == "COMPLETED":
                raise ValueError("missing result")
            if expression is None:
                reason = "not_requested"
            elif row.state != "COMPLETED":
                reason, receipt = "not_completed", None
            else:
                reason = "available" if receipt is not None else "legacy_result"
        except (ValueError, UnicodeError, RecursionError):
            raise PilotError("store") from None
        return {
            "task_id": row.task_id,
            "request_id": row.request_id,
            "thread_id": row.thread_id,
            "execution_state": row.state,
            "receipt": receipt,
            "reason": reason,
        }

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

    async def submit_coding(
        self, principal: OwnerPrincipal, payload: FactoryCodingRequest
    ) -> OwnerTaskView:
        if type(principal) is not OwnerPrincipal:
            raise PilotError("invalid_request")
        principal.__post_init__()
        self._owner(principal)
        snapshot = _snapshot_model(FactoryCodingRequest, payload)
        principal = OwnerPrincipal(principal.owner_id, principal.transport, principal.subject_id)
        return await self._retain(self._submit_coding(principal, snapshot))

    async def _submit_coding(
        self, principal: OwnerPrincipal, payload: FactoryCodingRequest
    ) -> OwnerTaskView:
        from dataclasses import replace

        async with self._gate:
            self._owner(principal)
            try:
                existing = await self._store.get_request(principal, payload.request_id)
            except PilotError as exc:
                if exc.code != "not_found":
                    raise
            else:
                if existing.operation != "turn" or existing.task_id is None:
                    raise PilotError("conflict")
                saved = await self._store.get_coding_record(
                    principal, existing.task_id or "", self._workspace_id
                )
                if saved is None or saved.request != payload:
                    raise PilotError("conflict")
                return self._view(replace(saved.admission, replayed=True))
            if self._coding is None or self._stopping:
                raise PilotError("disabled")
            if self._execution is not None and not self._execution.done():
                raise PilotError("conflict")
            if await self._uncertainty(principal):
                raise PilotError("uncertainty", uncertain=True)
            profile = self._coding_profile
            assert profile is not None and self._coding_sha is not None
            if (
                self._coding.profile is not profile
                or profile.sha256 != self._coding_sha
                or self._coding._claimed
                or self._coding._latched
                or self._coding._stopping
            ):
                raise PilotError("disabled")
            binding = CodingBinding(
                **{
                    k: getattr(profile, k)
                    for k in (
                        "owner_id",
                        "workspace_id",
                        "profile_id",
                        "blueprint_id",
                        "blueprint_version",
                        "runtime_agent_id",
                        "base_commit",
                        "checker_id",
                        "checker_version",
                        "checker_sha256",
                        "criteria_sha256",
                    )
                },
                profile_sha256=self._coding_sha,
                task_id=uuid4().hex,
                request_id=payload.request_id,
                thread_id=payload.thread_id,
            )
            try:
                self._coding._affinity()
                self._coding._live(binding)
            except NativeCodingDenied:
                raise PilotError("disabled") from None
            try:
                row = await self._store.admit_coding(
                    principal, payload, binding, profile_snapshot=profile.snapshot()
                )
            except PilotError as exc:
                if exc.uncertain:
                    self._blocked = True
                raise
            if not row.replayed:
                self._active_task_id = row.task_id
                self._coding_uncertain = None
                self._execution = asyncio.create_task(self._execute(principal, row))
            return self._view(row)

    async def get_coding_receipt(self, principal: OwnerPrincipal, task_id: str) -> dict[str, Any]:
        self._owner(principal)
        principal = OwnerPrincipal(principal.owner_id, principal.transport, principal.subject_id)
        row = await self._store.verification_record(principal, task_id, self._workspace_id)
        saved = await self._store.get_coding_record(principal, task_id, self._workspace_id)
        self._owner(principal)
        receipt = None
        if saved is None:
            reason = "not_requested"
        elif row.state != "COMPLETED":
            reason = "not_completed"
        elif saved.evidence_phase != "published" or saved.evidence is None:
            raise PilotError("store")
        else:
            reason = "available"
            receipt = saved.evidence.receipt.model_dump(mode="json")
        return {
            "task_id": row.task_id,
            "request_id": row.request_id,
            "thread_id": row.thread_id,
            "execution_state": row.state,
            "receipt": receipt,
            "reason": reason,
        }

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
                if existing.operation != "turn":
                    raise PilotError("conflict")
                if (
                    await self._store.get_coding_record(
                        principal, existing.task_id or "", self._workspace_id
                    )
                    is not None
                ):
                    raise PilotError("conflict")
                if json.loads(existing.input_json) != {
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
            if isinstance(exc, NativeCodingUncertain):
                self._blocked = True
            # Native gives its retained uncertainty callback one turn before
            # surfacing cancellation. Observe that exact ACK, not a racing root
            # transition, and never wait here for actual Git/model drain.
            native = self._coding
            if (
                native is not None
                and native._binding is not None
                and native._binding.task_id == row.task_id
                and native._uncertain_task is not None
            ):
                await asyncio.gather(native._uncertain_task, return_exceptions=True)
            if self._coding_uncertain is not None:
                await asyncio.gather(self._coding_uncertain, return_exceptions=True)
            try:
                # Always inspect durable audit even for forbidden/denied errors.
                try:
                    pending = (
                        await self._recovery.blocked()
                        if self._recovery is not None
                        else await self._audit.has_pending_calls()
                    )
                except Exception:  # noqa: BLE001 -- storage/child failures must fail closed
                    pending = True
                    self._blocked = True
                current = await self._store.get(principal, row.task_id or "")
                if current.state in {"ADMITTED", "RUNNING"}:
                    uncertain = (
                        pending
                        or current.result_json is not None
                        or isinstance(
                            exc, (MeteredError, asyncio.CancelledError, NativeCodingUncertain)
                        )
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

    async def _perform_coding(self, principal: OwnerPrincipal, saved: CodingRecord) -> None:
        from types import SimpleNamespace

        row, binding = saved.admission, saved.binding
        task_id = cast(str, row.task_id)
        executor = self._coding
        if executor is None:
            raise PilotError("disabled")
        if await self._uncertainty(principal):
            raise PilotError("uncertainty", uncertain=True)
        lease = await self._budget.reserve_request(principal, row.request_id, task_id)
        self._lease = lease
        await self._budget.mark_running(lease)
        grant = OwnerExecutionGrant(
            lease, self._budget, allowed_tools=frozenset(), max_tool_calls=1, max_model_calls=1
        )
        config = self._dispatch
        assert self._coding_profile is not None
        model = MeteredModel(
            LLMAdapter(
                provider=config.provider,
                model=config.model,
                api_key=config.api_key,
                base_url=config.base_url,
                max_tokens=config.max_tokens,
                temperature=config.temperature,
                timeout=config.timeout,
                proxy=config.proxy,
            ),
            OwnerMeteredBinding(
                grant, config.provider, config.model, self._coding_profile.provider_config_id
            ),
            self._budget,
            self._audit,
            timeout_s=config.timeout,
            spending=self._spending,
        )

        async def checkpoint(
            current_binding: CodingBinding,
            phase: Literal["planning", "mutation", "publication"],
            *,
            finalized: bool = False,
        ) -> None:
            current = await self._store.get_coding_record(principal, task_id, self._workspace_id)
            if (
                current is None
                or current.binding != binding
                or current.request != saved.request
                or current_binding != binding
            ):
                raise PilotError("conflict")
            live = current.admission
            if (
                self._stopping
                or live.cancel_requested
                or live.state != "RUNNING"
                or time.time() >= lease.deadline_at
                or not finalized
                and not self._budget.owns(lease)
            ):
                raise PilotError("uncertainty", uncertain=True)

        async def record_intent(
            current_binding: CodingBinding,
            edits: tuple[CodingEdit, ...],
            plan_sha256: str,
            branch: str,
        ) -> None:
            await checkpoint(current_binding, "mutation")
            await self._store.record_coding_intent(
                row.sequence, binding=binding, edits=edits, plan_sha256=plan_sha256, branch=branch
            )

        async def uncertain(
            current_binding: CodingBinding,
            phase: Literal["preflight", "planning", "mutation", "verification", "publication"],
            reason: Literal[
                "cancelled",
                "deadline",
                "stopping",
                "identity_changed",
                "model_unknown",
                "native_unknown",
                "evidence_invalid",
                "hook_unknown",
            ],
        ) -> None:
            self._blocked = True
            if current_binding != binding:
                raise PilotError("conflict", uncertain=True)
            if self._coding_uncertain is None:
                self._coding_uncertain = asyncio.create_task(
                    self._store.mark_coding_uncertain(
                        row.sequence, binding=binding, phase=phase, reason=reason
                    )
                )
            await asyncio.shield(self._coding_uncertain)

        async def publish(outcome: NativeCodingOutcome) -> None:
            await checkpoint(outcome.binding, "publication")
            evidence = CodingEvidence(
                edits=outcome.edits,
                forge_report=outcome.forge_report,
                artifact_text=outcome.artifact_text,
                patch=outcome.patch,
                receipt=outcome.receipt,
            )
            await self._store.stage_coding_result(row.sequence, binding=binding, evidence=evidence)
            passed = (
                evidence.receipt.objective_status == "passed"
                and evidence.forge_report.gate == "PASS"
            )
            entry = AuditEntry(
                agent="owner-gateway",
                task="owner-pilot",
                action="owner.gateway_result",
                result="completed" if passed else "failed",
                evidence={
                    "phase": "coding_result",
                    "objective_status": evidence.receipt.objective_status,
                },
            )
            if await self._audit.append_beta(entry) != entry.entry_id:
                raise PilotError("audit", uncertain=True)
            if await self._uncertainty(principal):
                raise PilotError("uncertainty", uncertain=True)
            await self._budget.finish_request(lease, success=passed)
            await checkpoint(binding, "publication", finalized=True)
            if await self._uncertainty(principal):
                raise PilotError("uncertainty", uncertain=True)
            if passed:
                await self._store.commit_coding_turn(row.sequence, binding=binding)
            else:
                await self._store.transition(
                    row.sequence,
                    expected="RUNNING",
                    state="FAILED",
                    phase="failed",
                    error_code="disabled",
                )

        hooks = SimpleNamespace(
            checkpoint=checkpoint, record_intent=record_intent, publish=publish, uncertain=uncertain
        )
        await self._butler.handle_owner_coding(
            NativeCodingRequest(saved.request, binding),
            grant=grant,
            model=model,
            executor=executor,
            hooks=cast(NativeCodingHooks, hooks),
        )

    async def _perform(self, principal: OwnerPrincipal, row: AdmissionRecord) -> None:
        assert row.task_id is not None and row.thread_id is not None
        coding = await self._store.get_coding_record(principal, row.task_id, self._workspace_id)
        if coding is not None:
            await self._perform_coding(principal, coding)
            return
        try:
            expression = admitted_expression(row.input_json, row.input_digest, row.thread_id)
        except (ValueError, UnicodeError, RecursionError):
            raise PilotError("store") from None
        if self._stopping or await self._uncertainty(principal):
            raise PilotError("uncertainty", uncertain=True)
        lease = await self._budget.reserve_request(principal, row.request_id, row.task_id)
        self._lease = lease
        await self._budget.mark_running(lease)
        if await self._uncertainty(principal):
            raise PilotError("uncertainty", uncertain=True)
        allowed_tools = (
            frozenset({"calculator"})
            if expression is not None
            else frozenset({"calculator", "utc_time", "web_search"})
        )
        if expression is None:
            if self._enable_web_fetch:
                allowed_tools = allowed_tools | {"web_fetch"}
            if self._enable_memory_tools:
                allowed_tools = allowed_tools | {"memory_query", "memory_write"}
        grant = OwnerExecutionGrant(
            lease,
            self._budget,
            max_model_calls=self._budget.limits.model_calls_per_request,
            allowed_tools=allowed_tools,
        )
        config = self._dispatch
        recovery = self._owner_recovery(principal)
        if recovery is not None:
            recovery = recovery.for_grant(grant)
        inner = LLMAdapter(
            provider=config.provider,
            model=config.model,
            api_key=config.api_key,
            base_url=config.base_url,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
            timeout=config.timeout,
            proxy=config.proxy,
            reasoning_effort=config.reasoning_effort,
        )
        model = MeteredModel(
            inner,
            OwnerMeteredBinding(grant, config.provider, config.model, self._config_id),
            self._budget,
            self._audit,
            timeout_s=config.timeout,
            spending=self._spending,
            owner_recovery=recovery,
        )
        memory_scope = None
        if self._enable_memory_tools:
            if self._memory_store is None:
                raise PilotError("disabled")
            principal_scope = PrincipalScope(
                owner_id=principal.owner_id,
                transport=principal.transport,
                subject_id=principal.subject_id,
            )
            memory_scope = PrincipalMemoryScope(self._memory_store, principal_scope)
        registry = ToolRegistry(
            owner_policy=OwnerToolPolicy(),
            audit=self._audit,
            owner_memory_scope=memory_scope,
            owner_recovery=recovery,
        )
        registry.register(CalculatorTool())
        registry.register(UtcTimeTool())
        registry.register(WebSearchTool())
        if self._enable_web_fetch:
            registry.register(WebFetchTool())
        if self._enable_memory_tools:
            registry.register(MemoryQueryTool(scoped=True))
            registry.register(MemoryWriteTool(scoped=True))
        payload = json.loads(row.input_json)
        reply = await self._butler.handle_owner_message(
            payload["message"], row.thread_id, grant=grant, model=model, registry=registry
        )
        report = bound_assessment(
            reply.assessment, task_id=row.task_id, execution_status=reply.status, output=reply.text
        )
        if reply.task_id != row.task_id or reply.status != "completed" or report is None:
            raise PilotError("conflict")
        verification = (
            None
            if expression is None
            else make_receipt(
                task_id=row.task_id,
                request_id=row.request_id,
                thread_id=row.thread_id,
                expression=expression,
                response=reply.text,
            ).model_dump(mode="json")
        )
        await self._store.stage_result(
            row.sequence,
            {
                "task_id": row.task_id,
                "response": reply.text,
                "assessment": report.model_dump(mode="json"),
                **({} if verification is None else {"verification": verification}),
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
        if verification is None:
            await self._store.commit_turn(row.sequence, text=reply.text, assessment=report)
        else:
            await self._store.commit_turn(
                row.sequence, text=reply.text, assessment=report, verification=verification
            )
