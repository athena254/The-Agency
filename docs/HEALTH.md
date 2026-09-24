# The Agency — Project Health Assessment (honest)

> Aligned with `README.md`, `docs/ARCHITECTURE.md`, and `docs/ROADMAP.md`.
> Direction: `docs/SOURCE_CONSOLIDATED_BRIEF.md`; code-backed gaps:
> `docs/SPEC_AGENCY_CORE_RECONCILIATION.md`. Coarse states only
> (Exists / Partial / Planned / Missing) — no precise percentages, no
> production-readiness claims. CI success and live operation were not verified
> in this docs pass.

## Executive Summary

**Overall status**: scaffolding exists across the pipeline (Butler → orchestrator →
agents/tools, Lattice, memory, policies/audit/sandbox), but the differentiators —
durable threads, Agent Factory, skill/workflow registries, integrated Forge, durable
governance — are planned, not built. Two prior status tables were factually wrong
(research agent "0%", CI/CD "0%"); both are corrected below.

## Component Health

### Foundation and runtime: Partial

| Component | State | Notes |
|-----------|-------|-------|
| Secrets management | Partial | `src/agency/memory/sms/secrets.py`, `config/keys.py`; ACL/rotation story unproven |
| Retrieval / memory | Partial | `src/agency/memory/sms/`; sender-scoped, hierarchical isolation planned |
| Agent lifecycle scaffolding | Partial | `kernel/registry.py`, `agents/registry.py`, `agents/loop.py`; no factory gate |
| Model routing | Partial | `src/agency/llm/` multi-provider; capability/cost metadata incomplete |
| API server + CLI | Partial | `src/agency/api/server.py`, `cli/main.py`; live operation unverified |
| Tests | Partial | Suite exists under `tests/`; prior "155 tests passing" count not re-verified here |

Prior verdicts of "solid, production-ready foundation" are withdrawn: the code exists
but production hardness was not established in this pass.

### Agents, tools, bridges: Partial

| Component | State | Notes |
|-----------|-------|-------|
| Butler gateway | Partial | Routing + audit work; history is sender-scoped (`chat-{sender}`), no thread store |
| Research agent | Exists, partial | `src/agency/agents/research/agent.py` exists — prior "not started" rows were wrong; behavior unverified |
| General / demo / planner / verifier | Exists, partial | `src/agency/agents/`; eval coverage not claimed |
| Tools (registry, driver, builtin) | Partial | `src/agency/tools/`; deterministic-first per brief, permission story incomplete |
| Bridges (claude, codex, hermes, openclaw) | Partial adapters | `src/agency/bridges/`; optional integrations behind explicit boundaries |

### Coordination and governance: Partial

| Component | State | Notes |
|-----------|-------|-------|
| Lattice | Partial | `lattice/api.py`, `backends/sqlite.py`, `models.py`, `factory.py`; ownership/approval APIs planned |
| Governance | Partial | `lattice/governance.py`: in-memory proposals, reputation-weighted votes, best-effort backend mirror; durable persistence planned — do not label durable |
| Reputation | Partial | `lattice/reputation.py`; weight calibration unproven |
| QA / adversarial critics | Partial | `agents/verifier.py`, `security/red|blue|purple/`; formal blocking contract planned |
| Sandbox / security / audit / evidence / risk | Partial | `security/sandbox/`, `kernel/audit.py`, `kernel/policies.py`, `evidence/`, `risk/`; isolation guarantees unaudited |

### Planned (the actual product gaps): Missing as integrated systems

| Component | State | Notes |
|-----------|-------|-------|
| Durable threads / workspaces | Planned | No module under `src/agency`; other branches working on threads — not claimed here |
| Agent Factory (versioned) | Planned | No factory gate; follows skill/workflow contracts per reconciliation spec |
| Skill registry / workflow registry + executor | Planned | No Agency-native modules; other branches working on skills/workflows — not claimed here |
| Forge + native coding agent | Planned | `addons/dark_factory/`, `ghost_factory` are prototypes, not an integrated factory; no complete Forge promised |
| CI/CD lineage | Exists, outcome unverified | `.github/workflows/ci.yml`, `cd.yml` exist — prior "planned" rows were wrong; success not verified |

**ATHENA boundary:** ATHENA is a separate peer platform, not an Agency component
(brief §2). Old diagrams showing ATHENA Core inside the Agency are
historical/superseded — see `docs/ARCHITECTURE.md` §§0–4. `Athena-global-skills` is a
separate pattern source, not a dependency.

## Strengths (observed in code, not overstated)

1. **Real scaffolding across the whole pipeline** — Butler, orchestrator, agents,
   tools, Lattice, memory, audit, sandbox all have code, not just docs.
2. **Governance has an executable core** — proposal/vote/quorum logic exists
   (`lattice/governance.py`), even though durability is planned.
3. **Security and audit are structural** — policies, audit log, evidence, and critics
   are separate modules, not prompt text.
4. **CI workflows exist** — lint/typecheck/test/security jobs are defined
   (outcomes unverified here, but the definitions are real).
5. **Direction is written down and preserved** — the 51-section brief and the
   reconciliation spec give every future phase a normative target.

## Critical Risks

### Risk 1: Differentiators still missing
The thread store, Agent Factory, skill/workflow registries, and Forge — the systems
that make the Agency more than an agent runner — are planned, not built.
**Mitigation**: follow the reconciliation build slice in order; do not claim outputs
from other branches until they land and pass gates.

### Risk 2: Governance not durable
Votes live in memory (`_proposals` dict) with best-effort backend mirroring.
A restart loses open proposals.
**Mitigation**: persist proposals/votes before calling governance durable.

### Risk 3: Sandbox and security unaudited
Isolation, secret handling, and permission enforcement exist as code but their
guarantees have not been audited.
**Mitigation**: audit actual isolation before any production or high-impact claims.

### Risk 4: Status drift (repeat of this pass's findings)
Prior docs claimed ResearchAgent 0%, CI/CD not started, ATHENA-as-core, and
production readiness — all contradicted by the code or by lack of evidence.
**Mitigation**: keep the coarse-state convention; every status claim cites a
file/line; brief §50 rule stands — never document planned functionality as
implemented.

### Risk 5: Unverified operations
CI workflow success, test counts, and live server/Butler operation were not verified
in this docs pass.
**Mitigation**: verify with `python -m pytest tests/ -q`, `ruff check`, CI runs, and
a smoke test before claiming health in numbers.

## Recommendations

### Immediate
1. Keep this docs pass uncommitted and reviewable; only the four owned files changed.
2. Land the thread-store slice first (durable identity, owner checks, isolation tests).
3. Then skill registry → workflow registry/executor → Agent Factory, per the
   reconciliation spec. Forge, durable approvals, and release automation after.

### Short-term
4. Persist Lattice governance votes and approvals; define the QA blocking contract.
5. Audit sandbox isolation and permission enforcement at the tool/resource boundary.
6. Re-baseline any timeline only from demonstrated gates, not percentages.

### Medium-term
7. Specify then build the integrated Forge and bounded coding agent.
8. Workspace/project UI and thread branching after isolation is proven.
9. External skill adaptation, distributed execution, competitive benchmarks last.

## Reality vs. roadmap

| Milestone | Honest state |
|-----------|--------------|
| Scaffolding (Butler/orchestrator/agents/tools/Lattice/memory/audit/sandbox) | Partial — code exists, maturity varies |
| Research agent, CI workflows | Exist — outcomes/behavior unverified here |
| Threads, Agent Factory, skills, workflows, Forge, durable governance | Planned |
| Production deployment | Planned; no date promised |

Old milestone tables with precise week counts and "on track / behind" deltas are
withdrawn — they rested on the unverified percentages removed above.

## Simple Summary

The Agency has working scaffolding and a clear, preserved target design, but its
defining systems are still planned. Prior docs overstated readiness and understated
what exists (research agent, CI). This assessment corrects both directions and holds
future claims to file-and-line evidence.

*No precise grade or percentage is given — the evidence does not warrant one.*
