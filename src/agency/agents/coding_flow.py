"""End-to-end flow: describe a task, plan edits, run the coding agent.

This module ties :func:`agency.agents.coding_planner.plan_edits` together with
an agent's ``run_task`` so callers can go from a free-form description to a
Forge verdict in one call.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from collections.abc import Coroutine
from typing import Any, cast

from agency.agents.coding_planner import plan_edits

__all__ = ["run_described_task"]


def _call_run_task(
    agent: Any, branch: str, edits: list[dict[str, Any]], kwargs: dict[str, Any]
) -> Any:
    raw = agent.run_task(branch, edits, **kwargs)
    if asyncio.iscoroutine(raw) or isinstance(raw, (asyncio.Future,)):
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    raw = pool.submit(asyncio.run, cast("Coroutine[Any, Any, Any]", raw)).result()
            else:
                raw = loop.run_until_complete(raw)
        except RuntimeError:
            raw = asyncio.run(raw)
    return raw


def run_described_task(
    llm: Any,
    task: str,
    agent: Any,
    *,
    branch: str,
    file_context: dict[str, str] | None = None,
    **run_task_kwargs: Any,
) -> tuple[list[dict[str, Any]], Any]:
    """Plan edits for ``task`` with ``llm``, then run them via ``agent``.

    Returns a ``(edits, report)`` tuple where ``edits`` is the list produced
    by :func:`plan_edits` and ``report`` is whatever ``agent.run_task``
    returns (typically a :class:`agency.forge.CodingAgentReport`).

    Any extra keyword arguments are forwarded to ``agent.run_task``.
    """
    edits = plan_edits(llm, task, file_context=file_context)
    report = _call_run_task(agent, branch, edits, run_task_kwargs)
    return edits, report
