# Known gaps — requested-agent / capability / factory / skills

Found while implementing `RequestedAgentService` (`src/agency/factory/requested_agent.py`).
Ordered by production risk. **Avoid these patterns; all agents are free to fix them.**

**Status 2026-11-22 (full-gap sweep):** 16 of 20 closed — gaps 1-9, 11-16 and 20; gaps 18
(grant path live on the authed intake routes; real PKI + multi-node consensus remain), 19
(leases + shared stores, no last-writer-wins CAS) and 10 (live adapter + live task qualified
on CentAI `hy4-flash:free` 2026-11-22; improve leg + soak remain) are partially closed —
commit references below; gap 20's ref is on `gap/autonomy-execution`, the Tier6 refs landed
on `main` (`37095b9`) and the consolidation line (`f981282`). Gap 17 remains open for an
external reason (no real PAK repository to wire against); gaps 18-20 were added by the
intake workstream (2026-11-20).

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

## 10. No live-provider qualification of this flow — PARTIALLY CLOSED (2026-11-22, live)
Qualified live on an OpenAI-compatible provider (CentAI `api.cent-ai.dev/v1`, model
`hy4-flash:free`, configured via `AGENCY_LLM_PROVIDER=openai` + `AGENCY_LLM_BASE_URL` +
`AGENCY_LLM_API_KEY` in the untracked `.env`; key is never committed): (a) the Agency's own
adapter path (dotenv → `load_config` → `LLMAdapter.generate`) returned the exact expected
marker; (b) a real task ran through the full dispatcher pipeline — live planner (2 subtasks),
live executor (`attempts=1`), correct result (`42`), audit entries, memory write, terminal
`completed`, clean shutdown. The verifier scored the subtasks `0.667/failed` on a correct
answer — the known independent-checker strictness open item, recorded honestly as findings.
Still open: multi-restart and concurrency soak, and a second provider for comparison. The
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
the provider stays unavailable (observed and verified live). Do not claim full production
certification until the soak is exercised.

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

## 18. External HTTP intake — route swap live on the authed intake routes — PARTIALLY CLOSED `8604f8a`, `11854fa`, `37095b9`, `f981282`, `fada9cb`
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
"cannot start a transaction within a transaction"). Still open: `POST /v1/agents` stays 503
(spec-pinned, see #21e — enablement is an owner decision); production peer certificates
from a real PKI and multi-node consensus beyond the single-host demo; fencing-token commit
for downstream task execution (intake consumes nonces; resource-side fencing stays with the
execution actuator). Tokens are plaintext over HTTP — bind servers to loopback or
terminate TLS.

## 19. Dispatcher/task-store operational bounds (2026-11-20) — PARTIALLY CLOSED `3a3cd91`, `a34d90d`
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
Still open: status updates across processes are last-writer-wins with best-effort transition
checks (a cross-process compare-and-swap would have to thread an expected-status guard
through every caller — deferred as not cheap). Honest limits of the lease: a hung-but-alive
process (blocked event loop, no heartbeats) has its leases expire and its tasks requeued
while still executing, and a task claimed by a process that crashed less than one lease TTL
ago is only recovered after the TTL lapses.

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
are never re-dispatched, and journal errors fail closed by propagating. Forge-domain
children keep routing through BlacksmithOps (owned by
`docs/PLAN_BLACKSMITH_FORGE_ORCHESTRATOR.md`, unchanged). Still out of scope: cross-restart
reconciliation of never-dispatched ASSIGNED rows (dispatch and its ASSIGNED→RUNNING write
share no intervening await, so the window is a killed process), parallel/dependency-aware
dispatch (sequential in plan order today), and the pre-existing umbrella parent execution
alongside the children.

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
regex-only (`agents/planner.py::_default_strategy`) — REMAINS: the `AgentPlanner` strategy
seam is already pluggable, but with the hermetic echo provider an LLM-backed splitter cannot
be validated offline (no API keys on this machine, #10); adding an untestable live path was
rejected as not a clean bounded improvement.
