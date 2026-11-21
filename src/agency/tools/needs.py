"""Domain tool-needs catalog loader: profile -> required/optional tools.

Reads ``data/tool_needs.yaml`` (declaration data, not code). Frozen pydantic
models; a missing file is an honest empty mapping, invalid YAML fails closed
with ValueError, wrong-shaped documents fail closed with TypeError.
Resolution/provisioning happens elsewhere (Phase 2); this module never
blocks blueprint creation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

# Repository root = parents[3] of this file (src/agency/tools/needs.py),
# mirroring src/agency/agents/coding.py.
DEFAULT_NEEDS_PATH = Path(__file__).resolve().parents[3] / "data" / "tool_needs.yaml"


class ToolNeeds(BaseModel):
    """Tool needs declared for one capability/domain profile."""

    model_config = ConfigDict(frozen=True)

    name: str
    required: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()


def _tool_names(value: Any, profile: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise TypeError(f"tool-needs profile {profile!r} requires list tool fields.")
    out: list[str] = []
    for entry in value:
        if not isinstance(entry, str):
            raise TypeError(f"tool-needs profile {profile!r} entries must be strings.")
        name = entry.strip()
        if not name:
            raise ValueError(f"tool-needs profile {profile!r} entries must be nonblank.")
        out.append(name)
    return tuple(out)


def _parse_profiles(data: Any) -> dict[str, ToolNeeds]:
    if not isinstance(data, dict):
        raise TypeError("tool-needs catalog must be a mapping.")
    profiles = data.get("profiles")
    if not isinstance(profiles, dict):
        raise TypeError("tool-needs catalog requires a 'profiles' mapping.")
    out: dict[str, ToolNeeds] = {}
    for name, entry in profiles.items():
        if not isinstance(entry, dict):
            raise TypeError(f"tool-needs profile {name!r} must be a mapping.")
        out[str(name)] = ToolNeeds(
            name=str(name),
            required=_tool_names(entry.get("required"), str(name)),
            optional=_tool_names(entry.get("optional"), str(name)),
        )
    return out


def load_tool_needs(path: str | Path | None = None) -> dict[str, ToolNeeds]:
    """Load profile -> ToolNeeds from YAML; missing file -> empty mapping."""
    needs_path = Path(path) if path is not None else DEFAULT_NEEDS_PATH
    if not needs_path.exists():
        return {}
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - PyYAML is a runtime dependency
        raise RuntimeError("PyYAML is required to read the tool-needs catalog") from exc
    try:
        data = yaml.safe_load(needs_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"tool-needs catalog is not valid YAML: {exc}") from None
    return _parse_profiles(data)


def default_requirements() -> tuple[str, ...]:
    """Return the ``default`` profile's required+optional names, sorted, deduped."""
    entry = load_tool_needs().get("default")
    if entry is None:
        raise KeyError("tool-needs catalog is missing the 'default' profile.")
    return tuple(sorted(set(entry.required) | set(entry.optional)))


__all__ = ["DEFAULT_NEEDS_PATH", "ToolNeeds", "default_requirements", "load_tool_needs"]
