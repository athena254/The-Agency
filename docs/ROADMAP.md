# The Agency — Roadmap (honest implemented / partial / planned)

> **Normative direction:** `docs/SOURCE_CONSOLIDATED_BRIEF.md` §§45–46 (priorities and
> build sequence) and `docs/SPEC_AGENCY_CORE_RECONCILIATION.md` (code-backed gap matrix
> and first build slice). Status words below are coarse — Implemented / Exists /
> Partial / Planned / Missing — because the evidence does not warrant precise
> percentages. Nothing here is a production-readiness claim.

## Corrections to prior versions of this file

- **ResearchAgent was listed as "0% / not started" (Phase 4).** That was wrong:
  `src/agency/agents/research/agent.py` (`ResearchAgent`) exists. Corrected to
  "exists, partial" — end-to-end behavior was not verified in this docs pass.
- **CI/CD Pipeline was listed as "0% / planned" (Phase 5).** That was wrong:
  `.github/workflows/ci.yml` and `.github/workflows/cd.yml` exist. Corrected to
  "exists, outcome unverified" — workflow success and live operation were not
  verified in this docs pass.
- Absolute "Complete ✅" labels and precise percentages (20/30/40/60%) were removed;
  they implied evidence this pass did not establish.

## Where things stand

### Implemented or partially implemented (code exists, maturity varies)

| Item | State | Notes |
|------|-------|-------|
| Secrets management | Partial | `src/agency/memory/sms/secrets.py`, `src/agency/config/keys.py` |
| Retrieval / memory | Partial | `src/agency/memory/sms/store.py`, `retrieval.py`, `lifecycle.py`, `cpr.py` |
| Agent lifecycle scaffolding | Partial | `src/agency/kernel/registry.py`, `agents/registry.py`, `agents/loop.py`, `agents/executor.py` |
| Model routing (LLM harness) | Partial | `src/agency/llm/` (openai/anthropic/ollama/echo providers) |
| Butler gateway + routing | Partial | `src/agency/butler/`; sender history and trusted owner-scoped SQLite threads; HTTP thread selection awaits auth |
| Sandbox / process isolation | Partial | `src/agency/security/sandbox/`; isolation guarantees unaudited |
| QA / adversarial critics | Partial | `src/agency/agents/verifier.py`, `security/red|blue|purple/`; blocking decision contract planned |
| Research agent | Exists, partial | `src/agency/agents/research/agent.py`; behavior unverified here |
| General / demo agents, planner | Exists, partial | `src/agency/agents/` |
| Bridges (claude, codex, hermes, openclaw) | Partial adapters | `src/agency/bridges/`; optional integrations, never dependencies |
| Lattice + governance + reputation | Partial | `src/agency/lattice/`; durable vote persistence planned |
| API server + CLI | Partial | `src/agency/api/server.py`, `src/agency/cli/main.py` (`agency` entry point) |
| CI / CD workflows | Exists, outcome unverified | `.github/workflows/ci.yml`, `cd.yml` |
| Prototype factories (dark/ghost) | Prototypes only | Standalone; not an integrated Forge |

### New partial foundations and remaining planned systems

| Item | State | Planned next step (per reconciliation spec) |
|------|-------|----------------------------------------------|
| Durable thread / workspace / project store | Partial | SQLite thread/workspace store and Butler service path; authenticated API and project model pending |
| Agent Factory (versioned) | Planned | Spec validation → evaluation → versioned registry → deployment gate |
| Skill registry (Agency-native) | Partial | Versioned metadata, lifecycle evidence, explicit permissions; skill runtime binding pending |
| Workflow registry + deterministic executor | Partial | Bounded DAG validation, pinned versions, allowlisted operations; no Agent/Forge integration or crash resume |
| Forge (integrated) + native coding agent | Planned | Specify before implementing; reuse audited primitives; no complete Forge promised |
| Durable governance votes / approvals | Planned | Persist proposals/votes; human-approval flow |
| Workspace/project UI, branching | Planned | After thread isolation lands |
| Production deployment, observability, audit | Planned | Docker/K8s, OpenTelemetry, security audit — later phases |

`Athena-global-skills` (`https://github.com/athena254/Athena-global-skills`) is a
separate pattern source for future skills/Forge work, not a dependency and not
auto-imported.

## Build sequence (from the brief and reconciliation spec)

1. **Architecture reconciliation** — this docs pass (canonical boundaries, honest statuses).
2. **Thread context foundation** — store, ownership checks, Butler internal routing implemented; authenticated public API pending.
3. **Agent Factory prerequisites** — skill and workflow registries implemented as partial foundations; factory pending.
4. **Forge** — architecture, bounded coding-agent profile, software-factory workflows.
5. **Security and governance hardening** — capability permissions, sandbox audit, approval gates.
6. **Competitive maturity** — benchmarks, UX, documented differentiation (brief §22).

Each phase needs its own spec, tests, and review. The consolidated brief's 51 sections
remain the target requirements.

## Historical sprint notes (preserved, not current status)

- **Sprint 1 (historical claim):** core services, reorganization, auto-research,
  simulation, QA suite, git adoption, concurrent addon design. Preserved as reported
  history; not re-verified against current code here.
- **Sprint 2 (historical claim ~30%):** Lattice + governance + finance domain target;
  reported as scaffolding done, domain agents not started — except that the research
  agent module does exist (see correction above).
- Old timeline estimates (v0.1.0/v0.2.0 "complete", v0.3.0/v0.4.0/v1.0.0 week counts)
  are withdrawn: they rested on the same unverified percentages removed above. No new
  dates are promised here.

## Full system specification

For the target system (architecture, agents, addons, memory, bridges, data models,
deployment), see `docs/SOURCE_CONSOLIDATED_BRIEF.md` — it is the canonical reference
for direction, not a claim about what is built.
