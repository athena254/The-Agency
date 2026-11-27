# Known gaps — requested-agent / capability / factory / skills

Found while implementing `RequestedAgentService` (`src/agency/factory/requested_agent.py`).
Ordered by production risk. **Avoid these patterns; all agents are free to fix them.**

**Status 2026-11-22 (full-gap sweep + build session):** 18 of 20 closed — gaps 1-9, 11-16,
19 and 20; gap 10 is CLOSED (the last open leg — a second live provider for comparison —
was qualified the same day; details below); gap 18 is partially closed (§12 grant path live
on the authed intake routes, fenced downstream at the executor, live 4-peer actuator demo
PASS; an optional fail-closed TLS-termination path with a local test-CA harness landed
`0fe1d0c`/`4810b82`/`5d328fa`; real-PKI certificates remain external). Gap 17 remains open
for an external reason (no real PAK repository to wire against); gaps 18-20 were added by
the intake workstream (2026-11-20). Gap 21 is partially closed: (d) and now (f) are closed
(commit references below); (e) stays deliberately blocked.

## 1. Capability ⇄ Factory circular import (structural) — CLOSED `5399d37`
`canonical_sha256` moved to the neutral `src/agency/core/hashing.py`; `factory.coding_models`
re-exports it, `capability/contract.py` imports from core, `capability/composition.py` takes
`AgentFactory`/`Blueprint` under `TYPE_CHECKING` only, and the lazy-import workaround in
`publish_skill` is gone. Module-level capability imports inside `agency/factory/` are safe
now; still never import factory from capability at module load time.

## 2. Self-improvement human gate — CLOSED `1ed038f`
`improve_agent` requires an explicit approver accepted by the service's
`self_improve_authorizer` for `"agent.self_improve"`; the agent being improved and the
service's own automated approver identity are rejected (no self-approval); fails closed when
unset. The human actor is recorded on the prompt publish and the factory approve audit.

## 3. Re-activation atomicity — CLOSED `1ed038f`
`AgentFactory.reversion()` performs activate(new) + revoke(old) inside one
`BEGIN IMMEDIATE` SQLite transaction (both identity updates and audit events commit
together, activate's rollback discipline preserved). A startup reconciler was intentionally
not built (YAGNI).

## 4. Distilled / gap skills have no runnable implementation — CLOSED `36bd00f`
Learned/gap skills are created with the `documentation-only` sentinel implementation
(`agency.skills.executor.DOCUMENTATION_ONLY_IMPLEMENTATION`); `SkillExecutor.run` returns
"skill is documentation-only; no executable operation" instead of "unknown operation".
Legacy persisted `learned.*` names keep the old error (pinned by test).

## 5. Publication gate is opt-in — CLOSED `1ed038f`
`publish_skill` fails closed: it raises unless an attestation store is configured AND the
store holds an attestation for the skill's `SkillCapabilityAdapter` content digest.

## 6. No external entry point to request an agent — CLOSED `0078fa3`
`POST /v1/agent-requests` (bearer-token `tasks:write` auth, creator bound server-side to the
principal) and the `agency agent request` CLI command both go through
`agents/bootstrap.request_agent_authorized` — an explicit `"agent.request"` authorizer plus a
domain allowlist, both fail-closed (env: `AGENCY_AGENT_REQUEST_ACTORS`, `..._DOMAINS`,
`..._APPROVER`; unset ⇒ all denied).

## 7. Orchestrator improvement hook is narrow — CLOSED `36bd00f`
`orchestrator._assessment_prompt_gaps` derives prompt-gap observations from real execution
evidence (failed heuristic checks, short classic-path outputs) and threads them into
`improve_agent(prompt_gaps=...)` alongside the trace-derived failed-skill-step gaps.
Classic-path refinement demonstrably runs now (still human-gated per gap 2).

## 8. Repeated improvement creates duplicate skills — CLOSED `1ed038f`
Gap/learned skills dedup by normalized-description hash (`canonical_sha256` over
whitespace-collapsed, digit-blanked descriptions) against the SkillRegistry before
registration, with a per-service lifetime cap (`MAX_LIFETIME_LEARNED_SKILLS = 32`).

## 9. Registries without WAL / shared-connection discipline — CLOSED `5399d37`
`PromptRegistry`, `AgentFactory` and `SkillRegistry` set `journal_mode=WAL` +
`synchronous=NORMAL` on their connections; one writer connection per process stands.

## 10. No live-provider qualification of this flow — CLOSED (2026-11-22, live, two providers)
Qualified live on an OpenAI-compatible provider (CentAI `api.cent-ai.dev/v1`, model
`hy4-flash:free`, configured via `AGENCY_LLM_PROVIDER=openai` + `AGENCY_LLM_BASE_URL` +
`AGENCY_LLM_API_KEY` in the untracked `.env`; key is never committed): (a) the Agency's own
adapter path (dotenv → `load_config` → `LLMAdapter.generate`) returned the exact expected
marker; (b) a real task ran through the full dispatcher pipeline — live planner (2 subtasks),
live executor (`attempts=1`), correct result (`42`), audit entries, memory write, terminal
`completed`, clean shutdown. The verifier scored the subtasks `0.667/failed` on a correct
answer — the known independent-checker strictness open item, recorded honestly as findings.
Still open: a second provider for comparison. The multi-restart and concurrency soak — the
last unqualified leg — was exercised live the same day (2026-11-22): `scripts/quality_soak.py`
(written uncommitted in the `D:/agency-wave-soak` worktree at `4e49a91` — evidence only, no
branch; Ruff check/format clean; no new unit tests, the script is evidence-producing) ran
both legs against hy4-flash:free with the file-backed shared stores (`AGENCY_TASKS_DB` /
`AGENCY_AUDIT_DB` / `AGENCY_EVIDENCE_DB` / `AGENCY_MEMORY_DB` per-leg temp dir, lease TTL 45
s). Concurrency leg: 10 independently verifiable tasks (exact-number / exact-marker mix) on
one SQLite queue drained by TWO dispatcher processes sharing the file — 10/10 terminal
`completed`, every completed output carried its exact expected token, exactly one execution
and one CAS terminal write per task across both processes (no duplicate side effects), the
deliberate terminal-CAS probe was refused 10/10 (`YIELDED_TERMINAL`), 16 subtask audit
entries / 16 evidence findings, workers exited 0. Restart leg: 6 tasks, dispatcher process
w1 killed mid-flight (Windows tree-kill via `taskkill /F /T`; the uv venv trampoline makes
`Popen.pid` the launcher, so the kill targets the tree and the lease-owning interpreter pid
is taken from the worker's own ready report) while holding 2 RUNNING leases — the
expired-only probe fired immediately after the kill requeued 0 (orphaned fresh leases and
the survivor's heartbeated leases all untouched), once the TTL lapsed a fresh
`AGENCY_TASKS_RECOVER=1` dispatcher requeued exactly the 2 orphaned claims and re-executed
them to `completed` with correct outputs; 6/6 terminal, no task lost, one execution + one
terminal write per task across all three workers, no pre-kill terminal record overwritten
(output digests unchanged), the killed process's partial audit trail intact (append-only,
1 entry before the kill → 7 after, pre-kill digests verified), terminal-CAS probe refused
6/6. Free-tier capacity was healthy in this window: 0 provider backoffs, 0 planner-regex
fallbacks, 0 executor retries across both legs — recorded as observed absence, not forced
passes; the provider-aware backoff and planner fallback remain the guards for dips. The
harness itself was fixed once during qualification (uv-trampoline pid handling; the first
dry run honestly failed with "no RUNNING claim owned by a killable worker" because the
lease owner carried the interpreter pid while `Popen` tracked the launcher). Evidence:
`D:/agency-merge-backups/soak-evidence.json` (per-leg verdicts/reasons, per-task final
status + output digest, audit/evidence counts, CAS-yield and probe counts, restart
timeline; per-worker logs and SQLite stores under `D:/agency-soak-runs/`). Both legs
`pass`; no secrets in the evidence (key-value scan before write). The
improve leg was qualified live the same day (2026-11-22): the offline `test_requested_agent_
improve leg was qualified live the same day (2026-11-22): the offline `test_requested_agent_
e2e` flow re-run with the live provider driving planner/executor — `request_agent` → live
task → `improve_agent` re-versioned `scout` 1.0.0→1.0.1 (`prompt_refined=True`, atomic
reversion, approver `human-lead` via the #2 human gate, purpose prompt 1.0.1 PUBLISHED,
distilled skill 1.0.1 DRAFT). The Forge review gate was qualified live the same day on a
second provider (Google, `5d91400`): with `GOOGLE_API_KEY` in the untracked `.env`,
`build_review_llm` now prefers the Google serious model — the documented free
`gemini-4-hydrogen` — via `load_named_provider("google")`, independent of the CentAI task
LLM (unit-tested credential separation; the un-overridden default stays Google-only). Live
`run_review(use_llm=True)` on a module+test fixture: inspection, security scan, the tests
sub-gate (`rc=0`) and the LLM review round all executed, gate `PASS score 4`, sole caveat
`not_a_human_review`. Free-tier capacity dips surface as 60s timeouts / transient 503s —
the provider-aware backoff retries them and the gate fails closed with honest caveats when
the provider stays unavailable (observed and verified live). The soak is now exercised
(both legs `pass`, evidence above); full production certification is still not claimed —
single host, single provider, no beta enablement.

**Second provider — CLOSED the same day (2026-11-22, live):** the remaining open leg — a
second provider for comparison — was qualified on Google (`ProviderKind.GOOGLE`, config
default `gemini-3.5-flash-lite`, `AGENCY_LLM_PROVIDER=google` + `AGENCY_LLM_API_KEY` /
`AGENCY_LLM_MODEL`): (a) the adapter leg round-tripped the exact expected marker through
dotenv → `load_config` → `LLMAdapter.generate` (1 attempt, no backoffs); (b) a real task ran
the full dispatcher pipeline — live planner (3 subtasks), live executor, exact correct
output, terminal `completed`, audit/evidence/memory all written, 0 provider errors. Both
CentAI legs re-ran green in the same comparison (`scripts/second_provider_comparison.py`,
commit `182faec`; hermetic tests for its arg handling/offline refusal/
secret-scan `tests/test_second_provider_comparison.py`; per-provider JSON evidence at
`D:/agency-second-provider/evidence/second-provider-evidence.json`, secret-scanned before
write; the key was never read into any artifact). The long-standing independent-checker
strictness item recorded above — the verifier scoring a correct answer `0.667/failed` — is
CLOSED (`8a9a48f`): the orchestrator had been passing the verifier's own check-category
names ("completeness"/"accuracy"/"safety") as if they were output-mention requirements, so
a correct keyword-free answer failed the fabricated completeness check; it now verifies
what was actually checked (completeness/safety honestly, accuracy recorded
`not_performed`/`no_trusted_accuracy_oracle`), and safety enforcement is pinned to survive.
The live Google task leg observed the fixed verdict honestly (`needs_review`, no fabricated
failure). Also fixed from the qualification (`2a909be`): the legacy `LLMAdapter()` env path
neither detected `GOOGLE_API_KEY`/`GEMINI_API_KEY` nor had a google default model — it fell
through to the Pollinations default and 404'd; detection + default now mirror
`config.py`. Honest limits: one qualification run per provider (the multi-restart and
concurrency soak legs ran on CentAI only; not re-run on Google), free-tier dips observed on
both (CentAI 503 retried by the provider-aware backoff; `gemini-4-hydrogen` rate-limited on
this key — the qualification uses the config-default model), and full production
certification is still not claimed: single host, no beta enablement.

## 11. GitHub-imported skills are metadata-only — CLOSED `36bd00f`
`ext-*` skills are created with the same documentation-only sentinel as gap 4; promotion and
attachment behavior unchanged.

## 12. Large-repo import bounds need per-repo narrowing — CLOSED `daffe85`
The hand-tuned `_ecc_skill_only`/`_skill_only`/`LARGE_REPOS` filters were removed;
`MarkdownImportService` now narrows oversized unfiltered walks to skill/agent docs
(deterministic shallowest-first subset) and bounds the content budget generically instead of
raising for filtered walks.

## 13. Import promotion has no distinct human approver — CLOSED `daffe85`
`GitHubSkillImportService.promote()` fails closed without an explicit approver identity and
a publish authorizer (the historical default approver `"owner"` is gone); the import script's
`--promote` requires `--approver <human identity>`.

## 14. Manifest attachment only on the API startup path — CLOSED `0078fa3`
`agents/bootstrap.bootstrap_agents()` installs manifest agents and grants manifest
capabilities in one seam; the API lifespan calls it. The CLI is a pure HTTP client, so it is
covered transitively (`agency start` → API lifespan). Embedded-orchestrator wiring was
deliberately not forced: it would break the codified empty-registry invariant
(`test_register_agent`) and is cwd-dependent — a one-line follow-up if wanted (see the note
in `tests/test_agent_request_entrypoint.py`).

## 15. Imported agent PACK prompts are not yet blueprint mappings — CLOSED `b616fb9`
`agents/bootstrap.pack_blueprint_from_prompt` maps a quarantined PACK DRAFT prompt to a DRAFT
factory blueprint (`prompt_refs=[ref]`, creator `pack-import`, provenance string carried as
evidence and logged; dedup by derived blueprint id). No auto-approval or activation — the
factory's governed approve/activate path applies.

## 16. Installed-agent registry is a flat manifest — CLOSED `b616fb9`
`agents.yaml` is the single source: `AgentManager.to_toml()/write_toml()` regenerate
`agents.toml` (drift fixed), and `validate_installed_agents()` runs before install —
duplicate manifest ids raise, unparsable versions and missing factory blueprints skip with a
reason. PLAN.json per agent was skipped (YAGNI, nothing consumes it);
`scripts/setup_autonomous_loop.sh` was not created — it is referenced nowhere outside this
file (the documented loop entry is `agency loop run|resume|status`).

## 17. PAK sync is modelled but not wired to an external PAK repository — OPEN (external blocker)
The local install/invert/merge model is complete and tested, but no real PAK
repository/manifest format is available to wire against (`docs/opensource/pak.md` and the
A2-Security-Advisors-LTD `pak`/`schema` docs were 404 at implementation time). Connect a real
PAK repository and verify the sync flow against its manifest format when one exists.

## 18. External HTTP intake — route swap live on the authed intake routes — PARTIALLY CLOSED `8604f8a`, `11854fa`, `37095b9`, `f981282`, `fada9cb`, `2566588`
Landed: the §12 actuator-side enforcement primitives — a grant-verifying HTTP dependency,
genesis registry seeding and scoped read opening (`8604f8a`, adapted to the merged router
posture in `2eb4603`, async-bridge fix `37095b9`), memory owner-scope binding, finality-gated
writes and the evidence doc (`11854fa`), plus the Wave1 lattice (ABCI codec/app/host, A4/A5
wiring) those surfaces ride on. Additive salvage on the consolidation line: `forge ship`
gated local fast-forward with audited approval bypass and the offline core-smoke CI/CD job
(`f981282`, incl. `tests/test_consensus_grant_wire.py`). Route-enforcement tail closed
(`fada9cb`): `POST /v1/tasks` and butler `POST /v1/message` accept a signed task-scoped
grant (`X-Agency-Grant` bound to the exact HTTP scope) as a first-class alternative to
bearer tokens — verified actuator-side through `grant_guard.enforce_grant` (nonce consumed
at the point of use, so replays fail closed) against the pinned genesis membership, with an
optionally file-backed ledger (`AGENCY_GRANT_STORE_DB` -> `DurableGrantStore`); invalid,
expired, revoked, replayed or wrongly-scoped grants fail closed 401/403 from a fixed public
catalog; bearer behaviour is byte-for-byte unchanged (any presented `Authorization` header
always takes the bearer path and a bad bearer is never bypassed by a grant); every intake
records `auth_kind=bearer|grant` plus the verified grant digest in its audit/log line.
Live multi-peer actuator demo (`fada9cb`): `scripts/d6_actuator_demo.py` — four
`identity_store` Ed25519 peers, pinned genesis, a uvicorn api process with file-backed
stores on loopback, revocation pre-seeded into the durable store — bearer 201, grant 201
(task bound server-side to the verified issuer peer id), replay/expired/revoked/
wrong-scope 403, tampered 401, both auth kinds observed in the server log; evidence at
`D:/agency-merge-backups/d6-actuator-evidence.json`. Also fixed en route in `fada9cb`:
`TaskStore` explicit transactions are serialized by an in-process lock (the api intake and
the dispatcher share one aiosqlite connection; concurrent `BEGIN IMMEDIATE` used to raise
"cannot start a transaction within a transaction"). Downstream fencing-token commit closed
(`2566588`, branch `gap/18-fencing-commit`): the `POST /v1/tasks` grant path stamps the
verified grant digest server-side on the task record (`input["grant_digest"]`; any
client-supplied value is always stripped, bearer intakes unchanged), and the executor —
`AgencyOrchestrator._execute_fenced`, the single seam both the dispatcher and inline
callers pass through — commits the resource-side fencing token bound to (grant digest,
task id) before the consequential pipeline runs and records an idempotent finality receipt
after it, on the existing `FencingStateStore` (`agency.consensus.fencing_commit`, now WAL
per gap-19 discipline). A re-executed attempt replays the existing receipt instead of
re-running the work; a grant revoked after intake, a fencing-store error, and an
orchestrator without a fencer all refuse the execution fail-closed with honest FAILED
outcomes (`task.grant_fencing_refused`); the api and butler lifespans both attach the
fencer (`attach_fencing_commit`, `AGENCY_FENCING_DB` for a durable store, per-process
in-memory otherwise — mirroring `AGENCY_GRANT_STORE_DB`). Honest limits: the grant object
is not re-verified at the executor (only the intake-verified digest is held there, and the
nonce was consumed at the point of use, so re-verification would be a replay by
construction); a crash between execution and receipt commit leaves the effect without a
receipt (external-resource reconciliation stays a follow-up, as in
`agency.consensus.fencing`); a concurrent duplicate commit of the same digest is refused
by the store's append-only receipt log (stale epoch). Still open: `POST /v1/agents` stays
503 (spec-pinned, see #21e — enablement is an owner decision); production peer
certificates from a real PKI. The multi-node-consensus item is materially covered by the
landed Slice C live proof (`docs/SLICE_C_LIVE_EVIDENCE.md`: four real cometbft 0.38.26
validators on loopback, each with its own ABCI host child over TCP, real
`/broadcast_tx_sync`/`/tx`/`/commit` RPC, repeat runs PASS — what remains there is
real-PKI certificates, not consensus). Tokens are plaintext over HTTP — bind servers to
loopback or terminate TLS. The TLS termination leg advanced 2026-11-22 (`0fe1d0c`,
`4810b82`, `5d328fa`): the owner pilot launcher accepts optional
`--tls-certfile`/`--tls-keyfile` (both-or-nothing, fail closed on a half pair via
`tls_cert_and_key_required`, defense-in-depth re-check in the server-config builder;
loopback bind, bearer, CSRF and ACL posture unchanged), and `scripts/gen_test_ca.py`
generates a local test-only CA + server leaf (SANs for localhost/127.0.0.1, refuses
overwrite, documented LOCAL TESTS ONLY / never deploy). The hermetic e2e makes a real HTTPS
request against the launched app with the chain verified through the generated CA
(`verify=False` nowhere). Production peer certificates from a real PKI remain external and
no other HTTP surface is TLS-wired in this change.

## 19. Dispatcher/task-store operational bounds (2026-11-20) — CLOSED `3a3cd91`, `a34d90d`, `11f7600`
Tasks persist in `data/tasks.db` (`AGENCY_TASKS_DB`); claims are atomic across the api and
butler processes, so both dispatchers share one queue. Closed since then:
the LLM 402/429/5xx burst item (`cfd9eb1`, provider-aware backoff in `LLMAdapter.generate`);
task-claim leases (`3a3cd91`) — claims stamp `lease_owner` + `lease_expires_at` (TTL
`AGENCY_TASKS_LEASE_SECONDS`, default 300 s), the dispatcher heartbeats in-flight tasks, and
`AGENCY_TASKS_RECOVER=1` now requeues only expired (or pre-lease) claims, so a second live
process's running tasks are left alone — safe multi-process recovery requires every claiming
process to run a lease-aware build (pre-lease processes do not heartbeat, so their claims
expire like dead ones); and shared durable backends (`a34d90d`) — `AGENCY_AUDIT_DB` /
`AGENCY_EVIDENCE_DB` / `AGENCY_MEMORY_DB` point the api and butler orchestrators at the same
file-backed SQLite stores (`:memory:` defaults unchanged, so the hermetic suite is untouched).
The last open item — last-writer-wins status updates with best-effort transition checks — is
closed too (`11f7600`): cross-process status writes are compare-and-set. `TaskStore.update_task`
guards its UPDATE with the caller's expected status inside one `BEGIN IMMEDIATE` transaction
(the claims discipline) and returns a typed `TaskWriteResult` (applied / yielded_terminal /
stale / missing); `TaskManager.update_status` threads the status it read into the store call
as the guard, so every status-writing caller (dispatcher-executed tasks, delegation timeouts,
the `PATCH /v1/tasks/{id}/status` route, forge runs) is covered centrally without per-caller
edits. COMPLETED/FAILED records are immutable — exactly the existing transition matrix's
no-outgoing-edge set; BLOCKED stays re-openable because BLOCKED -> PENDING is the documented
requeue path — so a late writer observes the stored terminal record and yields (logged
`task.status_write_yielded` / `task_store.terminal_write_refused`, no exception, no duplicate
`child_result` notification on the parent). A stale write — the record moved to another
writable state between the writer's read and its CAS, e.g. a zombie executor finishing a task
that recovery requeued — fails closed with a ValueError naming the actual status, never
overwrites blind; illegal transitions keep failing closed as before. The kernel keeps its
five-state enum (no UNCERTAIN member): interrupted RUNNING work is protected by the same
rule — no stale writer can terminalize it — and stays with the explicit lease-expiry recovery
path, which requeues it honestly rather than resuming it automatically. Honest limits of the
lease: a hung-but-alive process (blocked event loop, no heartbeats) has its leases expire and
its tasks requeued while still executing, and a task claimed by a process that crashed less
than one lease TTL ago is only recovered after the TTL lapses; since `11f7600` the requeued
task's record is safe either way — the zombie's late terminal write fails closed once the
record moved (PENDING/BLOCKED), but when recovery already re-claimed it RUNNING the status
guard cannot tell two RUNNING claims apart (lease ownership is deliberately not part of the
guard; wave F lease/heartbeat semantics unchanged), so first terminal write wins there.

## 20. Autonomy-engine assignments still never execute — CLOSED `f68d62f` (2026-11-21)
`run_autonomous` still stops after assign/spawn/forge/proof, but the new
`dispatch_assignments` leg (`src/agency/autonomy/engine.py`) now materialises every
planner-assigned subtask as a delegated child task: `submit_autonomous_task` mirrors spawned
agents into the identity registry, submits the umbrella parent task, then binds
`AgencyOrchestrator.delegate_task` (depth-bounded, per-child deadline via
`autonomy_delegate_timeout`, metered, through the shared dispatcher queue) as the engine's
delegate. Outcomes are honest: pre-creation rejections (unknown target/parent, depth limit,
no dispatcher) record FAILED; an expired wait records UNCERTAIN and is never re-dispatched;
COMPLETED carries a result digest; the proof bundle records
dispatched/dispatch_failed/dispatch_uncertain/dispatch_skipped. With
`assignment_journal_path` configured, the durable A5 `AssignmentJournal` records
ASSIGNED→RUNNING→terminal atomically with its outbox rows under its bounded id scheme (real
ids stay in the proof bundle and the new `RunResult.dispatches` field); progressed records
are never re-dispatched, and journal errors fail closed by propagating. Cross-restart
reconciliation of never-dispatched ASSIGNED rows — dispatch and its ASSIGNED→RUNNING write
share no intervening await, so a row still ASSIGNED at journal open comes from a killed
process and provably never started — is CLOSED `ea998d7` (2026-11-22): the journal's
open-time recovery, which already flipped interrupted RUNNING rows to UNCERTAIN, now also
claims every stuck ASSIGNED row with an atomic ASSIGNED→FAILED write plus its own
hash-chained outbox entry, whose FAILED-directly-from-ASSIGNED signature (no RUNNING entry
in between, no invented digest or clock) is the audit record of "never dispatched,
resolved at startup". RUNNING rows keep the UNCERTAIN recovery and are never resolved by
the new path (live-owner lease discipline), terminal rows are untouched, concurrent starts
serialize on the write lock so nothing double-resolves, a crash mid-recovery rolls back and
leaves rows for the next start, and journal errors propagate fail closed from the
constructor. Re-dispatch through the delegate was rejected deliberately: plan ids are fresh
uuid4s and the journal stores no free text, so the subtask payload, parent task binding and
agent id exist nowhere durable to honestly re-dispatch from. Forge-domain
children keep routing through BlacksmithOps (owned by
`docs/PLAN_BLACKSMITH_FORGE_ORCHESTRATOR.md`, unchanged). Still out of scope:
parallel/dependency-aware dispatch (sequential in plan order today), and the pre-existing
umbrella parent execution alongside the children.

## 21. Task-lifecycle audit items (2026-11-20 audit, disposition 2026-11-21)
Of the three audit items outside gap 19: (d) delegated child tasks' results never returned
to the submitter — CLOSED `3a3cd91`: a terminal child now records its
result on the parent task record (`TaskManager.update_status` appends a `child_result`
message to the parent, visible via `GET /v1/tasks/{id}` and `TaskManager.get_task`; the
child's own `output`/`error` stay authoritative; a crash between the terminal write and the
append is honest UNCERTAIN — the parent may never learn, the child record always holds the
result), and `TaskDispatcher.wait` re-polls the shared store so a delegation wait resolves
even when another process's dispatcher executed the child (completion events are
process-local). (e) agent intake path 503-blocked — LEFT BLOCKED deliberately (`17f1d95`
pins the boundary): the route is
`POST /v1/agents`, gated by `agency/api/authority.py` (`require_peer_authority`, "deliberately
not an owner-token switch") and the `docs/SPEC_BETA_BOUNDARY_AND_PEER_EXECUTION.md` release
gate ("until peer certificates and actuator checks exist, no consequential route may
create/revoke agents"); `tests/test_agent_intake_boundary.py` pins that `AGENCY_API_TOKENS`
does NOT open it. The D6 grant-verifier dependency has now landed (see #18); route
enablement still requires the full verifier swap on consequential routes and an explicit
owner decision, not a bearer-token switch. (f) task decomposition is
regex-only (`agents/planner.py::_default_strategy`) — CLOSED
`fd87401` (2026-11-22, live): an LLM-backed decomposition strategy landed on the existing
`AgentPlanner` strategy seam (`agents/planner.py::make_llm_strategy`); the default-strategy
resolver picks it whenever the configured provider is not echo/offline and asks the LLM to
split the task into 1-6 subtasks (short title + description + suggested lowercase
capabilities from the planner vocabulary), parsed and validated strictly (exact key set,
count cap, string bounds; one code fence tolerated). Any failure — malformed or
budget-truncated reply, over-cap plan, empty completion, timeout, provider error — falls
back to the regex `_default_strategy` with an honest `planner.llm_decompose_failed` log
(class only, never reply content). Prompt hygiene: only the task text goes out (bounded to
4000 chars) — no tool definitions, no system/memory content; the strategy-owned adapter
caps output (1024 tokens, temp 0.2). Selection reads configuration only (no network call)
and stays regex under echo, so the hermetic suite is untouched
(`tests/test_planner_decomposition.py` stubs the adapter). The regex strategy remains the
offline floor and the guaranteed fallback. Live-validated on CentAI `hy4-flash:free`: 3/3
sample tasks produced 5-subtask plans within the cap (an earlier run honestly fell back on
a budget-truncated reply and an empty free-tier completion, which motivated the 512→1024
token budget); evidence `D:/agency-merge-backups/decomp-evidence.json`, script
`scripts/decompose_live_demo.py`. Honest caveats: the sync `plan` seam blocks the calling
thread for the bounded LLM call (on the orchestrator's legacy `_execute_running` path that
thread is the event loop), and subtasks still reuse whole-task prompts downstream
(unchanged).

## 22. Forge efficiency/autonomy gaps (2026-11-27)

Recorded after main reached `ada31e8`, following the parallel-build proof and three CI-fix PRs (#98–#100).

1. **Model routing is only partly wired.** `routing.py` selects a model, but the worker command receives it only if the command template contains `{model}`; default commands do not. Outcome: route model applied automatically and visible in evidence.
2. **Review config is manual.** Without `--env-file` or a correctly launched MCP process, review falls back to echo. Outcome: repo-local `.env` discovered automatically, values never logged, provider actually used recorded in evidence.
3. **Runtime verification is standalone.** `forge verify` exists but risky jobs do not run it automatically. Outcome: risky route runs a runtime gate when command/endpoints are supplied; FAIL blocks evidence.
4. **Plans are checked for shape, not quality.** Allow-list catches scope creep but vague plans pass. Outcome: non-empty allow-list, named test command and risks required for code jobs; explicit caveat for legacy plans.
5. **Apprentice is a label, not a worker path.** No distinct cheap path or enforced promotion gate. Outcome: bounded tool budget, risky classes refused, promotion evidence recorded.
6. **Live state and cost are weak.** `UNCERTAIN` while running, no heartbeats or token estimates. Outcome: lease/heartbeat updates, watch shows real state, trace includes agent duration and estimated cost where available.
7. **Integration stops early.** `forge integrate` combines disjoint work but runs no suite. Outcome: tests run after combination and branch reported ready; still no merge.
8. **Self-improvement is not fed by CI failures.** `self_improve.py` exists but is disconnected. Outcome: bounded ingestion of gate/CI failures, idempotency keys, candidate jobs, human approval before action.

Cross-cutting rule for all 22+: no secrets in files or logs, no push/merge/deploy from workers, focused tests plus Ruff/Mypy gates, commit on own branch.
