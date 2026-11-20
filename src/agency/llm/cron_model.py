"""Model for Agency-side cron/scheduled jobs (cron lines, proactive runs).

Cron jobs — Agency-generated cron lines (``forge/scheduler.py``), agent cron
jobs, user-requested cron jobs, and proactive Agency runs — use this model:
``AGENCY_CRON_MODEL`` override, else the free-tier gemma default.
"""

from __future__ import annotations

import os

CRON_MODEL_ENV = "AGENCY_CRON_MODEL"
DEFAULT_CRON_MODEL = "gemma-3-4b-it"


def cron_model() -> str:
    """Cron/scheduled-job model: env override, else the free-tier gemma default."""

    return os.environ.get(CRON_MODEL_ENV, "").strip() or DEFAULT_CRON_MODEL
