"""Deterministic system-prompt composition for factory-issued agents.

Given pinned prompt references, a resolver, optional slot parameters, and an
optional per-agent override, this module renders one system prompt. It never
calls a model and never executes prompt content; the same inputs always
produce byte-identical output.

Factory slots use ``{{SLOT_NAME}}`` syntax. Angle-bracket placeholders inside
upstream packs (for example ``<FEATURE>``) are intentionally left untouched:
they are filled by the human or agent at use time, not by the factory.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from agency.factory.prompts import PromptResolver

SLOT_RE = re.compile(r"\{\{([A-Z][A-Z0-9_]{0,63})\}\}")


@dataclass(frozen=True)
class ComposedPrompt:
    """Rendered system prompt plus provenance-friendly metadata."""

    text: str
    sha256: str
    prompt_refs: tuple[tuple[str, str], ...]


def compose_system_prompt(
    prompt_refs: Sequence[tuple[str, str]],
    *,
    resolver: PromptResolver,
    params: Mapping[str, str] | None = None,
    override: str = "",
) -> ComposedPrompt:
    """Render the final system prompt for a pinned set of prompt references.

    Raises ``ValueError`` on empty refs, empty bodies, unfilled slots, or
    unused parameters (all fail closed). Duplicate bodies are collapsed once
    so shared segments do not inflate the prompt.
    """
    refs = tuple((str(pid).strip(), str(ver).strip()) for pid, ver in prompt_refs)
    if not refs:
        raise ValueError("prompt_refs must not be empty for composition.")

    bodies: list[str] = []
    seen: set[str] = set()
    for prompt_id, version in refs:
        spec = resolver.get(prompt_id, version)
        body = spec.body.strip()
        if not body:
            raise ValueError(f"prompt {(prompt_id, version)!r} has an empty body.")
        if body in seen:
            continue
        seen.add(body)
        bodies.append(body)

    text = "\n\n".join(bodies)
    cleaned_override = override.strip()
    if cleaned_override:
        text = f"{text}\n\n{cleaned_override}"

    values = dict(params or {})
    used: set[str] = set()

    def _substitute(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            raise ValueError(f"unfilled prompt slot: {{{{{key}}}}}.")
        used.add(key)
        return values[key]

    text = SLOT_RE.sub(_substitute, text)
    unused = set(values) - used
    if unused:
        raise ValueError(f"unused prompt params: {sorted(unused)}.")

    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return ComposedPrompt(text=text, sha256=digest, prompt_refs=refs)


__all__ = ["ComposedPrompt", "compose_system_prompt"]
