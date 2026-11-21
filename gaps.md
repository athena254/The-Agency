# Known gaps — requested-agent / capability / factory / skills

Found while implementing `RequestedAgentService` (`src/agency/factory/requested_agent.py`).
Ordered by production risk. **Avoid these patterns; all agents are free to fix them.**

**Status 2026-11-21 (full-gap sweep):** 15 of 20 closed — gaps 1-9 and 11-16, commit
references below are on `integrated`. Gaps 10 and 17 remain open for external reasons (no
local LLM API keys; no real PAK repository to wire against); gaps 18-20 were added by the
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

## 10. No live-provider qualification of this flow — OPEN (external blocker)
Verification remains offline. A live LLM run of request→task→improve plus multi-restart /
concurrency soak requires configured provider API keys — none exist on this machine. Run the
smoke after keys are set; do not claim production certification until then.

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

## 18. External HTTP intake is bearer-token authed, not peer-grant actuation (2026-11-20)
`POST /v1/tasks` and butler `POST /v1/message` are enabled behind owner-issued bearer tokens
(`agency/api/auth.py`, `AGENCY_API_TOKENS` file) with server-bound identity, scoped reads and
creator-only cancellation. This is *not* the §12 peer execution-grant path: `consensus/grants.py`
still has no actuator consumer (PLAN_V1 D6) and the grant-verifier replacement of
`require_peer_authority` (Slice D) remains future work. Tokens are plaintext over HTTP —
bind servers to loopback or terminate TLS. **Fix:** implement D6 actuator-side grant
enforcement and swap the authed routes onto the grant verifier.

## 19. Dispatcher/task-store operational bounds (2026-11-20)
Tasks persist in `data/tasks.db` (`AGENCY_TASKS_DB`); claims are atomic across the api and
butler processes, so both dispatchers share one queue. But: startup recovery of tasks caught
RUNNING is opt-in (`AGENCY_TASKS_RECOVER=1`) and single-process-only — a second live process's
running tasks would be wrongly requeued; audit/memory/evidence stores remain per-process
(`:memory:` defaults for the api server), so evidence rows are not shared; status updates
across processes are last-writer-wins with best-effort transition checks. Also (2026-11-20
live E2E): three near-simultaneous task executions hit the configured LLM provider's HTTP 402
(quota) under burst — per-caller/provider rate limiting or retry-with-backoff at the LLM
adapter is needed before this surface is load-bearing. **Fix:** shared
durable audit/evidence backends, a lease/heartbeat so recovery can distinguish dead from
live claimers, and provider-aware backoff in the LLM adapter.

## 20. Autonomy-engine assignments still never execute (2026-11-20)
`run_autonomous` stops after assign/spawn/proof; the general agent→agent path now exists
(`delegate_task` tool + `AgencyOrchestrator.delegate_task`, depth-bounded), but subtasks the
engine assigns are still not dispatched to their assigned agents. Forge routing
(`forge_build_strategy`, BlacksmithOps QUEUED→DISPATCHED) is owned by
`docs/PLAN_BLACKSMITH_FORGE_ORCHESTRATOR.md` phases 2-6. **Fix:** materialise engine
assignments as delegated child tasks once the BlacksmithOps state machine lands.
