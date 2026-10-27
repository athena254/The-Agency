"""Runtime access to factory-issued system prompts.

The governed Agent Factory stores the composed system prompt on
``Agent.metadata["system_prompt"]`` at activation. Runtime surfaces read it
back through this module so every path applies the same validation and a
corrupted governance artifact never silently falls back to an ungoverned
default prompt.
"""

from __future__ import annotations

from agency.kernel.identity import Agent

MAX_ISSUED_PROMPT_CHARS = 256 * 1024


def issued_system_prompt(agent: Agent | None) -> str:
    """Return the factory-issued system prompt for ``agent``, or ``""``.

    Returns an empty string when the identity carries no issued prompt (for
    example agents registered directly rather than through the factory).
    Raises ``TypeError`` for a non-string value and ``ValueError`` when the
    key is present but blank or larger than :data:`MAX_ISSUED_PROMPT_CHARS`.
    """
    if agent is None:
        return ""
    raw = agent.metadata.get("system_prompt")
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise TypeError("agent metadata system_prompt must be a string.")
    prompt = raw.strip()
    if not prompt:
        raise ValueError("agent metadata system_prompt must be nonblank.")
    if len(prompt) > MAX_ISSUED_PROMPT_CHARS:
        raise ValueError(f"agent metadata system_prompt exceeds {MAX_ISSUED_PROMPT_CHARS} chars.")
    return prompt


__all__ = ["MAX_ISSUED_PROMPT_CHARS", "issued_system_prompt"]
