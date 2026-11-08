"""Public package exports, loaded only when explicitly requested.

Importing persistence DTOs must not import a server or execution runtime.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "ButlerConfig": ("agency.butler.config", "ButlerConfig"),
    "DOMAIN_KEYWORDS": ("agency.butler.router", "DOMAIN_KEYWORDS"),
    "MessageRouter": ("agency.butler.router", "MessageRouter"),
    "app": ("agency.butler.server", "app"),
    "create_app": ("agency.butler.server", "create_app"),
    "ButlerService": ("agency.butler.service", "ButlerService"),
}

__all__ = ["DOMAIN_KEYWORDS", "ButlerConfig", "ButlerService", "MessageRouter", "app", "create_app"]


def __getattr__(name: str) -> Any:
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module, attribute = _EXPORTS[name]
    value = getattr(import_module(module), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
