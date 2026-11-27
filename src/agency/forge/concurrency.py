"""Bounded parallel dispatch through one durable Blacksmith queue."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any


def run_queue(
    ops_factory: Callable[[], Any],
    *,
    limit: int,
    max_jobs: int,
) -> list[Any]:
    """Run up to ``limit`` dispatchers at once until no queued job remains.

    Each worker owns its own ops instance; SQLite connections are not shared.
    Returns the jobs that were dispatched, in completion order.
    """
    if limit < 1 or max_jobs < 1:
        return []
    dispatched: list[Any] = []
    idle_streak = 0
    while len(dispatched) < max_jobs and idle_streak < 2:
        slots = min(limit, max_jobs - len(dispatched))
        ops_list = [ops_factory() for _ in range(slots)]
        try:
            with ThreadPoolExecutor(max_workers=slots) as pool:
                futures = [pool.submit(ops.dispatch_next) for ops in ops_list]
                got_job = False
                for future in as_completed(futures):
                    job = future.result()
                    if job is not None:
                        dispatched.append(job)
                        got_job = True
        finally:
            for ops in ops_list:
                close = getattr(ops, "close", None)
                if close is not None:
                    close()
        idle_streak = 0 if got_job else idle_streak + 1
    return dispatched
