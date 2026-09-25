"""Local prototype UI/API package for The Agency MVP.

Exposes :func:`agency.prototype.app.create_app`, the FastAPI application that
serves the same-origin local browser UI and a restricted Butler chat API.
"""

from __future__ import annotations

from agency.prototype.app import create_app

__all__ = ["create_app"]
