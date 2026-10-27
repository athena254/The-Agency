"""Bundled prompt library: shared segments plus REAP investigation packs.

Seed data is declarative: ``manifest.yaml`` describes each record and points
at a markdown body. REAP bodies are verbatim upstream prompt text (MIT) with
the upstream YAML frontmatter stripped; see ``THIRD_PARTY_LICENSES.md``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agency.factory.prompts import (
    PromptKind,
    PromptRegistry,
    PromptSpec,
    PromptStatus,
)

DATA_DIR = Path(__file__).resolve().parent
MANIFEST_PATH = DATA_DIR / "manifest.yaml"


def _load_manifest() -> dict[str, Any]:
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - PyYAML is a runtime dependency
        raise RuntimeError("PyYAML is required to read the prompt manifest") from exc
    with MANIFEST_PATH.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise TypeError("prompt manifest must be a mapping.")
    return data


def builtin_specs() -> list[PromptSpec]:
    """Return all bundled prompt records in manifest order."""
    data = _load_manifest()
    prompts = data.get("prompts")
    if not isinstance(prompts, list):
        raise TypeError("prompt manifest requires a 'prompts' list.")
    specs: list[PromptSpec] = []
    for entry in prompts:
        if not isinstance(entry, dict):
            raise TypeError("manifest prompt entries must be mappings.")
        body = (DATA_DIR / str(entry["file"])).read_text(encoding="utf-8")
        specs.append(
            PromptSpec(
                prompt_id=str(entry["id"]),
                version=str(entry["version"]),
                name=str(entry["name"]),
                description=str(entry.get("description", "")),
                domain=str(entry["domain"]),
                kind=PromptKind(str(entry["kind"])),
                body=body,
                provenance=str(entry["provenance"]),
                status=PromptStatus.PUBLISHED,
            )
        )
    return specs


def ensure_builtin_prompts(registry: PromptRegistry) -> int:
    """Seed bundled prompts idempotently; return the number newly inserted."""
    inserted = 0
    for spec in builtin_specs():
        if registry.seed_builtin(spec, evidence=spec.provenance):
            inserted += 1
    return inserted


__all__ = ["DATA_DIR", "MANIFEST_PATH", "builtin_specs", "ensure_builtin_prompts"]
