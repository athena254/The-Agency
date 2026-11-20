"""Human-approved promotion of factory-issued apprentices to the A1 rung."""

from __future__ import annotations

from agency.factory.service import AgentFactory, BlueprintStatus

A1_CAPABILITIES = ("forge", "forge.read", "forge.inspect")
A1_CEILINGS = (
    ("forge.read", "L1_SAFE_ANALYSIS"),
    ("forge.inspect", "L1_SAFE_ANALYSIS"),
)

_BASE_PROMPT_REFS = (
    ("agency-base-identity", "1.0.0"),
    ("agency-evidence-discipline", "1.0.0"),
    ("agency-l0-scope", "1.0.0"),
)
_A1_PROMPT_REFS = _BASE_PROMPT_REFS + (("apprentice-researcher", "1.0.0"),)
_CREATOR = "apprentice-promotion"


def _next_version(version: str) -> str:
    """Return the next patch version for an exact X.Y.Z string."""
    major, minor, patch = version.split(".")
    return f"{major}.{minor}.{int(patch) + 1}"


def promote_apprentice_a1(
    factory: AgentFactory, blueprint_id: str, *, approver: str, evidence: str
) -> str:
    """Promote the latest blueprint version to an A1 read/analysis identity."""
    versions = factory.list_versions(blueprint_id)
    if not versions:
        raise ValueError(f"unknown blueprint {blueprint_id!r}.")
    previous = versions[-1]
    if previous.status not in (BlueprintStatus.ACTIVE, BlueprintStatus.APPROVED):
        raise ValueError(
            f"promotion requires an ACTIVE or APPROVED blueprint, found {previous.status.value}."
        )
    if factory._is_prompt_published is None or factory._prompt_resolver is None:
        raise ValueError("promotion requires prompt publication callbacks (fail closed).")

    candidate = factory.create(
        blueprint_id,
        _next_version(previous.version),
        previous.name,
        domain=previous.domain,
        capabilities=A1_CAPABILITIES,
        capability_ceilings=A1_CEILINGS,
        prompt_refs=_A1_PROMPT_REFS,
        creator=_CREATOR,
    )
    factory.approve(
        candidate.blueprint_id,
        candidate.version,
        approver=approver,
        evidence=evidence,
    )
    agent = factory.activate(candidate.blueprint_id, candidate.version)
    factory.revoke(
        previous.blueprint_id,
        previous.version,
        evidence="superseded by A1 promotion",
    )
    return agent.id


__all__ = ["A1_CAPABILITIES", "A1_CEILINGS", "promote_apprentice_a1"]
