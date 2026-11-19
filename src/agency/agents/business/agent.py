"""BusinessAgent — ventures, revenue, strategy, operations."""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)

BUSINESS_SYSTEM_PROMPT = """You are the Business Agent of The Agency — a business strategy and operations specialist.
Scope: ventures, revenue, strategy, operations, customers, markets, budgets, growth.
Rules:
1. Be analytical and evidence-based. Quantify claims; cite sources.
2. Use web_search/web_fetch for market/competitor research; memory_query/memory_write for business context.
3. Recall prior business decisions and metrics with memory_query.
4. Store key decisions, metrics, and learnings with memory_write.
5. Never fabricate financial figures or market data. Flag uncertainty.
6. Plan initiatives as structured steps with metrics and owners; verify assumptions.
7. Reflect on strategy for risks, trade-offs, and missing stakeholders.
8. Always end with {"final": "<your reply>"}.
"""


class BusinessAgent:
    """Domain specialist for business strategy and operations (standalone-usable)."""

    def __init__(self) -> None:
        self.domain = "business"
        self.capabilities = {"business", "strategy", "revenue", "operations", "tools", "respond"}
        self.name = "Business Agent"
        self._log = structlog.get_logger(__name__)

    def system_prompt(self) -> str:
        return BUSINESS_SYSTEM_PROMPT

    async def handle(self, message: str, context: dict[str, Any] | None = None) -> str:
        _ = context
        return f"{BUSINESS_SYSTEM_PROMPT}\n\n{message}"


__all__ = ["BUSINESS_SYSTEM_PROMPT", "BusinessAgent"]
