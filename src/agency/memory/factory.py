"""Memory backend factory.

SMS is always the default. Honcho is selected only via explicit settings and
is optional at import time, so a missing ``honcho-ai`` install cannot break the
default runtime.
"""

from __future__ import annotations

import structlog

from agency.config.settings import AgencySettings
from agency.memory.sms.store import MemoryStore

log = structlog.get_logger(__name__)


def build_memory_backend(db_path: str, *, settings: AgencySettings | None = None) -> MemoryStore:
    """Build the configured memory backend, defaulting to plain SMS."""

    settings = settings or AgencySettings.load()
    if settings.memory_backend != "honcho":
        return MemoryStore(db_path)
    try:
        from agency.memory.honcho.backend import HonchoMemoryBackend
        from agency.memory.honcho.config import build_honcho_client
    except ImportError:  # optional extra not installed
        log.warning("memory_backend.honcho_unavailable_fallback")
        return MemoryStore(db_path)
    client = build_honcho_client(settings)
    if client is None:
        log.warning("memory_backend.honcho_disabled_fallback")
        return MemoryStore(db_path)
    log.info("memory_backend.honcho", workspace=settings.honcho_workspace)
    return HonchoMemoryBackend(db_path, client=client)


__all__ = ["build_memory_backend"]
