"""Local prototype UI/API package for The Agency MVP.

Exposes :func:`agency.prototype.app.create_app`, the FastAPI application that
serves the same-origin local browser UI and a restricted Butler chat API.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agency.prototype.app import create_app as create_app

__all__ = ["create_app"]


def __getattr__(name: str) -> Any:
    if name == "create_app":
        from agency.prototype.app import create_app

        return create_app
    raise AttributeError(name)
