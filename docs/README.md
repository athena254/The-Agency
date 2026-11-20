# Documentation index

Start with [CURRENT_STATE.md](CURRENT_STATE.md). It is the authoritative status
for the current revision and says what was actually verified. The two normative
direction documents are [SOURCE_CONSOLIDATED_BRIEF.md](SOURCE_CONSOLIDATED_BRIEF.md)
(target design) and [SPEC_AGENCY_CORE_RECONCILIATION.md](SPEC_AGENCY_CORE_RECONCILIATION.md)
(gap matrix between target and code).

## Status and planning

- [CURRENT_STATE.md](CURRENT_STATE.md): implemented surfaces, verification, limits.
- [ROADMAP.md](ROADMAP.md): build order.
- [KNOWN_ISSUES.md](KNOWN_ISSUES.md): open problems.
- [PRODUCTION_READINESS_PLAN.md](PRODUCTION_READINESS_PLAN.md): gated engineering plan.
- [OWNER_DECISIONS_D01_D05.md](OWNER_DECISIONS_D01_D05.md) and
  [OWNER_DECISIONS_D06_SLICE_A_CONTRACT.md](OWNER_DECISIONS_D06_SLICE_A_CONTRACT.md):
  approved policy inputs.

## Architecture and guides

- [ARCHITECTURE.md](ARCHITECTURE.md): current structure, with superseded diagrams labeled.
- [DEPLOYMENT.md](DEPLOYMENT.md): container artifacts and box deployment contract.
- [BETA_RUNBOOK.md](BETA_RUNBOOK.md) and [BETA_GAPS.md](BETA_GAPS.md): beta boundary operations.
- [OWNER_PILOT_LAUNCHER.md](OWNER_PILOT_LAUNCHER.md): running the single-owner pilot.
- [GUI_DESIGN.md](GUI_DESIGN.md): Mission Control console design.
- [HONCHO_ADAPTER.md](HONCHO_ADAPTER.md) and [ADR-honcho-memory-adapter.md](ADR-honcho-memory-adapter.md):
  optional memory mirror.
- [FACTORY_WORKFLOW.md](FACTORY_WORKFLOW.md) and [FORGE_FACTORY_PLAN.md](FORGE_FACTORY_PLAN.md):
  factory workflow and Forge direction.
- [BOUNDED_CODING_PILOT.md](BOUNDED_CODING_PILOT.md): the bounded coding fixture.
- [E2E_HARNESS.md](E2E_HARNESS.md): user-journey template for end-to-end checks.
- [COMPLETE_REFERENCE.md](COMPLETE_REFERENCE.md), [HEALTH.md](HEALTH.md),
  [PARALLEL_ORCHESTRATION.md](PARALLEL_ORCHESTRATION.md): reference material.

## Specs

Every `SPEC_*.md` file is a component spec. Specs are frozen contract inputs:
Forge treats `docs/SPEC*.md` as protected paths, so edits need explicit human
approval. [SPEC.md](SPEC.md) and [SPECIFICATION.md](SPECIFICATION.md) are the
older umbrella documents; the named component specs supersede them where they
disagree.

## Archive

[archive/](archive/README.md) holds dated handoffs, research notes, qualification
and review records, and their evidence files. They are historical provenance,
not current status. If an archived document disagrees with CURRENT_STATE.md,
CURRENT_STATE.md wins.
