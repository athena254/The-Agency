"""PACK->blueprint mapping service (gaps.md #15).

Quarantined ``PromptKind.PACK`` drafts produced by ``MarkdownImportService``
gain a runtime identity path: a governed ``AgentFactory`` DRAFT blueprint
pinned to the prompt ref, with the import provenance as the evidence chain.
No auto-approval and no auto-activation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agency.agents.bootstrap import PACK_BLUEPRINT_CREATOR, pack_blueprint_from_prompt
from agency.factory.prompts import (
    PromptKind,
    PromptRegistry,
    PromptSpec,
    PromptStatus,
)
from agency.factory.requested_agent import MAX_PURPOSE_CHARS
from agency.factory.service import AgentFactory, BlueprintStatus

PROVENANCE = (
    'import-quarantine:{"source":"https://github.com/o/r","path":"AGENTS.md",'
    '"digest":"sha256:abc","license":"MIT"}'
)


def _register_pack(
    registry: PromptRegistry,
    prompt_id: str = "agents-1a2b3c4d5e6f",
    *,
    version: str = "0.0.0",
    name: str = "Ops Agent",
    description: str = "Run the ops rota and escalate incidents.",
    body: str = "You are an imported ops agent.",
    kind: PromptKind = PromptKind.PACK,
    status: PromptStatus = PromptStatus.DRAFT,
) -> PromptSpec:
    spec = PromptSpec(
        prompt_id=prompt_id,
        version=version,
        name=name,
        description=description,
        domain="agent",
        kind=kind,
        body=body,
        provenance=PROVENANCE,
        status=status,
    )
    return registry.register(spec)


def _service(tmp_path: Path) -> tuple[PromptRegistry, AgentFactory]:
    prompts = PromptRegistry(str(tmp_path / "prompts.db"))
    factory = AgentFactory(str(tmp_path / "factory.db"))
    return prompts, factory


def test_pack_blueprint_created_as_draft_with_provenance_evidence(tmp_path: Path) -> None:
    prompts, factory = _service(tmp_path)
    spec = _register_pack(prompts)

    result = pack_blueprint_from_prompt(prompts, factory, spec.prompt_id, spec.version)

    assert result.created is True
    assert result.blueprint_id == "ops-agent"
    assert result.version == "1.0.0"
    assert result.prompt_ref == (spec.prompt_id, spec.version)
    assert result.evidence == PROVENANCE
    bp = factory.get(result.blueprint_id, result.version)
    assert bp.status is BlueprintStatus.DRAFT
    assert bp.creator == PACK_BLUEPRINT_CREATOR
    assert bp.name == "Ops Agent"
    assert bp.capabilities == ()
    assert bp.prompt_refs == ((spec.prompt_id, spec.version),)


def test_pack_blueprint_dedup_skips_when_blueprint_exists(tmp_path: Path) -> None:
    prompts, factory = _service(tmp_path)
    spec = _register_pack(prompts)
    first = pack_blueprint_from_prompt(prompts, factory, spec.prompt_id, spec.version)

    second = pack_blueprint_from_prompt(prompts, factory, spec.prompt_id, spec.version)

    assert second.created is False
    assert second.blueprint_id == first.blueprint_id
    assert second.version == ""
    assert "already exists" in second.note
    assert len(factory.list_versions(first.blueprint_id)) == 1


def test_pack_blueprint_rejects_non_pack_kind(tmp_path: Path) -> None:
    prompts, factory = _service(tmp_path)
    spec = _register_pack(prompts, kind=PromptKind.SEGMENT)

    with pytest.raises(ValueError, match="PACK"):
        pack_blueprint_from_prompt(prompts, factory, spec.prompt_id, spec.version)
    assert factory.list_versions("ops-agent") == []


def test_pack_blueprint_rejects_non_draft_status(tmp_path: Path) -> None:
    prompts, factory = _service(tmp_path)
    spec = _register_pack(prompts)
    prompts.transition(spec.prompt_id, spec.version, PromptStatus.TESTING, evidence="promote")

    with pytest.raises(ValueError, match="DRAFT"):
        pack_blueprint_from_prompt(prompts, factory, spec.prompt_id, spec.version)
    assert factory.list_versions("ops-agent") == []


def test_pack_blueprint_unknown_prompt_ref_fails_closed(tmp_path: Path) -> None:
    prompts, factory = _service(tmp_path)

    with pytest.raises(KeyError):
        pack_blueprint_from_prompt(prompts, factory, "missing-prompt", "0.0.0")


def test_pack_blueprint_purpose_prefers_description(tmp_path: Path) -> None:
    prompts, factory = _service(tmp_path)
    spec = _register_pack(prompts)

    result = pack_blueprint_from_prompt(prompts, factory, spec.prompt_id, spec.version)

    assert result.purpose == "Run the ops rota and escalate incidents."


def test_pack_blueprint_purpose_falls_back_to_bounded_body_head(tmp_path: Path) -> None:
    prompts, factory = _service(tmp_path)
    spec = _register_pack(prompts, description="", body="x" * (MAX_PURPOSE_CHARS + 50))

    result = pack_blueprint_from_prompt(prompts, factory, spec.prompt_id, spec.version)

    assert result.purpose == "x" * MAX_PURPOSE_CHARS


def test_pack_blueprint_slug_is_factory_id_valid(tmp_path: Path) -> None:
    prompts, factory = _service(tmp_path)
    spec = _register_pack(prompts, name="Dev!! Ops (Agent) v2")

    result = pack_blueprint_from_prompt(prompts, factory, spec.prompt_id, spec.version)

    assert result.blueprint_id == "dev-ops-agent-v2"
    bp = factory.get(result.blueprint_id, result.version)
    assert bp.blueprint_id == result.blueprint_id


def test_pack_blueprint_long_name_slug_stays_bounded(tmp_path: Path) -> None:
    prompts, factory = _service(tmp_path)
    spec = _register_pack(prompts, name="a" * 200)

    result = pack_blueprint_from_prompt(prompts, factory, spec.prompt_id, spec.version)

    assert len(result.blueprint_id) <= 128
    factory.get(result.blueprint_id, result.version)
