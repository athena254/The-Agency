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
        if binding.user_id != binding.principal.telegram_user_id:
            raise ValueError("binding user_id must agree with principal.telegram_user_id")
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
        # Lifelong provider binding (P-M1): constructor equality is not enough;
        # the inner adapter or stores may have been mutated after binding.
        # Re-validate before any quota/audit side effects; zero dispatch on drift.
        if type(self._inner) is not LLMAdapter:
            raise MeteredDenied("metered: provider binding denied")
        if type(self._store) is not BetaBudgetStore:
            raise MeteredDenied("metered: budget unavailable")
        if type(self._audit) is not BetaAuditLog:
            raise MeteredDenied("metered: audit unavailable")
        if not isinstance(binding, MeteredBinding):
            raise MeteredDenied("metered: invalid binding")
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
        # Budget and audit persistence yield to other tasks. Recheck the fixed
        # adapter after those awaits, before constructing the dispatch coroutine.
        # Refusal keeps the acknowledged intent pending rather than inventing an
        # outcome or permitting a retry through a different provider.
        if (
            type(self._inner) is not LLMAdapter
            or self._inner.provider != binding.provider
            or self._inner.model != binding.model
        ):
            raise MeteredDenied("metered: provider binding denied")
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
                # Unknown is evidence, not a terminal acknowledgement. Keep the
                # original intent unresolved so other processes and restarts
                # cannot dispatch another call on an uncertain provider effect.
                "phase": "uncertainty" if result == "unknown" else "outcome",
                "call_id": call_id,
                "provider": binding.provider,
                "model": binding.model,
                "user_id": binding.principal.telegram_user_id,
                "error_class": error_class,
            },
        )
        await self._audit.append_beta(entry)


__all__ = ["MeteredBinding", "MeteredDenied", "MeteredError", "MeteredModel"]
