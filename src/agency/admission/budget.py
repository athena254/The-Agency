"""Reuse the concrete quota ledger with private, platform-neutral integer keys.

The ledger's historical argument names are not Telegram identities. No principal
is fabricated, and the owner store and quota store are not one transaction.
"""

from __future__ import annotations

import asyncio
import secrets
import sqlite3
import time
from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from agency.admission.models import AdmissionRecord, OwnerPrincipal, PilotError, RequestLease
from agency.admission.store import OwnerPilotStore
from agency.telegram.budget_store import BetaBudgetStore, BudgetUnavailable, Reservation

_T = TypeVar("_T")


@dataclass
class _Owned:
    lease: RequestLease
    snapshot: tuple[Any, ...] = field(repr=False)
    backend_capability: str = field(repr=False)
    state: str = "RESERVED"


def _snapshot(lease: RequestLease) -> tuple[Any, ...]:
    return (
        lease.principal.owner_id,
        lease.principal.transport,
        lease.principal.subject_id,
        lease.request_id,
        lease.task_id,
        lease.sequence,
        lease.quota_subject_id,
        lease.capability,
        lease.deadline_at,
    )


class OwnerBudgetAdapter:
    def __init__(
        self, backend: BetaBudgetStore, store: OwnerPilotStore, *, namespace: str = "owner-pilot:v1"
    ) -> None:
        if type(backend) is not BetaBudgetStore or type(store) is not OwnerPilotStore:
            raise PilotError("invalid_request")
        if type(namespace) is not str or not namespace.strip() or len(namespace) > 128:
            raise PilotError("invalid_request")
        self._backend = backend
        self._store = store
        self._namespace = namespace
        self._owned: dict[int, _Owned] = {}
        self._closed = False
        self._gate = asyncio.Lock()

    async def _wait(self, operation: Awaitable[_T]) -> _T:
        task = asyncio.ensure_future(operation)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            drain = asyncio.gather(task, return_exceptions=True)
            while not drain.done():
                try:
                    await asyncio.shield(drain)
                except asyncio.CancelledError:
                    continue
            if not task.cancelled():
                task.exception()
            raise
        except PilotError:
            raise
        except (BudgetUnavailable, OSError, sqlite3.Error, ValueError, TypeError, RuntimeError):
            raise PilotError("store", uncertain=True) from None

    def owns(self, lease: RequestLease) -> bool:
        if (
            type(lease) is not RequestLease
            or self._closed
            or not self._store._ready
            or self._store._closed
        ):
            return False
        try:
            lease.__post_init__()
            if lease.sequence in self._store._blocked_sequences:
                return False
            owned = self._owned.get(lease.sequence)
            return (
                owned is not None
                and owned.lease is lease
                and owned.snapshot == _snapshot(lease)
                and owned.state in ("RESERVED", "RUNNING")
                and time.time() < lease.deadline_at
            )
        except (PilotError, AttributeError, TypeError):
            return False

    def revoke(self, lease: RequestLease) -> None:
        if type(lease) is RequestLease:
            for sequence, owned in tuple(self._owned.items()):
                if owned.lease is lease:
                    self._owned.pop(sequence, None)
                    return

    def _require(self, lease: RequestLease) -> _Owned:
        if not self.owns(lease):
            raise PilotError("forbidden")
        return self._owned[lease.sequence]

    async def _current(self, lease: RequestLease, state: str) -> AdmissionRecord:
        self._require(lease)
        row = await self._wait(self._store.get(lease.principal, lease.task_id))
        self._require(lease)
        if (
            row.sequence != lease.sequence
            or row.request_id != lease.request_id
            or row.owner_id != lease.principal.owner_id
            or row.created_at + 120 != lease.deadline_at
            or row.state != state
            or row.cancel_requested
        ):
            self.revoke(lease)
            raise PilotError("conflict")
        return row

    async def reserve_request(
        self, principal: OwnerPrincipal, request_id: str, task_id: str
    ) -> RequestLease:
        async with self._gate:
            if self._closed:
                raise PilotError("disabled")
            row, quota = await self._wait(self._store._claim(principal, request_id, task_id))
            reservation = await self._wait(
                self._backend.reserve_request(self._namespace, row.sequence, quota)
            )
            if (
                type(reservation) is not Reservation
                or type(reservation.allowed) is not bool
                or type(reservation.duplicate) is not bool
            ):
                raise PilotError("store", uncertain=True)
            if reservation.duplicate:
                raise PilotError("conflict")
            if not reservation.allowed:
                raise PilotError("quota")
            if (
                reservation.state != "RESERVED"
                or type(reservation.capability) is not str
                or not reservation.capability
            ):
                raise PilotError("store", uncertain=True)
            lease = RequestLease(
                principal,
                request_id,
                task_id,
                row.sequence,
                quota,
                secrets.token_urlsafe(32),
                row.created_at + 120,
            )
            self._owned[row.sequence] = _Owned(lease, _snapshot(lease), reservation.capability)
            try:
                await self._current(lease, "ADMITTED")
            except BaseException:
                self.revoke(lease)
                raise
            return lease

    async def mark_running(self, lease: RequestLease) -> None:
        async with self._gate:
            owned = self._require(lease)
            if owned.state != "RESERVED":
                raise PilotError("conflict")
            try:
                await self._current(lease, "ADMITTED")
                await self._wait(
                    self._backend.mark_running(
                        self._namespace,
                        lease.sequence,
                        lease.quota_subject_id,
                        capability=owned.backend_capability,
                    )
                )
                self._require(lease)
                await self._wait(
                    self._store.transition(
                        lease.sequence, expected="ADMITTED", state="RUNNING", phase="running"
                    )
                )
                await self._current(lease, "RUNNING")
                owned.state = "RUNNING"
            except BaseException:
                self.revoke(lease)
                raise

    async def _active(self, lease: RequestLease) -> bool:
        if not self.owns(lease) or self._owned[lease.sequence].state != "RUNNING":
            return False
        try:
            await self._current(lease, "RUNNING")
        except PilotError as exc:
            if exc.code in ("forbidden", "conflict"):
                return False
            raise
        return self.owns(lease)

    async def reserve_model_call(self, lease: RequestLease) -> bool:
        async with self._gate:
            try:
                if not await self._active(lease):
                    return False
                owned = self._require(lease)
                counted = await self._wait(
                    self._store.reserve_attempt(lease.sequence, kind="model", limit=8)
                )
                if not counted or not self.owns(lease):
                    return False
                reserved = await self._wait(
                    self._backend.reserve_model_call(
                        self._namespace,
                        lease.sequence,
                        lease.quota_subject_id,
                        capability=owned.backend_capability,
                    )
                )
                if type(reserved) is not bool:
                    raise PilotError("store", uncertain=True)
                return reserved and await self._active(lease)
            except BaseException:
                self.revoke(lease)
                raise

    async def reserve_tool_call(self, lease: RequestLease, tool: str) -> bool:
        if type(tool) is not str or tool not in {"calculator", "utc_time", "web_search"}:
            return False
        async with self._gate:
            try:
                if not await self._active(lease):
                    return False
                counted = await self._wait(
                    self._store.reserve_attempt(lease.sequence, kind="tool", limit=4)
                )
                return counted and await self._active(lease)
            except BaseException:
                self.revoke(lease)
                raise

    async def finish_request(self, lease: RequestLease, *, success: bool) -> None:
        if type(success) is not bool:
            raise PilotError("invalid_request")
        async with self._gate:
            owned = self._require(lease)
            if owned.state != "RUNNING":
                raise PilotError("conflict")
            try:
                await self._current(lease, "RUNNING")
                await self._wait(
                    self._backend.finish_request(
                        self._namespace,
                        lease.sequence,
                        lease.quota_subject_id,
                        success=success,
                        capability=owned.backend_capability,
                    )
                )
            finally:
                self.revoke(lease)

    async def close(self) -> None:
        # Revoke before waiting; an in-flight operation must never grant after close.
        self._closed = True
        self._owned.clear()
        async with self._gate:
            await self._wait(self._backend.close())
