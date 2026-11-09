# The Agency — Roadmap (honest implemented / partial / planned)

> November 9, 2026 update: the current source-backed feature ledger is [CURRENT_STATE.md](CURRENT_STATE.md). The active engineering order is P0 reproducible baseline -> P1 end-to-end identity/budget/audit -> P2 peer application authority -> P3 isolation/privacy/recovery -> P4 Factory/skills/Forge integration -> P5 deployment operations -> P6 independent release review. See [PRODUCTION_READINESS_PLAN.md](PRODUCTION_READINESS_PLAN.md) for dependencies and acceptance criteria. The owner decisions D-01–D-05 are approved and recorded in [OWNER_DECISIONS_D01_D05.md](OWNER_DECISIONS_D01_D05.md). The hermetic Python suite, owner pilot service/API/UI, persistent task workspace and Mission Control console now exist as bounded local surfaces; none of them is production readiness or peer authority.

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
- **The October 18 status ledger described a blocked suite and 16 Node UI tests.**
  Superseded: the suite is now hermetic (`tests/conftest.py` pins settings and
  isolates every runtime database) and records 3513 passed, 3 skipped, 4
  Windows-only harness errors; 79 Node UI tests pass. See `CURRENT_STATE.md`.
- **The owner pilot service/API/UI, persistent task workspace and Mission Control
  console were absent from the earlier ledger.** They now exist as bounded local
  surfaces and are listed below. They do not change the P2 gap.
- Absolute "Complete ✅" labels and precise percentages (20/30/40/60%) were removed;
  they implied evidence this pass did not establish. No new percentages or dates
  are promised here.

## Where things stand

### Implemented or partially implemented (code exists, maturity varies)

| Item | State | Notes |
|------|-------|-------|
| Owner pilot service + API | Implemented (local) | `src/agency/admission/`, `prototype/pilot.py`; loopback bearer + CSRF; `calculator`, `utc_time`, `web_search` only |
| Persistent task workspace | Implemented (local) | owner-scoped SQLite threads/messages/tasks; replay, cancel, UNCERTAIN recovery; no auto-resume |
| Mission Control console | Implemented (local) | `gui/` React 18 + TS + Vite + Tailwind; built to `static/mission-control`; served at `/mission-control`; honest locked states for P2 surfaces |
| Pilot workspace UI | Implemented (local) | dependency-free `static/pilot.html|pilot.js|pilot.css` at `/pilot` |
| Hermetic test suite | Implemented | `tests/conftest.py` isolates env and DBs; 3513 passed, 3 skipped, 4 Windows-only harness errors |
| Secrets management | Partial | `src/agency/memory/sms/secrets.py`, `src/agency/config/keys.py` |
| Retrieval / memory | Partial | `src/agency/memory/sms/store.py`, `retrieval.py`, `lifecycle.py`, `cpr.py` |
| Agent lifecycle scaffolding | Partial | `src/agency/kernel/registry.py`, `agents/registry.py`, `agents/loop.py`, `agents/executor.py` |
| Model routing (LLM harness) | Partial | `src/agency/llm/`; includes the Inception Labs OpenAI-compatible `mercury-3` provider and echo for offline tests |
| Butler gateway + routing | Partial | `src/agency/butler/`; owner-scoped threads; the owner pilot adds a durable local owner path; `/v1` business routes remain closed |
| Sandbox / process isolation | Partial | `src/agency/security/sandbox/`; isolation guarantees unaudited |
| QA / adversarial critics | Partial | `src/agency/agents/verifier.py`, `security/red|blue|purple/`; blocking decision contract planned |
| Research agent | Exists, partial | `src/agency/agents/research/agent.py`; offline tests exercise routing, not live-provider quality |
| General / demo agents, planner | Exists, partial | `src/agency/agents/` |
| Bridges (claude, codex, hermes, openclaw) | Partial adapters | `src/agency/bridges/`; optional integrations, never dependencies |
| Lattice + governance + reputation | Partial (single-process prototype) | `src/agency/lattice/`; process-local state, best-effort mirroring, `user` override; not peer consensus |
| API server + CLI | Partial | `src/agency/api/server.py` (`/v1` closed except health), `src/agency/cli/main.py`; prototype `/api` and `/api/pilot` surfaces are separate |
| CI / CD workflows | Exists, outcome unverified at this SHA | `.github/workflows/ci.yml`, `cd.yml`; no exact-SHA run is certified in this docs pass |
| Coding agent / Forge | Partial | `agents/coding.py` operator over Forge with protected-path guardrail; owner factory coding exposed over the pilot API (`/api/pilot/coding-tasks`) and console coding page, gated on an opt-in fixture repo; merge/release remain planned |
| Peer protocol slice A | Implemented (contracts only) | `src/agency/peers/`: identities, canonical serialization, envelope binding, membership/epoch types, redaction, durable hash-chained log; no transport, executor, actuator or consensus |
| Prototype factories (dark/ghost) | Prototypes only | Standalone; not an integrated Forge |

### New partial foundations and remaining planned systems

| Item | State | Planned next step (per reconciliation spec) |
|------|-------|----------------------------------------------|
| Durable thread / workspace / task store | Implemented (local owner) | Public authenticated API awaits P2; project model pending |
| Owner pilot API / UI | Implemented (local) | Live provider, real-browser and restart/crash qualification; decide whether to expose coding over HTTP |
| Mission Control | Implemented (local) | Add GUI tests; wire live telemetry or honest empty states; resolve pilot-mode bearer wiring |
| Agent Factory (versioned) | Partial v1 | Versioned blueprints, approval, L0/UNKNOWN activation; no evaluation, autonomous creation or restart rehydration |
| Prompt library (factory) | Partial v1 | Versioned prompt records, deterministic composition, bundled segments + REAP packs; runtime consumption and Forge tool-availability params implemented |
| Skill registry (Agency-native) | Partial | Versioned metadata, lifecycle evidence, explicit permissions; skill runtime binding pending |
| Workflow registry + deterministic executor | Partial product, explicit local recovery implemented | Bounded DAG/pinned allowlisted execution plus file-backed `run_recoverable/resume/reconcile`; Agent/Forge integration and distributed task recovery remain missing |
| Forge (integrated) + native coding agent | Partial | `forge/inspection.py` bounded read-only hash/syntax reports; coding operator and protected paths exist; real coding, tests, security review, packaging and release remain planned |
| Durable governance votes / approvals | Planned | D-01–D-05 policy approved; peer application implementation missing |
| Peer authority (P2) | Missing | `SPEC_KNOWN_PEER_PROTOCOL.md` §18 and `SPEC_PEER_LATTICE_GOVERNANCE.md` define the path; no implementation |
| Workspace/project UI, branching | Planned | After thread isolation and P2 authority |
| Production deployment, observability, audit | Planned | Docker/K8s, OpenTelemetry, security audit — later phases |

`Athena-global-skills` (`https://github.com/athena254/Athena-global-skills`) is a
separate pattern source for future skills/Forge work, not a dependency and not
auto-imported.

## Build sequence (from the brief and reconciliation spec)

1. **Architecture reconciliation** — the current ledger is `CURRENT_STATE.md`;
   claim boundaries are in `PRODUCTION_READINESS_PLAN.md`.
2. **Thread context foundation** — owner-scoped threads/messages and the durable
   owner task workspace are implemented locally; authenticated public API access
   awaits P2.
3. **Agent Factory** — v1 governed identity creation exists; a bounded literal
   coding fixture exists at service level. Add evaluation, safe permission and
   restart reconciliation before automatic deployment.
4. **Forge** — read-only inspection, a coding operator and protected-path
   guardrail exist. Build isolated testing, review, coding agent, artifact store
   and authorized release in separately verified slices.
5. **Security and governance hardening** — owner decisions D-01–D-05 are approved;
   capability permissions, sandbox audit and the P2 approval/grant path remain.
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
- The October 18 status/resume files and their passing totals are historical; the
  current gates and claim boundaries are in `CURRENT_STATE.md`.

## Full system specification

For the target system (architecture, agents, addons, memory, bridges, data models,
deployment), see `docs/SOURCE_CONSOLIDATED_BRIEF.md` — it is the canonical reference
for direction, not a claim about what is built.
