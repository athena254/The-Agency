"""Request-bound metered LLM adapter (frozen spec docs/SPEC_MODEL_METERING_V1.md)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from agency.admission.models import OwnerExecutionGrant, OwnerMeteredBinding, PilotError
from agency.kernel.audit import AuditEntry, BetaAuditLog
from agency.llm.adapter import LLMAdapter, _bounded_dispatch_text
from agency.telegram.budget_store import BetaBudgetStore
from agency.tools.base import BetaPrincipal

if TYPE_CHECKING:
    from agency.admission.budget import OwnerBudgetAdapter


class MeteredDenied(Exception):
    """Fail-closed denial: no provider call was (or will be) made."""


class MeteredError(Exception):
    """Provider/unknown-outcome failure after a durable pending intent."""


@dataclass(frozen=True)
class MeteredBinding:
    """Immutable request binding built once per admitted request."""

    bot_id: str
    update_id: int
    user_id: int
    capability: str
    principal: BetaPrincipal
    provider: str
    model: str

    def __post_init__(self) -> None:
        if type(self.bot_id) is not str or not self.bot_id.strip() or len(self.bot_id) > 128:
            raise ValueError("binding bot_id must be a nonempty local identifier")
        if ":" in self.bot_id and len(self.bot_id) >= 20:
            head, _, tail = self.bot_id.partition(":")
            if head.isdigit() and len(tail) >= 20:
                raise ValueError("binding bot_id must be a stable local identifier, not a token")
        if type(self.update_id) is not int or self.update_id < 0:
            raise ValueError("binding update_id must be a nonnegative int")
        if type(self.user_id) is not int or isinstance(self.user_id, bool) or self.user_id <= 0:
            raise ValueError("binding user_id must be a positive int")
        if type(self.capability) is not str or not self.capability:
            raise ValueError("binding capability must be a nonempty opaque string")
        if not isinstance(self.principal, BetaPrincipal):
            raise TypeError("binding principal must be a BetaPrincipal")
        if type(self.provider) is not str or not self.provider.strip():
            raise ValueError("binding provider must be a nonempty string")
        if type(self.model) is not str or not self.model.strip():
            raise ValueError("binding model must be a nonempty string")
        if self.user_id != self.principal.telegram_user_id:
            raise ValueError("binding user_id must agree with principal.telegram_user_id")

    def __repr__(self) -> str:
        return (
            "MeteredBinding("
            f"bot_id={self.bot_id!r}, update_id={self.update_id!r}, "
            f"user_id={self.user_id!r}, principal={self.principal!r}, "
            f"provider={self.provider!r}, model={self.model!r})"
        )


class MeteredModel:
    """Concrete metered wrapper over a concrete ``LLMAdapter``."""

    def __init__(
        self,
        inner: LLMAdapter,
        binding: MeteredBinding | OwnerMeteredBinding,
        store: BetaBudgetStore | OwnerBudgetAdapter,
        audit: BetaAuditLog,
        timeout_s: float = 30.0,
    ) -> None:
        from agency.admission.budget import OwnerBudgetAdapter

        if type(inner) is not LLMAdapter:
            raise TypeError("MeteredModel requires a concrete LLMAdapter")
        if not (
            (type(binding) is MeteredBinding and type(store) is BetaBudgetStore)
            or (type(binding) is OwnerMeteredBinding and type(store) is OwnerBudgetAdapter)
        ):
            raise TypeError("MeteredModel requires an exact matching binding/budget pair")
        if type(audit) is not BetaAuditLog:
            raise TypeError("MeteredModel requires a concrete BetaAuditLog")

        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)):
            raise TypeError("timeout_s must be a number")
        if not timeout_s > 0 or timeout_s == float("inf"):
            raise ValueError("timeout_s must be finite and positive")
        if binding.provider != inner.provider or binding.model != inner.model:
            raise ValueError("binding provider/model must match the wrapped adapter")
        if (
            isinstance(binding, MeteredBinding)
            and binding.user_id != binding.principal.telegram_user_id
        ):
            raise ValueError("binding user_id must agree with principal.telegram_user_id")
        if isinstance(binding, OwnerMeteredBinding):
            if timeout_s > 45:
                raise ValueError("owner timeout_s must not exceed 45 seconds")
            binding.__post_init__()
            if binding.grant.budget is not store:
                raise TypeError("MeteredModel requires the grant's exact budget")
        self._snapshot = (
            inner.freeze_dispatch() if isinstance(binding, OwnerMeteredBinding) else None
        )
        self._inner = inner
        self._binding = binding
        self._store = store
        self._audit = audit
        self._timeout_s = float(timeout_s)
        self._issued = (inner, binding, store, audit)
        self._owner_grant: OwnerExecutionGrant | None = (
            binding.grant if isinstance(binding, OwnerMeteredBinding) else None
        )
        self._owner_contract = (
            self._owner_facts(binding) if isinstance(binding, OwnerMeteredBinding) else None
        )

    @property
    def binding(self) -> MeteredBinding | OwnerMeteredBinding:
        return self._binding

    @property
    def audit(self) -> BetaAuditLog:
        return self._audit

    @property
    def provider(self) -> str:
        return self._binding.provider

    @property
    def model(self) -> str:
        return self._binding.model

    def __repr__(self) -> str:
        if self._owner_grant is not None or isinstance(self._binding, OwnerMeteredBinding):
            return "MeteredModel(owner-pilot)"
        return (
            "MeteredModel("
            f"bot_id={self._binding.bot_id!r}, "
            f"update_id={self._binding.update_id!r}, "
            f"user_id={self._binding.user_id!r}, "
            f"principal={self._binding.principal!r}, "
            f"provider={self._binding.provider!r}, "
            f"model={self._binding.model!r})"
        )

    async def generate(self, prompt: str, context: dict[str, Any] | None = None) -> str:
        from agency.admission.budget import OwnerBudgetAdapter

        binding = self._binding
        if not isinstance(prompt, str) or not prompt.strip():
            raise MeteredDenied("metered: invalid prompt")
        # Lifelong provider binding (P-M1): constructor equality is not enough;
        # the inner adapter or stores may have been mutated after binding.
        # Re-validate before any quota/audit side effects; zero dispatch on drift.
        if type(self._inner) is not LLMAdapter:
            raise MeteredDenied("metered: provider binding denied")
        if type(self._store) not in (BetaBudgetStore, OwnerBudgetAdapter):
            raise MeteredDenied("metered: budget unavailable")
        if type(self._audit) is not BetaAuditLog:
            raise MeteredDenied("metered: audit unavailable")
        if type(binding) not in (MeteredBinding, OwnerMeteredBinding):
            raise MeteredDenied("metered: invalid binding")
        if isinstance(binding, OwnerMeteredBinding):
            try:
                _bounded_dispatch_text(prompt)
            except (ValueError, TypeError):
                raise MeteredDenied("metered: invalid prompt") from None
            await self._check_owner(context)
        else:
            self._validate_beta(binding, context)
        try:
            if isinstance(binding, OwnerMeteredBinding) and isinstance(
                self._store, OwnerBudgetAdapter
            ):
                reserved = await self._store.reserve_model_call(
                    binding.grant.lease, limit=binding.grant.max_model_calls
                )
            elif isinstance(binding, MeteredBinding) and isinstance(self._store, BetaBudgetStore):
                reserved = await self._store.reserve_model_call(
                    binding.bot_id,
                    binding.update_id,
                    binding.user_id,
                    capability=binding.capability,
                )
            else:
                raise MeteredDenied("metered: invalid binding")
        except asyncio.CancelledError:
            if self._owner_grant is None:
                raise
            raise MeteredDenied("metered: budget unavailable") from None
        except Exception as exc:
            raise MeteredDenied("metered: budget unavailable") from exc
        if reserved is not True if self._owner_grant is not None else not reserved:
            raise MeteredDenied("metered: quota denied")
        await self._check_owner(context)
        call_id = uuid4().hex
        intent = self._entry(call_id, "pending", "intent")
        try:
            claimed = await self._audit.claim_intent(intent)
        except asyncio.CancelledError:
            if self._owner_grant is None:
                raise
            raise MeteredDenied("metered: audit unavailable") from None
        except Exception as exc:
            raise MeteredDenied("metered: audit unavailable") from exc
        if claimed is not True if self._owner_grant is not None else not claimed:
            raise MeteredDenied("metered: audit unavailable")
        await self._check_owner(context)
        if self._snapshot is None and (
            type(self._inner) is not LLMAdapter
            or self._inner.provider != binding.provider
            or self._inner.model != binding.model
        ):
            raise MeteredDenied("metered: provider binding denied")
        try:
            dispatch = self._dispatch(prompt, context)
            raw = await asyncio.wait_for(dispatch, timeout=self._timeout_s)
        except MeteredDenied as exc:
            if self._owner_grant is not None:
                raise
            await self._record_outcome(call_id, "unknown", type(exc).__name__)
            raise MeteredError("metered: model unavailable") from exc
        except TimeoutError as exc:
            raise MeteredError("metered: model unavailable") from exc
        except asyncio.CancelledError as exc:
            await self._record_outcome(call_id, "unknown", type(exc).__name__)
            raise MeteredError("metered: model unavailable") from exc
        except Exception as exc:
            await self._record_outcome(call_id, "unknown", type(exc).__name__)
            raise MeteredError("metered: model unavailable") from exc
        if not isinstance(raw, str) or not raw.strip():
            await self._record_outcome(call_id, "unknown", "EmptyResponse")
            raise MeteredError("metered: model unavailable")
        try:
            await self._check_owner(context)
        except MeteredDenied:
            await self._record_outcome(call_id, "unknown", "OwnerBindingChanged")
            raise MeteredError("metered: model unavailable") from None
        await self._record_outcome(call_id, "ok", "Generate")
        try:
            await self._check_owner(context)
        except MeteredDenied:
            raise MeteredError("metered: owner binding denied") from None
        return raw

    def _validate_owner(self, context: dict[str, Any] | None) -> OwnerExecutionGrant:
        from agency.admission.budget import OwnerBudgetAdapter

        binding, grant = self._binding, self._owner_grant
        if (
            type(binding) is not OwnerMeteredBinding
            or type(grant) is not OwnerExecutionGrant
            or binding.grant is not grant
            or type(self._store) is not OwnerBudgetAdapter
            or grant.budget is not self._store
            or type(self._audit) is not BetaAuditLog
            or type(self._inner) is not LLMAdapter
            or any(
                current is not issued
                for current, issued in zip(
                    (self._inner, binding, self._store, self._audit), self._issued, strict=True
                )
            )
        ):
            raise MeteredDenied("metered: owner binding denied")
        assert isinstance(grant, OwnerExecutionGrant)
        try:
            binding.__post_init__()
            if self._owner_facts(binding) != self._owner_contract:
                raise MeteredDenied("metered: owner binding denied")
        except (PilotError, AttributeError, TypeError, ValueError):
            raise MeteredDenied("metered: owner binding denied") from None
        if (
            not isinstance(context, dict)
            or "owner_grant" not in context
            or context["owner_grant"] is not self._owner_grant
        ):
            raise MeteredDenied("metered: missing owner grant")
        if any(key not in {"owner_grant", "task_id", "strict"} for key in context):
            raise MeteredDenied("metered: context override denied")
        if "task_id" in context and (
            type(context["task_id"]) is not str or context["task_id"] != grant.lease.task_id
        ):
            raise MeteredDenied("metered: task mismatch")
        return grant

    @staticmethod
    def _owner_facts(binding: OwnerMeteredBinding) -> tuple[Any, ...]:
        grant = binding.grant
        return (
            binding.provider,
            binding.model,
            binding.provider_config_id,
            id(grant.lease),
            grant.allowed_tools,
            grant.max_model_calls,
            grant.max_tool_calls,
        )

    async def _check_owner(self, context: dict[str, Any] | None) -> None:
        if self._owner_grant is None:
            return
        grant = self._validate_owner(context)
        try:
            active = await grant.budget._active(grant.lease)
        except (Exception, asyncio.CancelledError):  # noqa: BLE001 -- fail closed at storage boundary
            raise MeteredDenied("metered: owner binding denied") from None
        self._validate_owner(context)
        if active is not True:
            raise MeteredDenied("metered: owner binding denied")

    async def _dispatch(self, prompt: str, context: dict[str, Any] | None) -> str:
        await self._check_owner(context)
        if self._snapshot is not None:
            return await self._inner.generate_bound(prompt, self._snapshot)
        return await self._inner.generate(prompt, {})

    def _validate_beta(self, binding: MeteredBinding, context: dict[str, Any] | None) -> None:
        if type(self._store) is not BetaBudgetStore:
            raise MeteredDenied("metered: budget unavailable")
        if (
            type(binding.bot_id) is not str
            or not binding.bot_id.strip()
            or len(binding.bot_id) > 128
            or type(binding.update_id) is not int
            or binding.update_id < 0
            or type(binding.user_id) is not int
            or isinstance(binding.user_id, bool)
            or binding.user_id <= 0
            or type(binding.capability) is not str
            or not binding.capability
            or not isinstance(binding.principal, BetaPrincipal)
            or type(binding.provider) is not str
            or not binding.provider.strip()
            or type(binding.model) is not str
            or not binding.model.strip()
            or binding.user_id != binding.principal.telegram_user_id
        ):
            raise MeteredDenied("metered: invalid binding")
        if self._inner.provider != binding.provider or self._inner.model != binding.model:
            raise MeteredDenied("metered: provider binding denied")
        if binding.principal.private_chat is not True:
            raise MeteredDenied("metered: non-private scope denied")
        if not isinstance(context, dict):
            raise MeteredDenied("metered: missing principal")
        if context:
            for key in ("provider", "model", "binding", "capability", "principal"):
                if key in context:
                    raise MeteredDenied("metered: context override denied")
        principal = context.get("beta_principal")
        if not isinstance(principal, BetaPrincipal):
            raise MeteredDenied("metered: missing principal")
        if principal != binding.principal:
            raise MeteredDenied("metered: principal mismatch")

    def _entry(
        self, call_id: str, result: str, phase: str, error_class: str | None = None
    ) -> AuditEntry:
        binding = self._binding
        if self._owner_grant is not None:
            if self._snapshot is None:
                raise MeteredError("metered: owner binding denied")
            provider, model = self._snapshot.provider, self._snapshot.model
            task = "owner-pilot"
            identity: dict[str, Any] = {"identity": "owner-pilot"}
        else:
            if not isinstance(binding, MeteredBinding):
                raise MeteredError("metered: invalid binding")
            provider, model = binding.provider, binding.model
            task = f"{binding.bot_id}:{binding.update_id}"
            identity = {"user_id": binding.principal.telegram_user_id}
        return AuditEntry(
            agent="metered-model",
            task=task,
            target="model.generate",
            authorization="granted",
            action="model.generate",
            result=result,
            evidence={
                # Unknown is evidence, not a terminal acknowledgement. Keep the
                # original intent unresolved so other processes and restarts
                # cannot dispatch another call on an uncertain provider effect.
                "phase": phase,
                "call_id": call_id,
                "provider": provider,
                "model": model,
                **identity,
                **({"error_class": error_class} if error_class is not None else {}),
            },
        )

    async def _record_outcome(self, call_id: str, result: str, error_class: str) -> None:
        entry = self._entry(
            call_id, result, "uncertainty" if result == "unknown" else "outcome", error_class
        )
        if self._owner_grant is None:
            # Legacy beta callers retain their established error contract.
            await self._audit.append_beta(entry)
            return
        try:
            acknowledged = await self._audit.append_beta(entry)
        except (Exception, asyncio.CancelledError):  # noqa: BLE001 -- never expose storage exceptions
            raise MeteredError("metered: audit unavailable") from None
        if type(acknowledged) is not str or acknowledged != entry.entry_id:
            raise MeteredError("metered: audit unavailable")


__all__ = ["MeteredBinding", "MeteredDenied", "MeteredError", "MeteredModel"]
