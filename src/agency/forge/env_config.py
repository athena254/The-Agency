"""Load a supplied .env into the Forge process without leaking its values."""

from __future__ import annotations

import os
from pathlib import Path


def load_env_file(path: str | Path, *, overwrite: bool = False) -> tuple[str, ...]:
    """Parse KEY=VALUE lines and set missing environment variables.

    Returns only the key names; values are never logged or returned.
    """
    env_file = Path(path)
    if not env_file.is_file():
        return ()
    loaded: list[str] = []
    for raw in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        name = key.strip()
        if not name or (not overwrite and name in os.environ):
            continue
        cleaned = value.strip()
        if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in ("'", '"'):
            cleaned = cleaned[1:-1]
        os.environ[name] = cleaned
        loaded.append(name)
    return tuple(loaded)
