"""Agency HTTP API package.

Routers live in :mod:`agency.api.routers`; :mod:`agency.api.server` builds
the FastAPI application and wires shared state (task manager, registries,
stores, risk engine, bridge coordinator).
"""

from __future__ import annotations
