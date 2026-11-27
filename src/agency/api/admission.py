"""Bounded admission for task intake.

Pure, testable rule used by the HTTP router. It refuses new work when the
pending queue is full; it never starts build work inside a request handler.
"""

from __future__ import annotations

import os

DEFAULT_MAX_PENDING = 1000
RETRY_AFTER_S = 5


class AdmissionRejected(Exception):
    """Raised when the pending-queue ceiling is reached."""


def max_pending_tasks() -> int:
    raw = os.environ.get("AGENCY_API_MAX_PENDING_TASKS", str(DEFAULT_MAX_PENDING))
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_PENDING
    return max(1, min(100_000, value))


def check_admission(pending_count: int, cap: int) -> None:
    if pending_count < 0:
        raise ValueError("pending_count must be non-negative")
    if cap < 1:
        raise ValueError("cap must be at least 1")
    if pending_count >= cap:
        raise AdmissionRejected(
            f"task queue full: {pending_count} pending, cap {cap}; retry after {RETRY_AFTER_S}s"
        )
