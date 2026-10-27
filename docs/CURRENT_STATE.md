# The Agency — current implementation state

Updated October 18, 2026. **Pre-production prototype; not daily-driver-qualified or a production-ready decentralized runtime.** Start with `STATUS_AND_RESUME_20261018.md` and GitHub completion tracker #11. This document supersedes older README/HEALTH/ROADMAP/WIP labels where they disagree. The target remains `SOURCE_CONSOLIDATED_BRIEF.md`. Fresh qualification is recorded in `research-evidence/status-audit-20261018.json`: lint/format/mypy and 16 UI tests pass, but Python qualification is BLOCKED by an admission-worker bootstrap deadline failure and an earlier incomplete full run. Historical passing totals are not current acceptance.

## Repository and preservation

Canonical checkout: `C:/Users/alphi/theagency`, clean local `main` at `713a4e3`; it has not been switched. Remote main was `3417c79` at audit. Latest completed development handoff is `6b1d4c4` (A1/A2/A3.1 plus Lattice repairs); the October 18 documentation checkpoint is on `docs/agency-status-20261018` in a separate worktree. The 15 local branches, including this new checkpoint, have zero unrepresented non-equivalent patches by ancestry/patch-equivalence accounting. Five original worktrees were clean and retained, with a sixth added for isolated verification/status work. See `STATUS_AND_RESUME_20261018.md` and its evidence manifest. No main merge or deployment is implied.

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
- **Context/report repairs and remaining gaps:** A1 now propagates quoted untrusted recalled context through routed execution while preserving isolated-chat exclusion. A2 exposes immutable, task/body-bound execution/check reports and no longer presents unperformed checks as passed; those reports are not independent factual proof or authority. Classic planner subtasks still reuse whole-task prompts instead of actual specialist/dependency dispatch. Independent checker/approval/publication semantics remain open. See the A1/A2 handoffs and `AUTONOMY_READINESS_20261016.md`; older R-01/R-03 findings describe the prior baseline.
- **Legacy memory-tool warning:** the nonbeta memory query adapter accepts caller/model-selected or unscoped resource IDs; private thread-context isolation does not make that tool tenant-safe. Beta memory tools remain disabled. Do not expose the legacy runtime to multiple users until resource authorization and log redaction are enforced (R-04).
- Claude/Codex/Hermes/OpenClaw bridges are optional adapters, not required architectural dependencies or verified production worker deployments.

Source: `src/agency/agents/`, `orchestrator.py`, `llm/`, `tools/`, `memory/`, `bridges/`; contract and integration tests under `tests/`.

### Durable budgets, audit and metering

- Persistent beta request admission/capabilities, per-user ceilings, replay handling and polling cursor/failure bookkeeping exist. Direct update dictionaries/webhooks do not become trusted beta transport merely by supplying user fields.
- Strict search-only policy and acknowledged beta audit intent/outcome primitives exist, with failure/uncertainty gates and cross-process admission tests.
- Request-bound `MeteredBinding`/`MeteredModel` is integrated into the strict internal ToolDriver path, including loop iterations/forced final. A late review exposed an arbitrary/scripted model-object bypass; `28c9d09` now rejects every non-concrete model, including subclasses, before dispatch. Existing failure/policy tests use real request metering with fake provider responses below the boundary. Construction and pre-dispatch identity/provider binding are validated. Unknown cancellation/exception/empty responses retain durable blocking uncertainty across audit reopen; timeout retains its pending intent. No automatic pending-clearance is provided.
- This does **not** complete Telegram -> Butler -> all provider dispatches -> durable task outcome. **Ordinary beta chat, research, model calls and tools remain disabled.** No free-only or audit-completeness guarantee extends to unbound legacy paths.
- A3.1 now requires exact ordinary gateway audit acknowledgement before work/success/conversation persistence, propagates blocking failures and sanitizes repaired consumer boundaries. It does not roll back earlier execution effects or provide cross-store atomicity, a task journal, grants or complete A3 admission/provider/attempt accounting. See `QUALIFICATION_AUTONOMY_ADMISSION_A3_1_20261016.md`.

Source: `telegram/budget_store.py`, `kernel/audit.py`, `llm/metered.py`, `tools/{registry,driver}.py`; tests: `test_beta_budget_store.py`, `test_beta_tool_boundary.py`, `test_audit_lifecycle.py`, `test_model_metering.py`, `test_tools_driver.py`.

### Workflows, Factory, skills and Forge

- Versioned skill/workflow metadata, validation/publication contracts and deterministic allowlisted workflow execution exist.
- Local recovery APIs `run_recoverable`, `resume`, `reconcile`, `inspect_recoverable` use file-backed SQLite, an OS-owned lock, digest/version/actor binding, committed step state and explicit unknown status. Real child kill/restart/locking and binding regressions are included; completed records no longer bypass actor/input checks. These are library APIs, not automatic recovery of arbitrary agent tasks or exactly-once side effects.
- AgentFactory v1 stores versioned blueprints, approval provenance, L0/UNKNOWN activation and revocation. No autonomous capability development/evaluation/deployment or restart rehydration is claimed.
- The factory prompt library stores versioned prompt records, composes a system prompt deterministically at activation, and ships shared segments plus 26 REAP packs (MIT, pinned upstream commit, safety-pairing enforced). Runtime surfaces consume the issued prompt from identity metadata (malformed prompts fail closed), and the optional Forge availability provider fills the `reap-tool-availability` slots from the host tool catalog. No LLM generation is involved.
- Forge v1 inventories bounded paths and stores file hashes/Python syntax results. It does not run tests, write code, conduct a security review, package artifacts or authorize merge/release. Dark/ghost factories are separate prototypes.
- Unified capability contract is **proposed only** (`SPEC_CAPABILITY_CONTRACT_V1.md`). Skill optimization is research only (`RESEARCH_SKILL_OPT.md`). Neither is a deployed feature.

Source: `src/agency/workflows/`, `skills/registry.py`, `factory/service.py`, `factory/prompts.py`, `factory/compose.py`, `factory/prompts_builtin/`, `agents/prompts.py`, `forge/prompt_params.py`, `forge/inspection.py`; tests: `test_workflow_registry.py`, `test_local_recovery_v1.py`, `test_skill_registry.py`, `test_agent_factory.py`, `test_prompt_registry.py`, `test_prompt_library_builtin.py`, `test_factory_prompt_refs.py`, `test_factory_prompt_compose.py`, `test_runtime_prompt_consumption.py`, `test_forge_prompt_params.py`, `test_forge_inspection.py`.

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

## Next development checkpoint — remaining bounded autonomy

`SPEC_BOUNDED_AUTONOMY_V1.md` defines the proposed complete permitted task path and 24 acceptance scenarios. A1, A2 and A3.1 have since been implemented, but no complete autonomy acceptance scenario is signed off. First diagnose the fresh admission bootstrap failure; next freeze the remaining A3 provider-dispatch binding/admission/shared-budget contract for review and implementation approval. A4 peer authority, A5 task/recovery/outbox, A6 useful independent-checker task and A7 fault/adversarial/real-model qualification remain open. Owner policy decisions D-01–D-05 remain explicit.

The October 18 status checkpoint is based on `6b1d4c4`, changes no runtime source/tests, enables no beta/model/tool path and is not deployment or completion of autonomy. Use `STATUS_AND_RESUME_20261018.md` and GitHub #11 to resume; retain historical research/failed and passing qualification evidence.

## Verification and known limitations

Fresh October 18 checks at `6b1d4c4`: Ruff lint/format passed, strict mypy passed on 151 source files after a separately retained initial timeout, 16 Node UI tests passed, and two cold imports passed. The initial full Python run was incomplete after an outer 420-second timeout with a failure marker; corrected fail-fast diagnostics collected 1,870 cases and stopped after 14 passed/1 failed on an admission-worker bootstrap deadline. No fresh passing full-suite total exists. Exact evidence is `research-evidence/status-audit-20261018.json`. Historical October 16 A3.1 qualification records 1,870 Python/16 Node passes at identical source/test trees; the older Lattice 1,664-pass and baseline 1,297-pass results remain historical. Launcher pins still identify the older baseline. Exact-SHA CI, supported launch, live provider/Telegram behavior, deployment, full isolation/privacy and host-independent Byzantine resilience are not certified. See `STATUS_AND_RESUME_20261018.md` for all claim boundaries and `KNOWN_ISSUES.md` for retained follow-ups.
