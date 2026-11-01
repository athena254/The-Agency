"""LLM-backed planning helper for the coding agent.

Turns a free-form task description into a concrete list of file edits
(``{"path", "old", "new"}``) by asking an LLM to emit JSON. Parsing is
defensive: anything that is not a JSON list of objects yields an empty list.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import re
from collections.abc import Awaitable
from typing import Any

from agency.agents.executor import LLMCallable

__all__ = ["plan_edits"]

_PROMPT_TEMPLATE = """You are a coding assistant. Given the task below, return ONLY a JSON array of edits.
Each edit must be an object with keys "path", "old", and "new":
[
  {{"path": "file.py", "old": "exact existing text to replace", "new": "replacement text"}}
]

Rules:
- "old" MUST be a non-empty exact substring of the current file content.
- Do not return an empty "old" string.
- Return valid JSON only, no prose.

Task: {task}
"""


def _invoke(llm_callable: LLMCallable, prompt: str) -> Any:
    raw = llm_callable(prompt, {})
    if asyncio.iscoroutine(raw) or isinstance(raw, Awaitable):
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    raw = pool.submit(asyncio.run, raw).result()
            else:
                raw = loop.run_until_complete(raw)
        except RuntimeError:
            raw = asyncio.run(raw)
    return raw


def _parse_edits(raw: Any) -> list[dict]:
    text = str(raw).strip() if raw is not None else ""
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    data: Any = None
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(0))
            except (json.JSONDecodeError, ValueError):
                data = None
    if not isinstance(data, list):
        return []
    result: list[dict] = []
    for item in data:
        if isinstance(item, dict) and isinstance(item.get("path"), str) and isinstance(item.get("old"), str) and item.get("old") != "" and isinstance(item.get("new"), str):
            result.append(item)
    return result


def plan_edits(llm_callable: LLMCallable, task: str, file_context: dict[str, str] | None = None) -> list[dict]:
    """Ask ``llm_callable`` for a JSON list of ``{path, old, new}`` edits.

    ``file_context`` maps relative paths to their current contents so the LLM
    can copy exact "old" substrings.
    Returns ``[]`` when the LLM output cannot be parsed into such a list.
    """
    context_block = ""
    if file_context:
        context_block = "\n\nCurrent file contents:\n"
        for path, content in file_context.items():
            context_block += f"\n--- {path} ---\n{content}\n"
    prompt = _PROMPT_TEMPLATE.format(task=task) + context_block
    try:
        raw = _invoke(llm_callable, prompt)
    except Exception:  # noqa: BLE001 - treat any call failure as no edits
        return []
    return _parse_edits(raw)
