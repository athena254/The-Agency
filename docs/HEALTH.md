# The Agency — Project Health Assessment (honest)

> Aligned with `README.md`, `docs/ARCHITECTURE.md`, and `docs/ROADMAP.md`.
> Direction: `docs/SOURCE_CONSOLIDATED_BRIEF.md`; code-backed gaps:
> `docs/SPEC_AGENCY_CORE_RECONCILIATION.md`. Coarse states only
> (Exists / Partial / Planned / Missing) — no precise percentages, no
> production-readiness claims. PR #7 CI is verified at `5c4720c`; a deployed PR
> runtime and full product security audit are not verified.

## Executive Summary

**Overall status**: scaffolding exists across the pipeline (Butler → orchestrator →
agents/tools, Lattice, memory, policies/audit/sandbox), but the differentiators —
authenticated multi-threaded work, autonomous Agent Factory, integrated Forge, durable
governance — remain incomplete. Thread, skill and workflow foundations now exist.
Two prior status tables were factually wrong
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
| Butler gateway | Partial | Sender history plus owner-scoped SQLite thread history for trusted callers; HTTP thread selection blocked pending auth |
| Research agent | Exists, partial | Offline integration tests exercise routing; no live-provider certification from CI |
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

### Foundations and remaining product gaps

| Component | State | Notes |
|-----------|-------|-------|
| Durable threads / workspaces | Partial | `butler/threads.py` persists owner-scoped threads/messages for trusted channels; public HTTP is anonymous/stateless pending authentication and project UI |
| Agent Factory (versioned) | Partial v1 | `factory/service.py` versioned blueprints, approval gate, L0/UNKNOWN identity activation; no evaluation, automatic grants, or restart rehydration |
| Skill registry / workflow registry + executor | Partial | Versioned registries and a bounded deterministic runner exist; no agent/Forge integration yet |
| Forge + native coding agent | Partial inspection gate | `forge/inspection.py` read-only inventory/syntax reports; no tests, coding agent, security review or release; legacy prototypes are separate |
| CI/CD lineage | CI verified, CD unverified | All seven GitHub CI checks passed for PR #7 commit `5c4720c`; this is not deployment or production approval |

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
4. **CI workflows pass on the reviewed PR head** — lint/format, typecheck,
   four platform/interpreter test jobs, and the security scan passed at `5c4720c`.
5. **Direction is written down and preserved** — the 51-section brief and the
   reconciliation spec give every future phase a normative target.

## Critical Risks

### Risk 1: Differentiators still missing
autonomous Agent Factory, integrated Forge, authenticated thread API and end-to-end skill/workflow integration
are still missing; existing registry/runner foundations alone are not the finished product.
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

### Risk 5: Unverified deployment and live behavior
The PR's local suite (628 passed, 17 warnings), offline smoke, and seven GitHub
CI checks passed at `5c4720c`. This does not test live-provider replies or
deploy the PR code to the existing bot. The current `main` bot must not be
mistaken for a running instance of the PR branch.
**Mitigation**: test the deployed revision and a real user-to-bot reply only
after review and an authorized rollout; retain the existing warnings as debt.

## Recommendations

### Immediate
1. Review and harden the integrated thread, skill and workflow foundations.
2. Add real authentication before enabling thread selection through HTTP.
3. Then Agent Factory, per the
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
| Research agent, CI workflows | Offline research tests exist; all seven PR #7 CI checks passed at `5c4720c`; live-provider behavior is unverified |
| Thread store, skill registry, workflow runner | Partial foundations; tests pass, full runtime integration pending |
| Agent Factory v1, Forge inspection gate | Partial foundations |
| Autonomous Agent Factory, full Forge, durable governance | Planned |
| Production deployment | Planned; no date promised |

Old milestone tables with precise week counts and "on track / behind" deltas are
withdrawn — they rested on the unverified percentages removed above.

## Simple Summary

The Agency has working scaffolding and partial thread, skill, and workflow foundations,
but its full governed product is not built: autonomous Agent Factory, integrated Forge, durable governance,
and authenticated thread access remain planned. Prior docs overstated readiness and
understated what exists (research agent, CI). Claims remain tied to code evidence.

*No precise grade or percentage is given — the evidence does not warrant one.*
