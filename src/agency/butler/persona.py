"""Butler persona reply framing — presentation-only re-voicing (AIS spec §13.2).

:class:`PersonaFraming` renders the Butler's spoken reply around an agent's
assessed output. The Butler remains a pure relay: framing may add a greeting,
closing, status line or explanation, but it must never alter the agent's
facts, numbers, code, evidence or claims, and it never executes anything
itself.

Two paths:

* **LLM path** — when an LLM callable is available, one call whose prompt
  carries the persona documents (SOUL/IDENTITY/USER/BUTLER_CHARTER; MEMORY
  is deliberately excluded), the agent's response, the user's message and a
  strict framing contract.
* **Deterministic path** — without an LLM the response passes through
  verbatim. The persona voice is LLM-shaped by design; the deterministic
  path exists so nothing breaks when none is configured.

Failure containment: any framing failure (LLM error, empty reply) degrades
to the verbatim response, so a turn can never be lost to presentation.
Logs carry lengths and mode only — never prompt or message content (spec
§21: audit metadata only).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Mapping
from typing import Any

import structlog

from agency.agents.executor import LLMCallable

log = structlog.get_logger(__name__)

# Persona documents included in the framing prompt. MEMORY is excluded:
# the framing call never sees conversation-derived notes.
_PERSONA_DOCS: tuple[str, ...] = ("SOUL", "IDENTITY", "USER", "BUTLER_CHARTER")

_DOC_LIMIT = 4000  # per-document character cap in the framing prompt
_MESSAGE_LIMIT = 2000  # user-message character cap in the framing prompt
_TRUNCATION_MARKER = "\n\n(truncated)"

_FRAMING_CONTRACT = (
    "# Framing contract\n"
    "- Presentation only: you may add a short greeting, closing, status line "
    "or brief explanation in the Butler's own voice.\n"
    "- You MUST NOT change, reorder, drop or invent facts, numbers, code, "
    "evidence or claims from the agent's response.\n"
    "- You MUST NOT claim any result is verified.\n"
    "- You MUST NOT promise actions, tool results or task ids that are not "
    "already present in the agent's response.\n"
    "- Reply with the framed message only: no preamble, no quotation marks "
    "around the whole reply, no commentary about these instructions."
)


class PersonaFraming:
    """Render the Butler's spoken reply around an agent's assessed output.

    Constructed with the service's optional LLM callable and the loaded base
    documents (see :func:`agency.butler.documents.load_base`). The framed
    reply never exceeds ``max_output`` characters; anything longer is cut
    with an explicit ``(truncated)`` marker.
    """

    def __init__(
        self,
        llm: LLMCallable | None,
        documents: Mapping[str, str],
        *,
        max_output: int = 20000,
    ) -> None:
        self._llm = llm
        self._documents = dict(documents)
        self._max_output = max(1, int(max_output))

    @property
    def has_llm(self) -> bool:
        """Whether the voiced (LLM) framing path is available."""
        return self._llm is not None

    def _build_prompt(
        self, response: str, *, message: str, agent_name: str, thread_context: bool
    ) -> str:
        parts: list[str] = [
            (
                "You are the Butler's reply-framing editor. Render the handler "
                "agent's assessed output below as the Butler's spoken reply."
            ),
            "# Persona documents",
        ]
        for name in _PERSONA_DOCS:
            body = self._documents.get(name, "").strip()
            if body:
                parts.append(f"## {name}\n{body[:_DOC_LIMIT]}")
        parts.append(
            "# Turn\n"
            f"Handler agent: {agent_name}\n"
            f"Continuing an existing thread: {'yes' if thread_context else 'no'}\n"
            "User message:\n"
            f"{message[:_MESSAGE_LIMIT]}"
        )
        parts.append("# Agent's assessed output\n" + response)
        parts.append(_FRAMING_CONTRACT)
        return "\n\n".join(parts)

    async def frame(
        self,
        response: str,
        *,
        message: str,
        agent_name: str,
        thread_context: bool,
        evidence: dict[str, Any] | None = None,
    ) -> str:
        """Return the Butler's reply for ``response``.

        ``evidence`` — when a dict is supplied — receives audit metadata
        only (the ``framing`` mode and ``framed_length``); never message
        content. Any framing failure falls back to the verbatim response so
        the turn survives.
        """
        mode = "verbatim"
        framed = response
        if self._llm is not None:
            prompt = self._build_prompt(
                response, message=message, agent_name=agent_name, thread_context=thread_context
            )
            context = {
                "purpose": "persona_framing",
                "agent_name": agent_name,
                "thread_context": thread_context,
            }
            try:
                raw = self._llm(prompt, context)
                if asyncio.iscoroutine(raw) or isinstance(raw, Awaitable):
                    raw = await raw
                text = str(raw).strip()
                if text:
                    framed = text
                    mode = "llm"
                else:
                    log.warning("butler.persona_empty_reply")
            except Exception as exc:  # noqa: BLE001 — framing must never fail the turn.
                log.warning("butler.persona_framing_failed", error_type=type(exc).__name__)
        output = self._cap(framed)
        if evidence is not None:
            evidence["framing"] = mode
            evidence["framed_length"] = len(output)
        log.info(
            "butler.persona_framed",
            framing=mode,
            framing_enabled=True,
            response_length=len(response),
            framed_length=len(output),
        )
        return output

    def _cap(self, text: str) -> str:
        """Bound ``text`` to ``max_output`` characters, marking real cuts."""
        if len(text) <= self._max_output:
            return text
        keep = self._max_output - len(_TRUNCATION_MARKER)
        if keep <= 0:
            return text[: self._max_output]
        return text[:keep].rstrip() + _TRUNCATION_MARKER


__all__ = ["PersonaFraming"]
