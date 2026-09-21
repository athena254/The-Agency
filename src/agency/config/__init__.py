"""The Agency configuration system.

Centralises runtime configuration (see :mod:`agency.config.settings`),
per-provider LLM API key management (see :mod:`agency.config.keys`) and the
``agency config`` command-line interface (see :mod:`agency.config.cli`).

Security note: key material is never written to logs. Anywhere a key would
appear in user-facing output it is masked (first/last few characters only).
"""

from __future__ import annotations

from agency.config.keys import APIKeyManager
from agency.config.settings import AgencySettings

__all__ = ["APIKeyManager", "AgencySettings"]
