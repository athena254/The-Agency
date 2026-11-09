# The Agency — current implementation state

Updated November 9, 2026. **Pre-production prototype; not daily-driver-qualified or a production-ready decentralized runtime.** The target remains `SOURCE_CONSOLIDATED_BRIEF.md`. The approved policy inputs are recorded in `OWNER_DECISIONS_D01_D05.md`; the gated engineering plan is `PRODUCTION_READINESS_PLAN.md`. Older status/resume/audit files (including `STATUS_AND_RESUME_20261018.md` and `research-evidence/status-audit-20261018.json`) are historical and are not current acceptance.

## Repository and verification

Canonical checkout: `C:/Users/PC/The-Agency`; local `main` at `cafd683` (after `53909d0`). The committed tree at that revision is the reference for this document. This documentation change touches only `docs/`; uncommitted `gui/` edits observed in the working tree during the pass are not part of it and are not described here. No push, release, deployment, beta enablement, daemon launch or real-resource authority is implied.

Gates at this revision:

- Ruff `check` and `format --check` pass (393 files formatted).
- Strict mypy passes on 199 source files.
- The Python suite is hermetic: `tests/conftest.py` pins Agency/Telegram settings to test values and isolates every runtime database path, so the suite passes from a clean clone with no `.env` and an empty `data/` directory. The recorded result is **3453 passed, 3 skipped, 4 errors**. The 4 errors are a Windows-only harness limit: two parameterized tests use multi-thousand-character test IDs (`"x" * 65537` in `test_factory_coding_models.py` and `"雪" * 8000` in `test_factory_coding_publication.py`), and the pytest harness cannot set its per-test environment variable once the ID exceeds the Windows environment-block limit. They pass on Linux. This is a test-ID/harness limit, not a product failure.
- Node UI tests: **79 passed** (`node --test tests/*.test.mjs`), covering the pilot workspace UI, the legacy prototype UI and the calculator-verification UI against mocked HTTP/DOM leaves.

No live provider call, real browser session, deployment or OS-sandbox qualification is part of these gates. Historical passing totals (for example 1,297, 1,664, 1,870, 2,450 or 2,718 cases) are not current acceptance.

## Implemented foundations (bounded, not whole product completion)

### Owner pilot service, persistent task workspace and local UI

- `src/agency/admission/` implements a durable single-owner gateway: `OwnerPilotService` with `OwnerPilotStore`, `OwnerBudgetAdapter`, `OwnerAuthenticator`, `OwnerToolPolicy`, `SpendingLedger`, calculator receipt verification and the coding ledger. This is local owner authority, not peer consensus.
- Durable owner workspace: owner-scoped SQLite threads, messages and tasks in `pilot.db`, alongside `quota.db`, `audit.db` and an optional `spend.db`. Replays return the stored task without redispatch; cancellation is task-scoped; interrupted ADMITTED/RUNNING work is marked UNCERTAIN at startup and is never resumed automatically; terminal COMPLETED results are immutable. The store is a library/service, not an automatically recovering task runtime.
- Bounded tools: exactly `calculator`, `utc_time` and `web_search`, read-only, with argument and result limits. No shell, files, memory, fetch, bridges, governance or agent activation.
- Quotas: 5 requests/hour and 20/day, 40 model calls/hour and 160/day, 8 model attempts and 4 tool attempts per request; 120-second task deadline and at most 45-second provider timeout. These are attempt caps, not invoice amounts.
- Calculator verification: exact `[agency:calculator.v1]` marker messages are evaluated by an independent bounded integer checker that emits a task/body-bound receipt. It is a checker for one fixture, not general truth or tool-use verification.
- Optional spending ledger: exact-decimal USD reservations and settlements, separate from attempt quotas, disabled unless the launcher configuration supplies `spending`. It is an estimate, never a provider invoice.
- HTTP surface (`src/agency/prototype/pilot.py`): `GET /api/pilot/capabilities`, `GET /api/pilot/spending`, `GET|POST /api/pilot/threads`, `GET /api/pilot/threads/{id}/messages`, `GET|POST /api/pilot/tasks`, `POST /api/pilot/calculator-tasks`, `GET /api/pilot/tasks/{id}`, `GET /api/pilot/tasks/{id}/verification` and `POST /api/pilot/tasks/{id}/cancel`. Every route requires a loopback peer and Host, an exact owner bearer and (for writes) CSRF; bodies are bounded to 32 KiB and errors use a fixed public catalog. Routes are registered in all modes but return 503 `disabled` when the app is not pilot-configured.
- Local UIs: the dependency-free pilot workspace at `GET /pilot` (`static/pilot.html|pilot.js|pilot.css`) and the React Mission Control console at `GET /mission-control` (below). The owner bearer is held in browser memory only.
- Supported launcher: `python -m agency.prototype.pilot_launcher` creates `owner.token` in a protected data directory, verifies ACLs, binds loopback only, disables proxy-header trust, and never falls back to environment provider settings or echo. `--check` is read-only configuration validation, not provider verification.
- Coding factory core (service-level, **not HTTP-exposed**): `admission/coding_store.py`, `factory/coding_models.py`, `factory/coding_verification.py` and `agents/factory_coding.py` admit one fixed `literal-assignment-v1` job (`m.py`, baseline `x = 1`, one bounded edit, one model call, 120-second job timeout), run the coding agent in an isolated git worktree, check the literal artifact against a pinned checker/criteria, and store a typed receipt. `OwnerPilotService.submit_coding`/`get_coding_receipt` exist, but no `/api/pilot/*` route exposes them. `release_authorized` and `tests_run` are fixed `False`; no merge or release is authorized.
- Telegram owner authentication exists (`OwnerAuthenticator.authenticate_telegram`) but is not wired to any ingress; the pilot is local HTTP only.

Source: `src/agency/admission/`, `src/agency/prototype/{app,pilot,pilot_launcher,owner_guard}.py`, `src/agency/factory/coding_*.py`, `src/agency/agents/factory_coding.py`; tests: `test_owner_*.py`, `test_calculator_verification_*.py`, `test_factory_coding_*.py`, `test_pilot_launcher.py`, `pilot_ui.test.mjs`.

### Mission Control console

- `gui/` is a React 18 + TypeScript + Vite + Tailwind single-page app, built to `src/agency/prototype/static/mission-control/` and served by the prototype app at `/mission-control` (and `/mission-control/{path}`). The built assets are committed, so a clone can serve the console without a Node toolchain.
- Pages: Dashboard, IDE, Agents, Tasks, Memories, Evidence, Skills, Lattice, Forge, Settings. The IDE is the isolated model-only `/api/chat` path; Tasks reads `/api/pilot/tasks` and cancels active tasks; Settings holds the owner bearer in memory.
- The console's API client calls `/api/health`, `/api/agents`, `/api/session` and `/api/chat` without the owner bearer, and uses a separate `pilotRequest` helper that attaches the in-memory bearer plus CSRF for `/api/pilot/*`. The bearer is never written to disk, storage, cookies, URLs or logs.
- P2-gated surfaces render explicit locked states rather than fake controls: Memories, Evidence, Skills, Lattice and Forge show a `LockedPanel` naming the gate (`P1 identity + audit`, `P2 peer authority`, `P3 isolation`, `P4 capability lifecycle`). The Lattice page states that the current Lattice is a single-process SQLite prototype, not consensus.
- Honest labels: the IDE states it is model-only with no tools, memory or Lattice mirroring; the assessment panel is labelled "honest status, not verification"; Settings states that business API routes are closed until peer authority exists and that D-01–D-05 are approved.
- Boundaries: `gui/` has no test suite; the committed bundle is the built artifact of the checked-in source. Dashboard task/memory/evidence counts are placeholder zeros and the Activity feed contains static entries, so they are not live telemetry. In pilot-configured mode the console's unauthenticated `/api/health` and `/api/agents` calls are rejected because those routes require owner authentication; the console is fully usable only against the non-pilot prototype surface, or after its bearer wiring is extended.

Source: `gui/`, `src/agency/prototype/static/mission-control/`, `src/agency/prototype/app.py`.

### Human gateway, conversation and UI

- Butler routes human input and presents results. It must not be promoted to an autonomous agent, validator or unilateral authority.
- Owner-scoped SQLite workspaces/threads/messages, insertion-order history, persisted active thread and summary metadata; deterministic `/new`, `/threads`, `/switch`, `/current` commands; bounded summary/recent-window/thread-scoped recall.
- Private nonbeta Telegram thread commands are wired and tested through the real handler/Butler path, including persisted active-thread continuation, bot suffix normalization, deterministic unknown-command replies, private/group separation and two-user isolation. No public HTTP thread feature is implied.
- Telegram deterministic status/agent/help/name surfaces and persistent per-user naming exist. Stable numeric sender scope and separate group scope are intentional boundaries.
- The legacy static prototype UI (`static/index.html`, `app.js`) remains a model-only isolated chat: no tools, memory or Lattice mirroring. It is distinct from the owner pilot workspace and Mission Control.
- Main API: `/v1/*` business routes are closed with HTTP 503 except `GET /v1/health`, which reports component readiness. Listed routes in source are not all enabled features. In pilot-configured mode the prototype app also disables `POST /api/chat` (503) so the unmetered legacy chat path cannot be used as an alternate ingress.

Source: `src/agency/butler/`, `telegram/`, `prototype/`, `api/authority.py`, `api/server.py`; tests: `test_butler*`, `test_telegram*`, `test_beta*`, `test_prototype_app.py`, `prototype_ui.test.mjs`.

### Agents, models, tools and memory

- Actual general/research/demo agent implementations, planning/execution/verification scaffolding, registries and task routing exist. A seeded role/name is not a mature specialized domain agent.
- LLM adapters/router support multiple backends and explicit echo for offline testing. The Inception Labs OpenAI-compatible provider (`mercury-3`) is supported alongside the existing providers; a configured provider/model is not proof of a successful live response or guaranteed free pricing. The owner dispatch pins `trust_env=False`, so proxied deployments must set an explicit proxy (`LLMConfig.proxy` / `AGENCY_LLM_PROXY`, or the launcher config `proxy`); without it, provider calls bypass the deployment proxy and fail.
- Tool registry/driver and search/fetch/memory/sandbox adapters exist. The owner pilot registers only the three bounded read-only tools above; beta memory tools remain disabled.
- Scoped persistent memory, FTS retrieval, tier lifecycle and secrets helpers exist. Full resource-level permission/tenant audit remains incomplete; do not advertise hierarchical isolation as fully audited.
- Coding agent operator over Forge: `src/agency/agents/coding.py` runs bounded coding tasks in an isolated git worktree with inspection, security and optional test gates, and refuses protected paths (secrets, infra, migrations, env files, CI configuration and `docs/SPEC*.md`) before creating a worktree. A `PASS` report is evidence only; merge/release approval stays with a human. The API server bootstraps a coding-agent identity into its kernel/runtime registries at startup.
- Context/report repairs and remaining gaps: A1 propagates quoted untrusted recalled context through routed execution while preserving isolated-chat exclusion. A2 exposes immutable, task/body-bound execution/check reports and no longer presents unperformed checks as passed; those reports are not independent factual proof or authority. Classic planner subtasks still reuse whole-task prompts instead of actual specialist/dependency dispatch. Independent checker/approval/publication semantics remain open.
- Legacy memory-tool warning: the nonbeta memory query adapter accepts caller/model-selected or unscoped resource IDs; private thread-context isolation does not make that tool tenant-safe. Do not expose the legacy runtime to multiple users until resource authorization and log redaction are enforced (R-04).
- Claude/Codex/Hermes/OpenClaw bridges are optional adapters, not required architectural dependencies or verified production worker deployments.

Source: `src/agency/agents/`, `orchestrator.py`, `llm/`, `tools/`, `memory/`, `bridges/`, `forge/`; contract and integration tests under `tests/`.

### Durable budgets, audit and metering

- Persistent beta request admission/capabilities, per-user ceilings, replay handling and polling cursor/failure bookkeeping exist. Direct update dictionaries/webhooks do not become trusted beta transport merely by supplying user fields.
- Strict search-only policy and acknowledged beta audit intent/outcome primitives exist, with failure/uncertainty gates and cross-process admission tests.
- Request-bound `MeteredBinding`/`MeteredModel` is integrated into the strict internal ToolDriver path, including loop iterations/forced final. `28c9d09` rejects every non-concrete model, including subclasses, before dispatch. Unknown cancellation/exception/empty responses retain durable blocking uncertainty across audit reopen; timeout retains its pending intent. No automatic pending-clearance is provided.
- The owner pilot adds a parallel durable owner path: `OwnerMeteredBinding`/`OwnerExecutionGrant`, durable attempt counters, task-bound publication, cancellation and conservative UNCERTAIN transitions. It reuses the same metered model, ToolDriver, Registry and audit primitives; it is not a second unmetered assistant.
- This does **not** complete Telegram -> Butler -> all provider dispatches -> durable task outcome. **Ordinary beta chat, research, model calls and tools remain disabled.** No free-only or audit-completeness guarantee extends to unbound legacy paths.
- A3.1 requires exact ordinary gateway audit acknowledgement before work/success/conversation persistence, propagates blocking failures and sanitizes repaired consumer boundaries. It does not roll back earlier execution effects or provide cross-store atomicity, a task journal, grants or complete A3 admission/provider/attempt accounting.

Source: `telegram/budget_store.py`, `admission/{budget,store,service,policy}.py`, `kernel/audit.py`, `llm/metered.py`, `tools/{registry,driver}.py`; tests: `test_beta_budget_store.py`, `test_beta_tool_boundary.py`, `test_audit_lifecycle.py`, `test_model_metering.py`, `test_tools_driver.py`, `test_owner_*.py`.

### Workflows, Factory, skills and Forge

- Versioned skill/workflow metadata, validation/publication contracts and deterministic allowlisted workflow execution exist.
- Local recovery APIs `run_recoverable`, `resume`, `reconcile`, `inspect_recoverable` use file-backed SQLite, an OS-owned lock, digest/version/actor binding, committed step state and explicit unknown status. These are library APIs, not automatic recovery of arbitrary agent tasks or exactly-once side effects.
- AgentFactory v1 stores versioned blueprints, approval provenance, L0/UNKNOWN activation and revocation. No autonomous capability development/evaluation/deployment or restart rehydration is claimed.
- The factory prompt library stores versioned prompt records, composes a system prompt deterministically at activation, and ships shared segments plus bundled REAP prompt packs (MIT, pinned upstream commit, safety-pairing enforced). Runtime surfaces consume the issued prompt from identity metadata (malformed prompts fail closed), and the optional Forge availability provider fills the `reap-tool-availability` slots from the host tool catalog. No LLM generation is involved.
- Forge v1 inventories bounded paths and stores file hashes/Python syntax results. It does not run tests, write code, conduct a security review, package artifacts or authorize merge/release. The owner factory coding core above is a separate bounded fixture and does not change that boundary.
- Unified capability contract is **proposed only** (`SPEC_CAPABILITY_CONTRACT_V1.md`). Skill optimization is research only (`RESEARCH_SKILL_OPT.md`). Neither is a deployed feature.

Source: `src/agency/workflows/`, `skills/registry.py`, `factory/`, `agents/prompts.py`, `forge/`; tests under `tests/`.

### Lattice, peer primitives and consensus engine lab

- SQLite/in-memory Lattice coordination, reputation and prototype in-process proposal/voting code exist. Proposal authority is not durably replicated independent peer governance; special human-override and central mutation paths still need replacement/gating.
- Reviewed process-local resource leases cover the orchestrator and API lifespan together, with Butler borrowing the shared resource. These are lifetime/correctness boundaries, not authorization capabilities or decentralized peer authority.
- Signed peer-envelope primitives exist. They are not authenticated transport, membership, durable replay protection, final grants or actuator enforcement.
- A real four-process CometBFT loopback synthetic key/value lab has stored evidence for progress with one stopped peer, restart catch-up, quorum-loss stop, fresh commit after restoration and child cleanup. Those are historical verified engine runs, not a new live run in this pass, Byzantine attacks, network partitions or Agency task authorization.
- Missing core milestone: human -> Butler -> independently authorized peers -> harmless task -> durable result, with affected-consumer input, grants verified at actuators and replaceable coordination. The migration path is specified in `SPEC_KNOWN_PEER_PROTOCOL.md` §18 and `SPEC_PEER_LATTICE_GOVERNANCE.md`; no source implementation is authorized by those specs.

Source: `src/agency/lattice/`, `peer_envelope.py`, `lab/comet_smoke.py`; `COMET_LAB_VERIFIED_STATUS.md`, `SPEC_SINGLE_HOST_LATTICE_MILESTONE.md`, `SPEC_KNOWN_PEER_PROTOCOL.md`, `SPEC_PEER_LATTICE_GOVERNANCE.md`.

## Approved owner decisions (policy inputs, not implementation)

`OWNER_DECISIONS_D01_D05.md` records the owner-approved policy: 4 validator peers, tolerance of 1 Byzantine peer, 3/4 application quorum, CometBFT 0.38.26 version-pinned; genesis membership of those validators with signed membership proposals and epoch bumps; affected-set declaration plus signed attributable consumer input and explicit human attestation for override; signed task-scoped grants with nonce and expiry, finality proof at actuators and epoch-based revocation; local-only fixture/output resources, bounded numeric ceilings, the Inception `mercury-3` runtime route via the configured OpenAI-compatible endpoint, no confidential data, no paid fallback and 30-day evidence retention.

These decisions unblock the P1/P2 contracts and the bounded-autonomy acceptance scenarios. They do not by themselves enable beta, peer actuation, coding release or production rollout, and they are not evidence that any of those are implemented.

## What remains before production

1. Exact-revision CI, clean-clone/container baseline, secret/history exposure audit and supported launcher/container consistency. The hermetic suite is in place; exact-SHA CI and history audit are not certified here.
2. End-to-end trusted identity, all-provider budgets and fail-closed durable task audit before enabling any beta model/tool path. The owner pilot is a bounded local slice, not the full Telegram -> Butler -> provider -> durable outcome chain.
3. Approved peer application state machine, membership/fault policy, attributable affected consumers/human consent, independently verified execution grants and distributed failure/replay tests. The policy is approved; the implementation is missing.
4. Real target-host isolation, connected-address SSRF protection, per-resource authorization, protected persistence, migration/backup/restore and privacy/retention controls.
5. Factory lifecycle/restart/evaluation and real permitted skill/workflow runtime binding; Forge development/review/release only in separately authorized slices. The owner factory coding fixture does not authorize merge or release.
6. Deployable least-privilege artifacts, correct entrypoints/health checks, one-poller supervision, monitoring, measured limits and exercised rollback.
7. Independent adversarial review and scoped canary signoff. No release while a required authority/privacy/durable-audit gate is open.

See `PRODUCTION_READINESS_PLAN.md` for dependencies, observable acceptance gates, owner decisions and the restricted-pilot versus full-production distinction. There is no evidence-based completion percentage or release date.

## Next development checkpoint

A1, A2 and A3.1 are implemented. The owner pilot (M1/M2/M3), persistent task workspace, local pilot UI and Mission Control console are implemented as bounded local surfaces. The owner factory coding core is implemented at service level but not exposed over HTTP. No complete bounded-autonomy acceptance scenario is signed off, and no live provider, real browser or deployment qualification is recorded.

The next work is to freeze the P1/P2 contracts against the approved D-01–D-05 decisions, then implement and independently review the peer application path. A4 peer authority, A5 task/recovery/outbox, A6 useful independent-checker task and A7 fault/adversarial/real-model qualification remain open.

## Verification and known limitations

Fresh checks at `36d3673`: Ruff lint/format passed; strict mypy passed on 199 source files; the hermetic Python suite recorded 3453 passed, 3 skipped and 4 Windows-only harness errors; 79 Node UI tests passed. The 4 errors are an environment-variable-length limit on oversized test IDs and pass on Linux; they are not product failures. A live owner-pilot calculator task completed end-to-end on the Simular box via Inception `mercury-3` through the deployment proxy (`model.generate` ok, `tool.call` ok, `owner.gateway_result` completed, calculator receipt `passed`), which qualifies the provider route for that bounded slice. No fresh real-browser, deployment or host-independent Byzantine result exists. Exact-SHA CI, supported launch, live Telegram behavior, deployment, full isolation/privacy and host-independent Byzantine resilience are not certified. See `KNOWN_ISSUES.md` for retained follow-ups and `PRODUCTION_READINESS_PLAN.md` for claim boundaries.
