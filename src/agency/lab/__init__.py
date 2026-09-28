"""Lab-only packages for engine and peer experiments.

NOTHING in this package is imported by production Agency code paths. The
``comet_smoke`` module drives real ``cometbft`` binaries on loopback only and
is deliberately stdlib-only.
"""

from __future__ import annotations

__all__: list[str] = []
