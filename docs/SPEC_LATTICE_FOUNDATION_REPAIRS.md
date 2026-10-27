# Lattice foundation repairs — bounded implementation contract

Base: `713a4e311b1e071fbd6c5cf103b995d17aa7e5a2`. Status: repair contract; not release approval.

## Authorization and invariants

The owner requested parallel subagents and fixes after clarifying Human ↔ Butler ↔ full Agency backend. This checkpoint authorizes bounded maintenance of existing behavior, not new governance policy, provider access, beta enablement, public routes, deployment or consequential tools. Canonical main and private runtime data remain untouched. Separate branches/worktrees, explicit file ownership, regression-first development, independent integration review and exact-revision verification are required.

The Lattice is the hive-memory, messaging, coordination and orchestration layer connecting independently identified agents. Butler is the human gateway: no vote, validator key, independent goals or unilateral task authorization. A local scheduler is replaceable, not an authority above the Lattice. Shared knowledge is permission-scoped, not universal access or a shared authoritative SQLite database. All agents participate within their roles; this does not require every agent to vote on every action.

## Evidence and issue inventory

Four read-only investigations were checked against source at the base revision. No new runtime reproduction was performed by those investigations. Existing green test totals are historical, not proof of these repairs.

- W-01: orchestrator stop omits owned memory/evidence/audit stores; Butler stop omits its owned audit; partial startup and shared Lattice ownership require explicit cleanup semantics.
- W-02: telegram server registers deprecated shutdown events; migrate to lifespan without changing admission.
- W-03: installed qualification environment has FastAPI 0.141.1, Starlette 1.7.0, httpx 0.28.1; lock is not evidence of installed dependencies. Reproduce TestClient warning before choosing a dependency/test-transport change. No blind lockfile rewrite.
- W-04: TestStatus enum imported under a collectible Test-prefixed name in test_security_red.py.
- W-05: recovery child tests discard output, ignore premature exit and swallow callback exceptions. Improve diagnostic reliability without claiming the unexplained historical flake is fixed.
- R-01: classic routed execution omits already-authorized recalled context; isolated conversation must remain memory-free.
- R-02: planner subtask assignment/dependency outputs are not actual specialist dispatch. Real delegation awaits peer assignment/grant contracts.
- R-03: tool records claim verification passed without a performed verification. Truthful status is safe maintenance; new blocking authority semantics require review.
- R-04: memory tools accept model-selected/unscoped resource access and expose query/error data. Trusted scoped access and provenance must be specified; beta memory tools remain denied. Do not equate worker agent IDs with authenticated owners.
- R-05: ordinary task/audit/evidence persistence and aging wiring remain incomplete. Owned-store shutdown is in this batch; durable task schema, acknowledgement ordering and replay policy are separate contracts.
- R-06: factory publication/authorization callbacks and ordinary workflow authorizer use truthiness. Only literal True may permit the existing operation. Authenticated peer approval/evaluation is not supplied by this repair.

## Initial independent repair slices

### A. Telegram lifespan (W-02)

Ownership: src/agency/telegram/server.py, src/agency/telegram/handler.py, new tests/test_telegram_lifespan.py.

Use an async lifespan context with finally cleanup. Preserve create_app signature and pre-body secret checks, error codes, default beta refusal and route behavior. Closing the handler attempts both adapter and profile cleanup even if one fails; preserve the primary failure rather than reporting success. Injected Butler/budget stores do not become handler-owned. Keep existing injected-handler close behavior unless existing contracts require otherwise. Construction failure after allocating an owned profile must close it. Do not start providers or Telegram polling.

Acceptance: lifespan normal/exception exit, partial construction rollback, both cleanup attempts and unchanged webhook admission tests. No app.on_event shutdown registration.

### B. Collection and child diagnostics (W-04/W-05)

Ownership: tests/test_security_red.py, tests/test_local_recovery_v1.py only.

Alias the imported enum, preserving public enum/member values and security test node IDs. For child tests capture bounded output to per-test temporary files (avoid pipe deadlock), inspect process exit while waiting for readiness, report exit code/output, re-raise callback errors, always reap children and preserve the primary failure if cleanup also fails. Never increase deadlines, add success-by-retry or suppress collection warnings. Production recovery semantics remain unchanged.

Acceptance: deterministic early-exit/stderr/raised-callback/timeout cleanup tests, existing real crash/lock/resume tests, security collect-only with collection warnings as errors and node-ID comparison.

### C. Strict callback verdicts (bounded R-06)

Ownership: src/agency/factory/service.py, src/agency/workflows/executor.py, new tests/test_callback_verdicts.py.

Existing callable signatures remain unchanged. Callback output is authorization only when `result is True`. Reject nonbool values including 1, nonempty strings/containers and objects whose bool is true. Exceptions and absent callbacks deny. Apply to both factory publication callbacks, factory authorizer and ordinary workflow authorizer; retain existing strict recoverable behavior. Denial must leave publication/blueprint state, events and runtime identities unchanged, and must not invoke operations. No new approving actor model or skill-publication policy is invented.

Acceptance: RED then GREEN parametrized tests for literal true, false, None, numbers, strings, containers, raising callbacks and custom truthiness; existing Factory/workflow/recovery tests pass.

### D. Owned service lifecycle (W-01 first slice)

Ownership: src/agency/orchestrator.py, src/agency/butler/service.py, new tests/test_service_lifecycle.py only. No concurrent writer may edit these files.

Orchestrator owns its constructed memory/evidence/audit stores. Butler owns its default audit and lazy thread store, borrows orchestrator memory and caller-injected memory/audit/thread stores. Preserve the established service start/stop of its orchestrator, but do not blindly close caller-injected stores. Stop must close owned resources, remain safe when repeated/after failed startup and attempt independent cleanup after a failure. Startup rollback catches cancellation as well as ordinary exceptions, preserves the primary exception, and cleans acquired owned resources before return. Repeated start must not reopen/leak resources. Borrowed resources remain usable after Butler stop. Do not enable periodic aging/deletion or change retention.

The process-global Lattice singleton has a separate ownership defect: closing it from one consumer may invalidate another, and caching before initialize may leave a broken instance. This initial slice must not claim W-01 fully closed; any necessary factory/backend/initialization fix requires a separate reviewed ownership contract and disjoint subsequent slice. Do not mask these defects with global warning filters or close injected shared handles indiscriminately.

Acceptance: owned-close counts, shared-memory single-owner close, caller store usability, repeated start/stop, startup failure/cancellation, cleanup-failure continuation and no skipped owned cleanup. Real temporary SQLite where feasible; no canonical databases.

## Coordinator-owned follow-on work

Reconcile normative Lattice definitions/voting/examples and current status across SPEC_LATTICE, SPEC_KNOWN_PEER_PROTOCOL and peer milestone documents; preserve immutable source transcripts. Record legacy API semantics accurately rather than pretending SQLite and in-memory governance are identical. Mark advertised but absent messaging interfaces unimplemented.

R-01 authorized context propagation and R-03 truthful verification labels may follow the lifecycle slice because they share orchestrator.py. Do not add a separate orchestrator above the Lattice, promote recalled text into authority or make heuristic verification a security grant.

P1 needs a separate immutable request binding, request-local dispatch and durable typed outcome journal contract. Preserve all beta denial branches until end-to-end reviewed evidence exists. R-04 requires trusted transport provenance and mandatory SQL scope, not a model-controlled string. These are not silently fixed by this foundation batch.

## Open policy gates for the peer task

Already chosen: Byzantine/compromised-peer tolerance; attributable input from every affected consumer; missing input blocks; rejection is not an individual veto; separate peer processes on one host are an accepted first milestone.

Still require owner decisions: fault bound and validator membership/power, admission/membership/key recovery, application approval and override rules, affected-graph derivation, independently verifiable human input, deterministic committed-time/deadline rules, transport limits and operator recovery powers. No defaults are invented here. Single-host peers do not prove independent-host security.

First future task: deterministic canonicalization of public synthetic JSON, with committed request, proposal, inputs, authorization, assignment, independently checked result and durable completion. No LLM, arbitrary files, subprocess tools or external effects. A successful engine KV lab is not this task path.

## Qualification and evidence

Use the existing qualified Python 3.11 interpreter read-only as a toolchain; force each test invocation to use the worker's src through absolute PYTHONPATH. Explicit echo provider/model, scratch-only working data and an external-network-denying harness are mandatory. Workers may not install packages, read credentials/private runtime, call providers, change git history outside their own branch, push, merge, deploy or restart daemons. Source edits from free/contributor workers contain only owner-authorized repository code and synthetic tests, not private data; zero reported price does not assert privacy/retention guarantees.

Each worker first reports a read-only file/symbol map and stops. Coordinator reviews it and explicitly resumes implementation. Then show a meaningful failing regression before the corresponding production fix, focused passing tests, Ruff lint/format and relevant mypy. Stage exact owned paths only. Preserve logs/evidence outside source.

After independent diff review, integrate scoped commits into this repair branch. Run full offline Python tests, UI tests, repo-wide Ruff lint/format, strict mypy and supported launcher preflight at the exact integrated revision. Record warnings honestly; warnings as errors are used for targeted closure, never blanket suppression. A locally passing batch is not CI, release, a live provider test or production readiness.

## References checked for the repair design

- FastAPI official Lifespan Events: lifespan is the recommended replacement for deprecated startup/shutdown event handlers.
- aiosqlite official API: awaited close completes queued work and closes the connection; it is not sufficient to drop the Python reference.
- pytest official warnings/collection documentation: imported Test-prefixed classes can produce collection warnings; fix naming rather than suppressing collection diagnostics.

No new consensus, runtime dependency or infrastructure technology is selected by this checkpoint.
