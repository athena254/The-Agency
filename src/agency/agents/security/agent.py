"""SecurityAgent — threat analysis, hardening, vulnerability review."""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)

from agency.agents._prompt_shared import ESCALATION_RULE, OUTPUT_CONTRACT

SECURITY_SYSTEM_PROMPT = (
    """You are the Security Agent of The Agency — a defensive security specialist.
Scope: threat analysis, vulnerability review, hardening, secure configuration, incident triage, risk assessment.
Rules:
1. Be precise and defensive. Focus on reducing risk, never on attacking systems you don't own.
2. Use web_search/web_fetch for CVEs/advisories; memory_query/memory_write for findings.
3. Recall prior findings and assets with memory_query.
4. Store findings with memory_write (severity, evidence, affected component).
5. Never fabricate vulnerabilities or exploit details. Distinguish confirmed vs. suspected.
6. Plan reviews as structured checklists; verify each control before marking pass.
7. Reflect on findings for false positives, missing scope, and residual risk.
"""
    f"8. {ESCALATION_RULE}\n"
    f"9. {OUTPUT_CONTRACT}"
)


class SecurityAgent:
    """Domain specialist for defensive security (standalone-usable)."""

    def __init__(self) -> None:
        self.domain = "security"
        self.capabilities = {
            "security",
            "threat-analysis",
            "hardening",
            "vulnerability-review",
            "tools",
            "respond",
        }
        self.name = "Security Agent"
        self._log = structlog.get_logger(__name__)

    def system_prompt(self) -> str:
        return SECURITY_SYSTEM_PROMPT

    async def handle(self, message: str, context: dict[str, Any] | None = None) -> str:
        _ = context
        return f"{SECURITY_SYSTEM_PROMPT}\n\n{message}"


__all__ = ["SECURITY_SYSTEM_PROMPT", "SecurityAgent"]
