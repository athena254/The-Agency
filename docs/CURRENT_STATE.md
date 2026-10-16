# The Agency — current implementation state

Updated October 16, 2026 for the repair-branch handoff. **Pre-production prototype; not a production-ready decentralized runtime.** This document supersedes older README/HEALTH/ROADMAP/WIP status labels where they disagree. The target remains `SOURCE_CONSOLIDATED_BRIEF.md`. Start with `HANDOFF_LATTICE_REPAIRS_20261016.md`; exact latest local qualification is `research-evidence/lattice-repairs-qualification-ba9ec768.json`. Older verification records remain historical evidence, not current test totals.

## Repository and preservation

Canonical checkout: `C:/Users/alphi/theagency`, clean local `main` at `713a4e3`; it has not been switched to the repair branch. Earlier Butler, metering and recovery consolidation is part of that baseline (`7567471`, `b9882ab`, `59e9bc9` and subsequent documentation/preflight checkpoints). The latest completed repair source is `ba9ec76` on `fix/lattice-foundation-20261015`, in a separate integration worktree. Repair worker worktrees are retained and were clean at handoff inventory; their committed patches are represented in integration. The earlier claim that only one local branch/worktree remains applies to the prior consolidation, not this repair batch. See the handoff for publication and private evidence preservation; no main merge or deployment is implied.

Before consolidation, all four original local branch tips were preserved in a Git bundle and recovered into a separate bare repository for comparison. All three original worktrees have SHA-256 source snapshots, binary tracked patches and private runtime/worker inputs preserved externally; consistent SQLite backups passed integrity checking. Canonical credentials and runtime databases stay outside Git tracking. The live lattice database was **not deleted** when removed from the index. Historical Git commits can still contain old database blobs; this is not history cleansing or proof those blobs were harmless.

No push, release, deployment, beta model/tool enablement, daemon launch or real-resource authority is implied by local consolidation. An owner-side Telegram reply is not certified by Bot API credential checks.

## Implemented foundations (bounded, not whole product completion)

### Human gateway, conversation and UI

- Butler routes human input and presents results. It must not be promoted to an autonomous agent, validator or unilateral authority.
- Owner-scoped SQLite workspaces/threads/messages, insertion-order history, persisted active thread and summary metadata; deterministic `/new`, `/threads`, `/switch`, `/current` commands; bounded summary/recent-window/thread-scoped recall.
- Private nonbeta Telegram thread commands are wired and tested through the real handler/Butler path, including persisted active-thread continuation, bot suffix normalization, deterministic unknown-command replies, private/group separation and two-user isolation. No public HTTP thread feature is implied.
- Telegram deterministic status/agent/help/name surfaces and persistent per-user naming exist. Stable numeric sender scope and separate group scope are intentional boundaries.
- Local browser prototype is static HTML/CSS/JS served by FastAPI. Its model-only isolated chat does not run tools, recall/write memory or mirror private chat to Lattice. Host/peer/origin/CSRF protections do not make it a multiuser web application or replace actual loopback binding.
- API/CLI route scaffolding exists. Agency/legacy Butler HTTP business routes are currently closed with health-only access because peer authority is unavailable. Listed routes in source are not all enabled features.

Source: `src/agency/butler/`, `telegram/`, `prototype/`, `api/authority.py`, `api/server.py`; tests: `test_butler*`, `test_telegram*`, `test_beta*`, `test_prototype_app.py`, `prototype_ui.test.mjs`.

### Agents, models, tools and memory

- Actual general/research/demo agent implementations, planning/execution/verification scaffolding, registries and task routing exist. A seeded role/name is not a mature specialized domain agent.
- LLM adapters/router support multiple backends and explicit echo for offline testing. Configured provider/model is not proof of a successful live response or guaranteed free pricing.
- Tool registry/driver and search/fetch/memory/sandbox adapters exist. Calculator/UTC helpers are explicit opt-ins, not silently enabled beta tools.
- Scoped persistent memory, FTS retrieval, tier lifecycle and secrets helpers exist. Full resource-level permission/tenant audit remains incomplete; do not advertise hierarchical isolation as fully audited.
- **Additional cross-domain/runtime gaps:** only general/research tool prompts receive recalled `memory_context`; classic routed domains omit it. Classic planner subtasks reuse the whole-task prompt instead of executing assigned specialists with dependency outputs. Tool-loop records hardcode a passed-verification label, and execution status—not independent verification—controls task completion. These are open product gaps, not completed delegation or QA authority. See `LATE_REVIEW_DISPOSITION.md` (R-01–R-03).
- **Legacy memory-tool warning:** the nonbeta memory query adapter accepts caller/model-selected or unscoped resource IDs; private thread-context isolation does not make that tool tenant-safe. Beta memory tools remain disabled. Do not expose the legacy runtime to multiple users until resource authorization and log redaction are enforced (R-04).
- Claude/Codex/Hermes/OpenClaw bridges are optional adapters, not required architectural dependencies or verified production worker deployments.

Source: `src/agency/agents/`, `orchestrator.py`, `llm/`, `tools/`, `memory/`, `bridges/`; contract and integration tests under `tests/`.

### Durable budgets, audit and metering

- Persistent beta request admission/capabilities, per-user ceilings, replay handling and polling cursor/failure bookkeeping exist. Direct update dictionaries/webhooks do not become trusted beta transport merely by supplying user fields.
- Strict search-only policy and acknowledged beta audit intent/outcome primitives exist, with failure/uncertainty gates and cross-process admission tests.
- Request-bound `MeteredBinding`/`MeteredModel` is integrated into the strict internal ToolDriver path, including loop iterations/forced final. A late review exposed an arbitrary/scripted model-object bypass; `28c9d09` now rejects every non-concrete model, including subclasses, before dispatch. Existing failure/policy tests use real request metering with fake provider responses below the boundary. Construction and pre-dispatch identity/provider binding are validated. Unknown cancellation/exception/empty responses retain durable blocking uncertainty across audit reopen; timeout retains its pending intent. No automatic pending-clearance is provided.
- This does **not** complete Telegram -> Butler -> all provider dispatches -> durable task outcome. **Ordinary beta chat, research, model calls and tools remain disabled.** No free-only or audit-completeness guarantee extends to unbound legacy paths.

Source: `telegram/budget_store.py`, `kernel/audit.py`, `llm/metered.py`, `tools/{registry,driver}.py`; tests: `test_beta_budget_store.py`, `test_beta_tool_boundary.py`, `test_audit_lifecycle.py`, `test_model_metering.py`, `test_tools_driver.py`.

### Workflows, Factory, skills and Forge

- Versioned skill/workflow metadata, validation/publication contracts and deterministic allowlisted workflow execution exist.
- Local recovery APIs `run_recoverable`, `resume`, `reconcile`, `inspect_recoverable` use file-backed SQLite, an OS-owned lock, digest/version/actor binding, committed step state and explicit unknown status. Real child kill/restart/locking and binding regressions are included; completed records no longer bypass actor/input checks. These are library APIs, not automatic recovery of arbitrary agent tasks or exactly-once side effects.
- AgentFactory v1 stores versioned blueprints, approval provenance, L0/UNKNOWN activation and revocation. No autonomous capability development/evaluation/deployment or restart rehydration is claimed.
- Forge v1 inventories bounded paths and stores file hashes/Python syntax results. It does not run tests, write code, conduct a security review, package artifacts or authorize merge/release. Dark/ghost factories are separate prototypes.
- Unified capability contract is **proposed only** (`SPEC_CAPABILITY_CONTRACT_V1.md`). Skill optimization is research only (`RESEARCH_SKILL_OPT.md`). Neither is a deployed feature.

Source: `src/agency/workflows/`, `skills/registry.py`, `factory/service.py`, `forge/inspection.py`; tests: `test_workflow_registry.py`, `test_local_recovery_v1.py`, `test_skill_registry.py`, `test_agent_factory.py`, `test_forge_inspection.py`.

### Lattice, peer primitives and consensus engine lab

- SQLite/in-memory Lattice coordination, reputation and prototype in-process proposal/voting code exist. Proposal authority is not durably replicated independent peer governance; special human-override and central mutation paths still need replacement/gating.
- Reviewed process-local resource leases now cover the orchestrator and API lifespan together, with Butler borrowing the shared resource. Ready-only publication, thread-safe manager installation, managed owning-loop checks, last-owner cleanup and serialized same-instance start/stop/restart are implemented. These are lifetime/correctness boundaries, not authorization capabilities or decentralized peer authority.
- Signed peer-envelope primitives exist. They are not authenticated transport, membership, durable replay protection, final grants or actuator enforcement.
- A real four-process CometBFT loopback synthetic key/value lab has stored evidence for progress with one stopped peer, restart catch-up, quorum-loss stop, fresh commit after restoration and child cleanup. Those are historical verified engine runs, not a new live run in this pass, Byzantine attacks, network partitions or Agency task authorization.
- Missing core milestone: human -> Butler -> independently authorized peers -> harmless task -> durable result, with affected-consumer input, grants verified at actuators and replaceable coordination.

Source: `src/agency/lattice/`, `peer_envelope.py`, `lab/comet_smoke.py`; `COMET_LAB_VERIFIED_STATUS.md`, `SPEC_SINGLE_HOST_LATTICE_MILESTONE.md` and associated lab evidence/tests.

## What remains before production

1. Exact clean-clone/CI/release baseline; resolve test/resource warnings, secret/history exposure and supported launcher/container inconsistencies.
2. End-to-end trusted identity, all-provider budgets and fail-closed durable task audit before enabling any beta model/tool path.
3. Approved peer application state machine, membership/fault policy, attributable affected consumers/human consent, independently verified execution grants and distributed failure/replay tests.
4. Real target-host isolation, connected-address SSRF protection, per-resource authorization, protected persistence, migration/backup/restore and privacy/retention controls.
5. Factory lifecycle/restart/evaluation and real permitted skill/workflow runtime binding; Forge development/review/release only in separately authorized slices.
6. Deployable least-privilege artifacts, correct entrypoints/health checks, one-poller supervision, monitoring, measured limits and exercised rollback.
7. Independent adversarial review and scoped canary signoff. No release while a required authority/privacy/durable-audit gate is open.

See `PRODUCTION_READINESS_PLAN.md` for dependencies, observable acceptance gates, owner decisions and the restricted-pilot versus full-production distinction. There is no evidence-based completion percentage or release date yet.

## Next development checkpoint — proposed bounded autonomy

`SPEC_BOUNDED_AUTONOMY_V1.md` defines the first complete permitted task path, safety/recovery acceptance scenarios and unresolved authority decisions. `RESEARCH_BOUNDED_AUTONOMY_20261016.md` maps selected current-source gaps and records a synthetic diagnostic reproducing missing classic recall and unearned tool verification/risk labels. `SPEC_AUTONOMY_CONTEXT_REPAIR_A1.md` is the first bounded repair proposed for implementation approval; the later slices require separate frozen contracts.

This documentation checkpoint is on `docs/bounded-autonomy-20261016`, based on handoff `1c7e7da`, in `C:/Users/alphi/agency-worktrees/bounded-autonomy-20261016`. It changes no runtime source/tests, enables no beta/model/tool path and does not constitute deployment or completion of autonomy. The diagnostic is not a fresh full-suite, live-provider, peer-authority or independent-QA qualification. The existing gates and historical qualification below remain unchanged.

## Verification and known limitations

Latest full offline local verification at `ba9ec76`: **1,664 Python tests passed, zero failures/errors/skips, one Starlette/httpx deprecation warning**; **14 UI tests passed**; repo-wide Ruff lint/format and strict mypy on **150 source files** passed. Resource, unhandled-thread and unraisable warnings were errors; unsafe timed faulthandler diagnostics were disabled. The checkout/revision/source/test trees remained unchanged during the completed run. This handoff adds documentation/evidence only and must retain those exact source/test trees. Machine-readable results: `research-evidence/lattice-repairs-qualification-ba9ec768.json`; remaining warning/stability work: the handoff and `KNOWN_ISSUES.md`. Earlier `59e9bc9` verification and launcher pin/`--check` are historical and do not qualify the new repair revision for launch. Current CI matrix, fresh supported-launcher check, live-provider behavior, deployment, full sandbox/tenant security and host-independent Byzantine resilience remain unverified. Do not aggregate focused selections or turn the retained earlier failed/timed-out attempts into passing evidence.
