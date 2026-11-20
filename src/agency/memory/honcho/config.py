"""Configuration for the optional Honcho memory mirror."""

from __future__ import annotations

import structlog

from agency.config.settings import AgencySettings
from agency.memory.honcho.client import HonchoClient, SDKHonchoClient

log = structlog.get_logger(__name__)


def build_honcho_client(settings: AgencySettings) -> HonchoClient | None:
    """Return a configured client, or ``None`` when Honcho is not enabled.

    Fails closed: a missing SDK or malformed config never takes down the
    default SMS memory path.
    """
    if settings.memory_backend != "honcho" or not settings.honcho_enabled:
        return None
    if not (settings.honcho_url and settings.honcho_workspace and settings.honcho_api_key):
        log.warning("honcho.config.incomplete")
        return None
    try:
        return SDKHonchoClient(
            base_url=settings.honcho_url,
            api_key=settings.honcho_api_key.get_secret_value(),
            workspace=settings.honcho_workspace,
            timeout=settings.honcho_timeout,
        )
    except Exception:  # noqa: BLE001 - optional integration must degrade
        log.warning("honcho.config.failed")
        return None


__all__ = ["build_honcho_client"]
