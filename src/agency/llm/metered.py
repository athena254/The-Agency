"""Request-bound metered LLM adapter (frozen spec docs/SPEC_MODEL_METERING_V1.md)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from agency.kernel.audit import AuditEntry, BetaAuditLog
from agency.llm.adapter import LLMAdapter
from agency.telegram.budget_store import BetaBudgetStore, BudgetUnavailable
from agency.tools.base import BetaPrincipal


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
        binding: MeteredBinding,
        store: BetaBudgetStore,
        audit: BetaAuditLog,
        timeout_s: float = 30.0,
    ) -> None:
        if type(inner) is not LLMAdapter:
            raise TypeError("MeteredModel requires a concrete LLMAdapter")
        if type(store) is not BetaBudgetStore:
            raise TypeError("MeteredModel requires a concrete BetaBudgetStore")
        if type(audit) is not BetaAuditLog:
            raise TypeError("MeteredModel requires a concrete BetaAuditLog")
        if not isinstance(binding, MeteredBinding):
            raise TypeError("MeteredModel requires a MeteredBinding")
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)):
            raise TypeError("timeout_s must be a number")
        if not timeout_s > 0 or timeout_s == float("inf"):
            raise ValueError("timeout_s must be finite and positive")
        if binding.provider != inner.provider or binding.model != inner.model:
            raise ValueError("binding provider/model must match the wrapped adapter")
        self._inner = inner
        self._binding = binding
        self._store = store
        self._audit = audit
        self._timeout_s = float(timeout_s)

    @property
    def binding(self) -> MeteredBinding:
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
        binding = self._binding
        if not isinstance(prompt, str) or not prompt.strip():
            raise MeteredDenied("metered: invalid prompt")
        if context:
            for key in ("provider", "model", "binding", "capability", "principal"):
                if key in context:
                    raise MeteredDenied("metered: context override denied")
        principal = context.get("beta_principal") if isinstance(context, dict) else None
        if principal is None:
            raise MeteredDenied("metered: missing principal")
        if principal != binding.principal:
            raise MeteredDenied("metered: principal mismatch")
        if binding.principal.private_chat is not True:
            raise MeteredDenied("metered: non-private scope denied")
        try:
            reserved = await self._store.reserve_model_call(
                binding.bot_id,
                binding.update_id,
                binding.user_id,
                capability=binding.capability,
            )
        except BudgetUnavailable as exc:
            raise MeteredDenied("metered: budget unavailable") from exc
        except Exception as exc:
            raise MeteredDenied("metered: budget unavailable") from exc
        if not reserved:
            raise MeteredDenied("metered: quota denied")
        call_id = uuid4().hex
        intent = AuditEntry(
            agent="metered-model",
            task=f"{binding.bot_id}:{binding.update_id}",
            target="model.generate",
            authorization="granted",
            action="model.generate",
            result="pending",
            evidence={
                "phase": "intent",
                "call_id": call_id,
                "provider": binding.provider,
                "model": binding.model,
                "user_id": binding.principal.telegram_user_id,
            },
        )
        try:
            claimed = await self._audit.claim_intent(intent)
        except Exception as exc:
            raise MeteredDenied("metered: audit unavailable") from exc
        if not claimed:
            raise MeteredDenied("metered: audit unavailable")
        try:
            raw = await asyncio.wait_for(self._inner.generate(prompt, {}), timeout=self._timeout_s)
        except TimeoutError as exc:
            # Dispatch timed out with a genuinely unknown provider outcome
            # (the call may still complete server-side): leave the pending
            # intent unresolved so it stays blocking; never claim success.
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
        await self._record_outcome(call_id, "ok", "Generate")
        return raw

    async def _record_outcome(self, call_id: str, result: str, error_class: str) -> None:
        binding = self._binding
        entry = AuditEntry(
            agent="metered-model",
            task=f"{binding.bot_id}:{binding.update_id}",
            target="model.generate",
            authorization="granted",
            action="model.generate",
            result=result,
            evidence={
                "phase": "outcome",
                "call_id": call_id,
                "provider": binding.provider,
                "model": binding.model,
                "user_id": binding.principal.telegram_user_id,
                "error_class": error_class,
            },
        )
        await self._audit.append_beta(entry)


__all__ = ["MeteredBinding", "MeteredDenied", "MeteredError", "MeteredModel"]
