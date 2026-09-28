# Free-only decentralized Agency program — evidence and approval checkpoint

Status: RESEARCH / PROPOSED IMPLEMENTATION PROGRAM. No new runtime feature, production rollout, BFT engine selection or consequential execution is approved by this document alone.

Source baseline: `1e38912cbc5767fe1ee599f10019f60214ba4f03`.
Research date: 2026-09-28. Program branch: `spec/free-only-decentralized-program`.

## 1. User request and decisions

Build as many useful integrated Agency features as possible with free Cline CLI models, Freebuff and OpenCode Muse Spark 1.3 doing the heavy work. Research autonomous-system practices and failure recovery; preserve full decentralized authority. The coordinator may plan and independently verify, but must not substitute paid coding workers.

New explicit owner decisions:
- Consequential peer decisions must tolerate Byzantine/compromised peers, not merely crashes.
- Every affected consumer must give attributable input. Missing input blocks execution.
- A consumer rejection is recorded evidence, NOT an individual veto; the approved peer decision policy may override it.

These resolve the fault-class and veto questions in `SPEC_KNOWN_PEER_PROTOCOL.md` and `SPEC_BETA_BOUNDARY_AND_PEER_EXECUTION.md`. They do not choose validator count, implementation/version, membership-change mechanism, human confirmation mechanism, machine placement or release permission. This program preserves the standing research -> specification -> commit -> approval -> implement workflow. A feature counts only when wired, tested and independently checked; no success quota based on files or stubs.

## 2. Honest current state and scope of inventory

The tracked inventory at this revision contains 36 documentation files, 147 source files and 73 test files. These counts are inventory, not a claim that every historical transcript was re-extracted or that every line has been audited. The complete consolidated product brief is assigned to the feature-audit worker. Narrow worker reports accompany this document and remain subordinate to independently checked code.

### Worker outputs and review

- [Feature gap map](RESEARCH_FREE_PROGRAM_GAP_MAP.md): free MiMo performed broad source lookups but timed out before writing; free Muse recovered a bounded report from archived successful source-tool outputs. It explicitly leaves uninspected areas UNVERIFIED; it is not an exhaustive implementation-ready product audit.
- [Recovery report](RESEARCH_FREE_PROGRAM_RECOVERY.md): the MiMo lookup run likewise timed out; free Muse produced the report and a correction. Independent review caught and rejected a reversal of the owner's Byzantine requirement, an unsafe retry/compensation suggestion, and misleading model-budget wording. The integrated version corrects these; worker completion alone was not trusted.
- [BFT report](RESEARCH_FREE_PROGRAM_BFT.md): free Muse completed directly. Review strengthened the laboratory gate to require a successful synthetic authorized transition, not only refusals.
- Evidence-only snapshots are retained under `docs/research-evidence/` with the baseline identified. They contain successful source lookup results, not model reasoning or private runtime data. References to snapshot line numbers are historical evidence, not current live code.
- Two MiMo audit runs ended at their configured time limits. Cline headless `--id` continuation failed for both positional and piped prompts; fresh explicitly pinned free Muse workers recovered the reports. All completed model-worker receipts inspected reported zero cost. This is provider-reported usage, not an independently audited account bill. No paid coding worker was used.
- Only documentation is integrated at this checkpoint. Source/tests/configuration, canonical runtime data and the running bot are unchanged by the program.

Verified directly:
- `src/agency/peer_envelope.py:1-6`: cryptographic envelopes explicitly do NOT supply transport authentication, membership, durable replay, consensus or grants.
- `src/agency/api/authority.py:13-18`: sensitive HTTP authorization currently always refuses with 503; this is a safe closed door, not a functioning peer authorizer.
- `src/agency/lattice/governance.py:144-147`: legacy direct governance still has a `user` string override. Entry-route guards do not turn this into decentralized authority.
- `src/agency/telegram/budget_store.py:563-572`: model reservation API exists; repository-wide source search finds only its definition/delegation, not production callers. All outbound model paths must be audited before beta enablement.
- `src/agency/agents/executor.py:126-129`: execution status/running tasks/cancellation live in local memory. Retry and timeout are not restart recovery.
- `src/agency/workflows/executor.py:1-6`: deterministic executor explicitly disclaims automatic resume/exactly-once execution.
- `src/agency/forge/inspection.py:1-9`: Forge is a bounded read-only inventory/syntax gate, not a complete autonomous software factory or release authority.
- `docs/BETA_GAPS.md` current canonical checkpoint identifies remaining beta wiring, SSRF, live-delivery, peer execution and recovery gates. Historical table rows must not be treated as current truth without the newer checkpoint.

Fresh baseline checks in isolated `agency-free-program`, not the canonical live-data tree:
- `AGENCY_LLM_PROVIDER=echo AGENCY_LLM_MODEL=echo PYTHONPATH=src ...python.exe -m pytest tests/ -q`: **1146 passed, 23 warnings**, 132.81 seconds, exit 0. Warnings include aiosqlite worker threads reaching a closed event loop; retain as technical debt, not a clean-shutdown claim.
- Ruff check: passed; format check: 216 files already formatted.
- Mypy: no issues in 144 source files.
- `node --test tests/prototype_ui.test.mjs`: 14 passed, 0 failed.
- These are local echo-provider/prototype checks, not a live model, peer network, hostile sandbox, beta or production readiness proof. Explicit echo configuration was used; this run was not protected by a process-level outbound-network blocker.

## 3. Free-only workforce policy and observed readiness

Only explicitly pinned free model identifiers may run. Verify the current provider free catalog and a completed smoke before each batch. Disable automatic paid fallback and refuse a changed price/model route. Record actual provider/model, result, usage/cost field, task, base SHA, branch, diff and verification. Free quotas and availability are external constraints; pause on exhaustion rather than claiming work continued. Do not rotate accounts, spoof clients, bypass regional limits or provider restrictions.

Observed CLI results:
- Cline 3.0.65, `--provider cline --model cline-free/mimo-v2.6-flash`: exact smoke reply, completed, reported `totalCost=0` and zero pricing metadata. Used for feature/recovery audits.
- Cline 3.0.65, `--provider cline --model cline-free/muse-spark-1.3-contributor`: exact smoke reply, completed, reported `totalCost=0`. Used for BFT review. This is Muse through Cline, NOT a claim that OpenCode worked.
- OpenCode 1.18.32: catalog lists `opencode/muse-spark-1.3-contributor-free`. Two native CLI smoke attempts returned 403 `OpenCode's free tier can only be used from within OpenCode`; a `big-pickle` control probe returned the same 403. Installed version matched npm registry lookup. No work credited to OpenCode and no provider restriction bypass attempted.
- Freebuff 0.1.0 auto-updated itself to 0.1.2 during startup. UI showed GLM 5.3 Flash and free allowance, but two interactive sessions yielded no completed smoke response through this PTY. Sessions were stopped. Installed/visible allowance != verified usable worker. No Freebuff implementation credited.

Privacy: OpenCode's Muse contributor terms permit prompts/completions to train future models; Cline free-model documentation permits model-improvement use; Freebuff discloses prompts/code/repository processing and ad-personalization practices. Never submit `.env`, tokens, private chats, runtime DBs, raw transcripts or user data. Workers receive narrowly named source/docs with `.clineignore` exclusions and no authority to browse other directories. Git worktrees are collision isolation, NOT a security sandbox. Before untrusted code execution, use a separately verified OS/container sandbox with no host secrets and bounded network/resource access; prompts/ignore files alone do not enforce that boundary.

## 4. Architecture recommendation — BFT, not agent roleplay

Recommend evaluating a maintained BFT replicated-state-machine engine (CometBFT is the first candidate) behind a local adapter rather than having free LLMs invent Python consensus. Pin version, license, supported deployment platform and compatibility before adoption. Use a bounded integration spike before committing to the dependency. Raft is a valid crash-fault alternative but does not meet the newly selected Byzantine model. Gossip/CRDTs can disseminate observations but do not authorize conflicting mutations. A queue or shared database cannot be the network authority.

Proposed initial lab: four equal-weight independently keyed validators, tolerating one Byzantine validator, with a three-validator BFT commit threshold. This is a PROPOSAL requiring approval; the threshold is only one part of the full protocol. Real round/lock/finality proofs must come from the engine, not a count of three application approval messages. Its assumptions include fewer than one-third Byzantine voting power and eventual synchrony for progress. A local four-process test is not four independent failure domains or protection from a host administrator who can steal every key.

Separate layers:
1. Model deliberation produces redacted evidence/recommendations, never authority.
2. Application policy validates membership/relationship versions, complete affected-consumer input, permissions, proposed action and explicit approval rules.
3. BFT consensus orders/finalizes deterministic application transitions; it does not guarantee evidence is truthful, a model answer is correct, or a human really consented.
4. Every actuator independently verifies a final committed authorization, action/parameter digest, local policy, freshness, scope, resource fence and durable idempotency before acting.

A round proposer schedules consensus work; it has no unilateral decision power. Every peer has its own process, signing identity and durable store. Butler/Telegram/GUI hold no validator signing keys. The development coordinator is not installed as a runtime governor. No universal bypass for `user`, `system`, local loopback, admin token or model output. ATHENA remains a separate peer platform, not a dependency.

Human input proof remains a gate: a compromised gateway can fabricate a Telegram-origin attestation. Recommend a human-held signing credential over proposal/action digest (with explicit enrollment/recovery), or separately reviewed independent verification. A single gateway signature cannot count as Byzantine-resistant proof. Missing proof or incomplete authenticated affected-set graph blocks human-affecting execution. Negative input may be overridden only by the approved application policy, with recorded reasoning/evidence; legal consent and resource access rights are not erased by a quorum.

## 5. Failure and recovery contract

- Provider 429/outage/free-quota exhaustion: classify; honor Retry-After; capped exponential backoff with jitter; finite attempts and total deadline; persist WAITING_PROVIDER/BLOCKED, show honest status. Only switch within an approved free allowlist. An error/echo is never a successful result.
- Peer crash: peer-local supervisor restarts with crash-loop limits; reopen its own committed store; validate identity/state version; reconcile pending operations before becoming ready. Heartbeats indicate liveness, never authority.
- Partition: no valid commit proof -> no consequential action. Do not lower quorum, invent missing consumers or elect an omnipotent coordinator. On reconnect, verify committed history/snapshots before catch-up; bound buffering/gaps and reject competing membership roots.
- Duplicate/reordered delivery: durable inbox dedup plus transactional outbox. Persist outgoing exact signed bytes and sequence before send; retry identical bytes. Bind replay tracking to signer/membership. State transition and dedup record must be atomic.
- Unknown external outcome: intent persisted but timeout/crash before acknowledgement -> INDETERMINATE. Retry only if the actual target supports stable idempotency/fencing and reconciliation proves safe. Otherwise require review. Exactly-once external effects are NOT guaranteed by SQLite or an outbox alone.
- Audit/disk/lock failure: reject new work; no success reply or success memory write after durability failure. Short bounded lock retry, then visible failure. Separate readiness from process-alive health.
- Stale worker: local lease expiry does not cancel a remote action; resource-side fencing must reject old generations. Never retry a mutating action merely because a worker vanished.
- Workflow recovery: persist versioned workflow/step state and input/evidence digests. Resume only explicitly replay-safe registered operations. Compensation is an explicit auditable operation, not automatic reversal of arbitrary effects.
- Overload: bounded queues/concurrency, request deadlines, retry budgets, per-user/provider ceilings and load shedding. Reserve every outbound model attempt durably before provider dispatch, including classification, retries, forced-final and fallback paths.
- Corruption/rollback: quarantine store, validate backup and history; do not silently erase/rebuild or reuse signing sequences. Snapshot rollback must not permit double-signing. Restore drills must include key/sign-state handling.
- Whole-host failure: same-host processes all stop. Production resilience requires separate hosts/keys/admin boundaries and a tested recovery plan; no claim of it from local tests.

## 6. Delivery waves and ownership

All proposed paths below are NEW design contracts, not already existing interfaces. Detailed implementation briefs must derive from approved contracts and freeze method/type names before parallel consumers start. A foundation worker lands first; consumers do not invent private fallback implementations.

### Wave A — prerequisites and useful local recovery

A0: baseline regressions and lifecycle fixes. Own `src/agency/kernel/audit.py`, focused audit tests. Reproduce same-instance initialize/close races and eliminate resource leaks without weakening audit failures. Independently measure warning deltas; unrelated warnings stay tracked.

A1: authoritative model-dispatch metering seam. Own `src/agency/llm/` plus new focused tests; consumer integration into Butler/ToolDriver/Executor is a subsequent single-owner step. Trusted request context plus atomic `reserve_model_call` must precede EVERY beta request. No beta enablement until positive authorized path and denial/failure paths both pass.

A2: durable local execution store and recoverable read-only workflow. Own new `src/agency/runtime/{models,store,recovery}.py` and corresponding tests. Consumer integration owns `src/agency/agents/executor.py`, `src/agency/workflows/executor.py` and CLI only after store contracts merge. Non-authoritative local scheduler; cannot grant peer capabilities.

A3: read-only health/status and failure observability. Own new runtime health module and CLI tests after A2. Report actual store/provider/peer readiness, waiting/unknown work and limits without leaking prompts/keys. UI integration later; no parallel edits to shared CLI files.

### Wave B — independent peer substrate and BFT evaluation

B0: document/test engine choice, versions, key ownership, membership genesis, human-input proof and exact application policy. No mutating actuator until this separate checkpoint is approved.
B1: peer-local pinned membership, durable replay/inbox/outbox using existing envelope primitive where compatible. Own new `src/agency/peers/{models,store,membership}.py` and tests. No schema silently overrides established signed-byte contracts.
B2: authenticated transport + independent peer CLI. Own new peers transport/runtime modules, then a sole CLI integration worker. Mutual identity binding, bounded body/queue/deadline, replay handling and independent stores; transport success is not consensus.
B3: maintained BFT adapter and deterministic application validation in a synthetic-resource laboratory only. Own `src/agency/peers/consensus/` and multiprocess fixture. Independent validation of finality/membership/affected-input evidence; no custom threshold-only consensus.
B4: adversarial process/fault harness and grant-verifier tests by a worker that did not author the adapter. Kill proposer, Byzantine equivocation, duplicate/reorder, partitions, state replay, unknown membership, key revocation/rollback and incomplete consumer inputs. A failed safety test halts this wave.

### Wave C — operational features after foundation proof

C1: trusted Telegram -> Butler -> strict search-only registry with durable budget/audit/result propagation; no beta invitation before all gates and owner approval.
C2: persistent scoped threads/memory, versioned skills/workflows and useful resumable read-only tasks. Reuse existing stores/registries; don't create duplicates. Privacy tests per sender and after restart.
C3: Agent Factory and policy/identity changes only behind real grant/fencing validation across HTTP, CLI, direct service and tool paths.
C4: Forge branch/worktree job lifecycle, free-only worker catalog, bounded execution, evidence capture, independent review and human release gate; do not call syntax inspection a build pipeline.
C5: GUI status/cancel/recovery, tool/domain-agent expansion and bridges. Each feature requires the same grant/privacy/budget boundaries, a useful end-to-end success scenario and rollback procedure. No hostile-code tools until the isolation backend is verified. `web_fetch` stays beta-disabled until DNS validation is bound to the actual connection.

A feature backlog is not approval to widen beta, execute financial/security actions, publish, push, merge or deploy. Prefer finishing a small complete vertical slice over launching every domain at once.

## 7. Proposed shared interfaces for first implementation briefs

```python
# Proposed immutable boundary types; not current production symbols.
ExecutionRecord(run_id, owner_ref, definition_digest, state, attempt,
                deadline_ms, lease_generation, input_digest, outcome_digest)
# states: RESERVED, RUNNING, WAITING_PROVIDER, COMPLETED, FAILED,
#         CANCELLED, INDETERMINATE
LocalRunStore.reserve(record) -> ReservationResult
LocalRunStore.claim(run_id, worker_id, lease_until_ms) -> Lease
LocalRunStore.transition(run_id, expected_state, lease_generation,
                         new_state, outcome_digest) -> TransitionResult
LocalRunStore.recovery_candidates(limit) -> tuple[ExecutionRecord, ...]
RecoveryCoordinator.reconcile(run_id) -> RecoveryDecision
# Never dispatches an unsafe/unknown side effect merely on restart.

MeteredModel.generate(prompt, context, *, trusted_request) -> ModelResult
# trusted_request is created by authenticated internal ingress, never by
# deserializing an arbitrary caller-provided identity dictionary.
# Durable reserve attempt -> bounded provider call -> durable outcome.

PeerInbox.accept(authenticated_peer, wire_bytes) -> Receipt
PeerOutbox.enqueue(signed_wire_bytes, destinations) -> MessageRef
ConsensusAdapter.submit(command_bytes) -> SubmissionRef
ConsensusAdapter.verify_commit(proof_bytes, pinned_membership) -> FinalCommit
GrantVerifier.verify(final_commit, action_digest, resource_state) -> VerifiedGrant
Actuator.apply(verified_grant, idempotency_key, fencing_token) -> Outcome
# Raw signed envelopes and model text cannot construct a VerifiedGrant.
```

LocalRunStore uses peer-local SQLite, explicit durability mode, atomic transitions/uniqueness and bounded busy timeouts. It is not a shared global work queue or governance DB. Preserve separate runtime paths from the tracked canonical lattice DB. Exact SQL migrations, receipt schemas and network endpoints require the component spec approval, not improvisation by parallel workers.

Proposed peer network API: authenticated `POST /peer/v1/messages` for bounded canonical bytes, authenticated bounded recovery exchange, and redacted read-only readiness. No unauthenticated proposal/grant/membership mutation API. Health may reveal process liveness without exposing private membership/topology; full peer status requires scoped access. All endpoint bodies/error codes and authentication bindings must be frozen in B1/B2 specs.

## 8. Completion and release gates

Completion is a vector, not 'tests green': useful behavior, safety, restart durability, failure handling, independence, privacy, deployment and free-only evidence must each be demonstrated for the exact revision.

Required executable scenarios:
- Accepted read-only request -> real permitted model/search result -> correct citations/status -> durable sanitized audit; denied requests cause zero provider/tool calls.
- Kill/restart during each state boundary; no lost acknowledged state and no duplicated non-idempotent side effect; unknown outcomes visibly unresolved.
- Four actual independent peer processes/stores/keys in the proposed lab; Butler stopped and proposer killed; deterministic authorized synthetic action still commits when assumptions permit.
- Partition without adequate proof -> zero consequential effects; heal -> verified convergence. Missing consumer -> zero execution even with otherwise valid consensus. Present rejection follows approved policy, not fabricated assent.
- Forged/equivocating/replayed/stale-epoch messages rejected; conflicting proposals cannot both execute; stale worker fencing rejected at target resource.
- Disk full/unwritable/audit commit failure -> failure before success claim. Readiness becomes false and recovers only after reconciliation.
- Backup -> controlled corruption -> restore -> replay protection/signing state intact. Actual restore and rollback drill, not just backup-file creation.
- No `.env`/private data in worker inputs, artifacts, logs, evidence, prompt traces or commits. No paid model, hidden model fallback or quota bypass.
- Full pytest, Ruff check/format, mypy, UI checks and independent security review. Platform CI and live checks are separate claims.

Deployment needs a verified sandbox/platform, independently provisioned peer hosts/keys for production fault independence, secrets enrollment, backup, rollback, single Telegram poller and owner-side reply verification. Keep current bot and canonical runtime DB untouched while researching/building. No deployment from a merely green lab.

## 9. Primary sources and how used

- Anthropic, Building effective agents: https://www.anthropic.com/engineering/building-effective-agents — simple composable workflows, explicit tools, bounded loops, environmental evidence and evaluation; not endorsement of central runtime authority.
- AWS, timeouts/retries/jitter: https://aws.amazon.com/builders-library/timeouts-retries-and-backoff-with-jitter/ — timeouts, capped retry/backoff/jitter and unknown side effects.
- AWS, idempotent APIs: https://builder.aws.com/content/3Ev0BENTyBr0XxzRk5FDZzgNYos/making-retries-safe-with-idempotent-apis — stable request identifiers and atomicity.
- AWS, transactional outbox: https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/transactional-outbox.html — state + outbox in one transaction; dedup consumers; not exactly-once external execution.
- Google SRE, cascading failures: https://sre.google/sre-book/addressing-cascading-failures/ — bounded queues, load shedding, propagated deadlines, overload recovery.
- CometBFT full consensus specification: https://raw.githubusercontent.com/cometbft/cometbft/main/spec/consensus/consensus.md — rounds, locks, commit proofs, Byzantine assumptions; candidate only, not selected/installed.
- Raft paper: https://raft.github.io/raft.pdf — crash-fault comparison, not chosen BFT protocol.
- Temporal workflow execution: https://docs.temporal.io/workflow-execution — durable history/replay pattern; a Temporal service would not be peer governance authority and is not added as a dependency.
- OpenCode pricing/privacy: https://opencode.ai/docs/zen/ — exact free identifier and contributor data-use caveat.
- Cline free models: https://docs.cline.bot/getting-started/free-models ; live catalog https://api.cline.bot/api/v1/ai/cline/recommended-models — free lanes rotate, CLI-only promotions, model-improvement use; exact live probes are stronger than listings.
- Freebuff CLI README: https://raw.githubusercontent.com/CodebuffAI/freebuff/main/freebuff/cli/release/README.md — free service limitations/data processing; catalog descriptions varied across fetches, so live UI is not a completed-model proof.

## 10. Next approval scope

Recommend approving Wave A specifications/implementation and a non-actuating Wave B laboratory, subject to detailed component contracts and free-provider availability. Keep production mutations, beta invites and deployment separately gated. Before BFT actuation, approve validator/fault count, concrete engine/version, application decision policy, human confirmation, membership changes and independent host/key placement. Broad 'fully operational' is a release objective, not an assertion that these gates are already closed.
