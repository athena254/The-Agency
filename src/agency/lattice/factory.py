"""Singleton factory for the unified Lattice API.

Use :func:`get_lattice` everywhere so the process shares one backend
connection pool. Use :func:`reset_lattice` in tests to drop the cached
instance (also closes the backend when possible).
"""

from __future__ import annotations

import structlog

from agency.lattice.api import Lattice
from agency.lattice.models import LatticeConfig

log = structlog.get_logger(__name__)

_lattice_instance: Lattice | None = None


async def get_lattice(config: LatticeConfig | None = None) -> Lattice:
    """Get or create the singleton Lattice instance."""

    global _lattice_instance
    if _lattice_instance is None:
        log.info("lattice.factory.creating")
        _lattice_instance = Lattice(config=config)
        await _lattice_instance.initialize()
        log.info("lattice.factory.ready")
    return _lattice_instance


async def reset_lattice() -> None:
    """Reset singleton (for testing)."""

    global _lattice_instance
    if _lattice_instance is not None:
        try:
            await _lattice_instance.close()
        except Exception:  # noqa: BLE001 - best-effort cleanup in tests
            log.warning("lattice.factory.reset_close_failed")
    _lattice_instance = None
    log.info("lattice.factory.reset")


__all__ = ["get_lattice", "reset_lattice"]
