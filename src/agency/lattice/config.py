"""Lattice configuration loading and validation.

Precedence (highest wins):

1. ``LATTICE_*`` environment variables.
2. ``config/lattice.yaml`` (``lattice:`` mapping per SPEC_LATTICE.md section 8).
3. Built-in defaults on :class:`~agency.lattice.models.LatticeConfig`.

Defaults to the SQLite backend so core operation needs zero external
dependencies. Use :func:`get_config` for the process-wide singleton and
:func:`reset_config` in tests.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import structlog

from agency.lattice.models import LatticeConfig

log = structlog.get_logger(__name__)

DEFAULT_CONFIG_PATH = Path("config/lattice.yaml")

_VALID_BACKENDS: frozenset[str] = frozenset({"sqlite", "neo4j"})

_config_singleton: LatticeConfig | None = None


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None or value == "":
        return None
    return value


def _load_yaml_file(path: str | Path | None) -> dict[str, Any]:
    """Read the ``lattice:`` mapping from a YAML file (empty dict if absent)."""

    candidate = Path(path).expanduser() if path is not None else DEFAULT_CONFIG_PATH
    if not candidate.is_file():
        return {}
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - PyYAML is a test/dev dep
        raise RuntimeError("PyYAML is required to read config/lattice.yaml") from exc
    raw: Any = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise TypeError(f"Lattice config {candidate} must contain a YAML mapping.")
    lattice = raw.get("lattice", raw)
    if not isinstance(lattice, dict):
        raise TypeError(f"Lattice config {candidate} 'lattice' key must be a mapping.")
    return lattice


def _flatten_yaml(data: dict[str, Any]) -> dict[str, Any]:
    """Flatten the nested SPEC_LATTICE.md section 8 layout to flat kwargs."""

    flat: dict[str, Any] = {}
    if not data:
        return flat

    def _sub(*names: str) -> dict[str, Any]:
        for name in names:
            section = data.get(name)
            if isinstance(section, dict):
                return section
        return {}

    if isinstance(data.get("backend"), str):
        flat["backend"] = data["backend"]

    sqlite = _sub("sqlite")
    if isinstance(sqlite.get("path"), str):
        flat["sqlite_path"] = sqlite["path"]

    neo4j = _sub("neo4j")
    for key, target in (
        ("uri", "neo4j_uri"),
        ("user", "neo4j_user"),
        ("password", "neo4j_password"),
        ("database", "neo4j_database"),
    ):
        if neo4j.get(key) is not None:
            flat[target] = neo4j[key]

    qdrant = _sub("qdrant")
    if qdrant.get("url") is not None:
        flat["qdrant_url"] = qdrant["url"]
    if qdrant.get("api_key") is not None:
        flat["qdrant_api_key"] = qdrant["api_key"]

    vectors = _sub("vectors")
    if vectors.get("model") is not None:
        flat["vector_model"] = vectors["model"]
    if vectors.get("dimension") is not None:
        flat["vector_dimension"] = vectors["dimension"]

    events = _sub("events")
    if events.get("retention_days") is not None:
        flat["event_retention_days"] = events["retention_days"]
    if events.get("prune_enabled") is not None:
        flat["prune_enabled"] = events["prune_enabled"]

    governance = _sub("governance")
    if governance.get("default_quorum") is not None:
        flat["default_quorum"] = governance["default_quorum"]
    if governance.get("proposal_ttl_seconds") is not None:
        flat["proposal_ttl_seconds"] = governance["proposal_ttl_seconds"]
    if governance.get("reputation_window") is not None:
        flat["reputation_window"] = governance["reputation_window"]

    # Already-flat files are also accepted.
    for key in LatticeConfig.__dataclass_fields__:
        if key not in flat and data.get(key) is not None:
            flat[key] = data[key]
    return flat


def _apply_env(flat: dict[str, Any]) -> dict[str, Any]:
    """Overlay ``LATTICE_*`` environment variables onto flat kwargs."""

    mapping: dict[str, str] = {
        "LATTICE_BACKEND": "backend",
        "LATTICE_SQLITE_PATH": "sqlite_path",
        "LATTICE_NEO4J_URI": "neo4j_uri",
        "LATTICE_NEO4J_USER": "neo4j_user",
        "LATTICE_NEO4J_PASSWORD": "neo4j_password",
        "LATTICE_NEO4J_DATABASE": "neo4j_database",
        "LATTICE_QDRANT_URL": "qdrant_url",
        "LATTICE_QDRANT_API_KEY": "qdrant_api_key",
        "LATTICE_VECTOR_MODEL": "vector_model",
        "LATTICE_VECTOR_DIMENSION": "vector_dimension",
        "LATTICE_EVENT_RETENTION_DAYS": "event_retention_days",
        "LATTICE_PRUNE_ENABLED": "prune_enabled",
        "LATTICE_DEFAULT_QUORUM": "default_quorum",
        "LATTICE_PROPOSAL_TTL_SECONDS": "proposal_ttl_seconds",
        "LATTICE_REPUTATION_WINDOW": "reputation_window",
    }
    for env_name, target in mapping.items():
        value = _env(env_name)
        if value is not None:
            flat[target] = value
    # ``${VAR}`` placeholders inside YAML resolve against the environment.
    for key, value in list(flat.items()):
        if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
            inner = value[2:-1]
            resolved = os.environ.get(inner, "")
            flat[key] = resolved if resolved != "" else None
    return flat


def _coerce_types(flat: dict[str, Any]) -> dict[str, Any]:
    """Coerce stringly-typed env/YAML values to :class:`LatticeConfig` types."""

    def _int(key: str) -> None:
        if key in flat and not isinstance(flat[key], int):
            flat[key] = int(str(flat[key]).strip())

    def _float(key: str) -> None:
        if key in flat and not isinstance(flat[key], float):
            flat[key] = float(str(flat[key]).strip())

    def _bool(key: str) -> None:
        if key in flat and not isinstance(flat[key], bool):
            flat[key] = str(flat[key]).strip().lower() in ("1", "true", "yes", "on")

    for key in (
        "vector_dimension",
        "event_retention_days",
        "proposal_ttl_seconds",
        "reputation_window",
    ):
        _int(key)
    for key in ("default_quorum",):
        _float(key)
    _bool("prune_enabled")
    if "backend" in flat and flat["backend"] is not None:
        flat["backend"] = str(flat["backend"]).strip().lower() or "sqlite"
    for key in ("neo4j_uri", "neo4j_user", "neo4j_password", "qdrant_url", "qdrant_api_key"):
        if key in flat and flat[key] == "":
            flat[key] = None
    return flat


def validate_config(config: LatticeConfig) -> list[str]:
    """Check configuration coherence; return a list of problems (empty == valid)."""

    errors: list[str] = []
    if config.backend not in _VALID_BACKENDS:
        errors.append(
            f"backend {config.backend!r} is invalid; expected one of {sorted(_VALID_BACKENDS)}."
        )
    if not (config.sqlite_path or "").strip():
        errors.append("sqlite_path must not be empty.")
    if config.vector_dimension <= 0:
        errors.append(f"vector_dimension {config.vector_dimension} must be > 0.")
    if not (config.vector_model or "").strip():
        errors.append("vector_model must not be empty.")
    if config.event_retention_days <= 0:
        errors.append(f"event_retention_days {config.event_retention_days} must be > 0.")
    if not 0.0 < config.default_quorum <= 1.0:
        errors.append(f"default_quorum {config.default_quorum} must be within (0.0, 1.0].")
    if config.proposal_ttl_seconds <= 0:
        errors.append(f"proposal_ttl_seconds {config.proposal_ttl_seconds} must be > 0.")
    if config.reputation_window <= 0:
        errors.append(f"reputation_window {config.reputation_window} must be > 0.")
    if config.backend == "neo4j" and not (config.neo4j_uri or "").strip():
        errors.append("neo4j_uri is required when backend is 'neo4j'.")
    return errors


def load_config(config_path: str | Path | None = None) -> LatticeConfig:
    """Build a validated :class:`LatticeConfig` from file + environment.

    Raises :exc:`ValueError` when validation fails.
    """

    flat = _flatten_yaml(_load_yaml_file(config_path))
    flat = _coerce_types(_apply_env(flat))
    config = LatticeConfig(**flat)
    errors = validate_config(config)
    if errors:
        log.info("lattice.config.invalid", error_count=len(errors))
        raise ValueError("; ".join(errors))
    log.info("lattice.config.loaded", backend=config.backend)
    return config


def get_config(config_path: str | Path | None = None) -> LatticeConfig:
    """Return the process-wide :class:`LatticeConfig` singleton.

    The first call loads (and caches) the config; pass ``config_path`` only
    on that first call. Use :func:`reset_config` in tests.
    """

    global _config_singleton
    if _config_singleton is None:
        _config_singleton = load_config(config_path)
    return _config_singleton


def reset_config() -> None:
    """Drop the cached singleton so the next :func:`get_config` reloads."""

    global _config_singleton
    _config_singleton = None


__all__ = ["get_config", "load_config", "reset_config", "validate_config"]
