"""Agency CLI package.

Exposes the Typer application defined in :mod:`agency.cli.main` so both
``python -m agency.cli.main`` and the ``agency`` console script work::

    agency --help
    agency status
    agency agent list
"""

from __future__ import annotations

from agency.cli.main import app, main

__all__ = ["app", "main"]
