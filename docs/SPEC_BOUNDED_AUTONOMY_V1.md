# Bounded autonomy v1 — one dependable Agency task

Date: October 16, 2026.
Status: **PROPOSED milestone contract; no runtime enablement or deployment approval.**
Baseline: `1c7e7da2774ee4687741a0e346cbf2d593a9c845`, based on tested source `ba9ec76896cd70df290f0fe70fe7c9ac0a7528ee`.
Branch: `docs/bounded-autonomy-20261016`.

## 1. Plain-English goal and approval boundary

Give the Agency one permitted goal and limits. It chooses useful next steps, observes actual tool results, checks its work, saves progress and returns the real artifact or an honest blocker without needing a human at every iteration. A restart must not erase acknowledged progress or authorize repeating an uncertain effect.

This is bounded autonomy for an explicitly admitted task class, not unattended general-purpose authority, full decentralization, production readiness, self-improvement or an autonomous software factory. The complete milestone includes independently identified participating peers; an in-process mock is only an engineering test.

The owner's instruction, "Start working towards this", starts research/specification/checkpoint work under the standing spec-first process. It does not select unresolved peer policy, authorize a live provider, enable beta execution, grant real filesystem access, merge main, push, restart the bot or deploy. Only the separate [A1 context repair](SPEC_AUTONOMY_CONTEXT_REPAIR_A1.md) is detailed enough here to request implementation approval. Later slices need their own frozen implementation contracts; do not build guessed protocol/SQL/wire details from this milestone outline.

Companion documents:
- [Source-backed gap map and research](RESEARCH_BOUNDED_AUTONOMY_20261016.md).
- [Request/decision traceability](research-evidence/bounded-autonomy-request-20261016.md).
- [Baseline diagnostic](research-evidence/bounded-autonomy-baseline-20261016.json).
- Existing authority requirements: `SPEC_SINGLE_HOST_LATTICE_MILESTONE.md`, `SPEC_FREE_ONLY_DECENTRALIZED_PROGRAM.md`, `SPEC_KNOWN_PEER_PROTOCOL.md`, `SPEC_PEER_LATTICE_GOVERNANCE.md`.
- Existing implementations to reuse: `SPEC_LOCAL_RECOVERY_V1.md`, `SPEC_MODEL_METERING_V1.md`, `SPEC_AUDIT_LIFECYCLE.md`.

## 2. Architecture invariants

BA-01. Human <-> Butler <-> the full Agency backend. Butler is the gateway, not an agent, planner, validator, voter or unilateral authorizer. Reasoning and work belong to Lattice peers. A development coordinator is not a runtime governor.

BA-02. Orchestration is a function of the Lattice's participating peers. Scheduling/coordination roles are replaceable. A launcher/supervisor manages processes, not permissions. Existing admitted work can progress with Butler offline unless additional human input is genuinely required; results wait durably for the gateway.

BA-03. Peers use separate processes, identities, keys and durable stores with authenticated explicit interfaces. One SQLite file, an in-process role roster, a successful signature check or an engine KV lab is not decentralized task authority. Single-host operation retains one host/admin compromise and availability domain.

BA-04. Byzantine/compromised-peer tolerance is the selected consequential-decision requirement. All affected consumers provide attributable input; missing input blocks. A present rejection is not an individual veto; overriding it requires the separately approved application policy and does not erase ownership/access rights. Do not count model answers as consensus.

BA-05. A worker chooses steps only within a valid authorization. Prompts, recalled text, role names, sender strings, local loopback, a gateway token, a supervisor, a lease or a majority of LLM opinions cannot mint authority. The actual tool/actuator checks scope, action/parameter binding, validity, applicable revocation and fencing before effects.

BA-06. Beta model/tool execution and closed HTTP business routes stay denied until their existing gates are satisfied. An opt-in lab cannot call the legacy-open registry to evade those gates. ATHENA remains an independent platform, not a controller or dependency.

## 3. First task class and explicit resource boundary

Proposed class: `public-fixture-summary/v1`.
Goal: inspect an immutable approved manifest of public/sample UTF-8 text documents; produce a structured summary with resolvable source spans and identified conflicting claims; save a report in one newly created task output location.

This is **read-only input plus a narrowly authorized output write**, not a wholly read-only task. Publishing the report is a bounded side effect subject to the output grant and recovery rules. No access to arbitrary user directories, home directories, repository credentials, private conversations or runtime databases is granted by this document.

Proposed worker tools, NOT current shipped tool names:
- `fixture_list()` -> manifest entries with opaque document IDs, byte sizes and content digests.
- `fixture_read(document_id, start_line, end_line)` -> bounded text and a provenance receipt tied to the pinned manifest/digest.

The worker submits a structured candidate, never an absolute output path or authority fields. A separately scoped runtime staging operation stores that immutable candidate for checking. `report_publish(report)` is a proposed **internal post-check actuator**, not an advertised worker tool: it accepts only the exact checked artifact digest plus the applicable verified output authorization, audit intent and current fence. Calls made before checking/final authorization are denied with zero publication. Candidate staging and final publication are both bounded task-owned writes and must be authorized/recoverable; a checker receipt is not itself a resource grant.

No shell, arbitrary Python, package installation, browser, bridge, memory query/write, web search/fetch, credential access, agent creation, policy change or self-modifying code is available in this class. Use public fixture content only. Recalled conversation is optional authorized reference data, not required to demonstrate the first task; do not enable legacy memory tools to add continuity.

Tool network egress is denied. A real model is a separate explicitly approved provider boundary: either a reviewed local model or one named external endpoint/model with approved data disclosure, metering and egress. "No network" must not silently conceal a hosted model call. Deterministic/scripted offline providers test control flow but do not prove model-directed autonomy.

Provisional LAB ceilings for review (not measured capacity and not enabled defaults): 8 model attempts total per task across all participants, including checking/routing/retries/forced finals; 12 tool invocations; 5-minute task deadline; one task at a time; 8 documents, 64 KiB per document, 256 KiB total input; 32 KiB final artifact; at most one feedback-driven revision. Budgets survive restarts; no child budget multiplies the parent total. Transport retransmission has a separate bounded delivery budget. Component contracts must define UTF-8 byte measurement and validate these limits at runtime. Existing stricter beta limits still apply wherever beta ingress is used.

## 4. Trust boundaries and proposed records

All records below are proposed logical schemas, not current Python classes or approved peer wire payloads. Unknown fields, booleans used as integers, oversized data and unsupported versions must fail closed. Identifiers/digests retain their declared formats; do not normalize malformed signed source tokens into valid ones.

### TaskContract
- `schema_version`: exact supported version.
- `task_id`, `request_id`, `ingress_receipt_ref`: stable IDs; requester-scoped deduplication.
- `owner_ref`, `requester_proof_ref`: authenticated principal and independently checkable human-input evidence where required; not user-provided sender strings.
- `goal_ref`, `acceptance_definition_ref`, `task_class_ref`: immutable versioned content references/digests.
- `input_manifest_ref`, `authorized_context_refs`: pinned source/context provenance and resource scopes.
- `policy_epoch_ref`, `membership_ref`, `affected_snapshot_ref`, `authorization_ref`: independently verifiable applicable authority; no gateway-issued universal grant.
- `limits_ref`, `provider_binding_ref`, `deadline`: pinned constraints, approved provider/model and deadline semantics.
- `output_resource_ref`: sole task-owned artifact namespace and allowed operation.

### Assignment
- `assignment_id`, `task_id`, `revision`, `worker_peer_id`: unique assignment and authenticated recipient.
- `objective_ref`, `acceptance_ref`, `input_refs`, `dependency_result_refs`: actual subtask work, not a repeated whole-goal prompt.
- `authorization_ref`, `limits_reservation_ref`, `policy_epoch_ref`: scoped permission and allocated shared budget.
- `generation`, `coordinator_epoch_ref`, `deadline`: stale-assignment protection. Local ownership is not grant authority.
- Completion depends on declared prerequisites. Unknown, failed, foreign-owner or unvalidated dependency outputs cannot satisfy readiness.

### OperationIntent and Observation
- `operation_id`, `assignment_id`, `step_sequence`, `generation`, `tool_version_ref`.
- `action_digest`, `input_digest`, `authorization_ref`, `budget_reservation_ref`, `target_resource_ref`.
- `intent_state`, `outcome_state`, `receipt_ref`, `output_ref`, `error_code`, `record_version`.
- Replay class is trusted tool-registration metadata, never a model/caller `safe_to_retry` flag. A timeout does not prove non-execution.
- Uncertain provider calls remain accounted for and blocking under the applicable audit policy. Do not automatically clear existing MeteredModel uncertainty or refund attempts.

### CheckReport, ResultEnvelope and DeliveryRecord
- CheckReport: `report_id`, checker identity, checker version, task/assignment and exact artifact digest, acceptance-definition digest, per-criterion verdict/evidence references, missing checks and aggregate verdict.
- Check verdicts: `NOT_PERFORMED`, `PASSED`, `FAILED`, `NEEDS_REVIEW`. A performed structural check is not semantic accuracy, safety approval or peer authority. Risk is `NOT_ASSESSED` unless an actual named assessment with provenance exists; never infer "none" from no check.
- ResultEnvelope: task/revision, actual execution state, artifact references/digests, check-report references, audit-completion reference, unresolved operations, redacted reason codes and policy binding. Completion and verification are distinct facts.
- DeliveryRecord: result ID/digest, owner-scoped destination binding, outbox sequence, delivery attempt count and transport receipt. `PENDING` / `TRANSPORT_ACCEPTED` / `INDETERMINATE` describe sending, not whether a human read or accepted the result.
- Cancellation is separately authenticated and task-scoped. It cannot revoke other owners' tasks or pretend an already dispatched effect was undone.

### Required internal operations (proposed, not existing APIs)

`submit_authenticated_task(contract, ingress_receipt)` returns admitted/duplicate/blocked only after durable admission. `assign(task_id, recipient, dependency_refs, authorization_ref)` records a policy-valid assignment, not a self-issued grant. `claim_local_assignment(id, expected_revision)` establishes exclusive local process ownership. `record_intent(...)` precedes dispatch; `record_observation(...)` acknowledges an actual outcome. `check_candidate(...)` consumes an immutable artifact; `finalize_result(...)` requires all completion gates; `enqueue_result(...)` commits with task finalization. `inspect_task(authenticated_reader, task_id)` returns only authorized redacted state. `request_cancel(...)` persists cancellation intent; `reconcile_task(...)` never dispatches uncertain effects automatically.

Do not expose new HTTP endpoints or a new CLI mutator in this milestone specification. Freeze exact Python signatures, SQL migrations, error enums, canonical peer message schemas and version negotiation in dependency-first component specs before implementation. Reuse `peer_envelope.py` canonical/signature primitives where compatible; they do not validate these payloads or provide transport/membership/grants.

## 5. Runtime loop and state machine

Worker loop: inspect authorized context and prior committed observations -> propose next step -> validate current permission/budget/deadline/cancellation -> persist reservation and intent -> execute actual tool -> persist observation -> evaluate progress -> continue, submit candidate or stop. No tool dispatch or model call before its applicable durable reservation and intent. A provider's final-answer string is a candidate, not completion.

Logical task states:
- `ADMITTED`: ingress/dedup/contract durably recorded; not yet assigned.
- `READY`: policy-valid assignment and inputs available.
- `RUNNING`: claimed by a live local owner; current generation established.
- `WAITING_PROVIDER`: only a known retryable pre-dispatch failure or safely classified failure; bounded backoff/deadline. Unknown post-dispatch effects instead become `INDETERMINATE`.
- `CHECKING`: immutable candidate awaiting required independent checks.
- `BLOCKED`: a known prerequisite is missing, with a redacted reason and an explicit policy-valid unblocking transition.
- `INDETERMINATE`: effect or durability is uncertain; no success and no blind retry.
- `COMPLETED`: nonempty required assignments completed, acceptance checks passed, artifact verified, audit and final-result/outbox committed.
- `FAILED`, `CANCELLED`, `DEADLINE_EXCEEDED`: terminal known outcomes. If dispatch was in flight and unresolved, preserve `INDETERMINATE` plus cancellation/deadline intent rather than asserting no effect.

Allowed progress: ADMITTED -> READY -> RUNNING -> CHECKING -> COMPLETED. CHECKING may return to READY for the single permitted revision with remaining budget. Active states may enter BLOCKED, INDETERMINATE or a justified terminal failure. WAITING_PROVIDER may return to READY within remaining limits. BLOCKED requires recorded prerequisite satisfaction and revalidation before READY. INDETERMINATE requires a durable reconciliation decision based on target evidence, not elapsed time or a model guess. Terminal records are immutable; subsequent work gets an explicitly related new task/revision, never an implicit state reset.

The final component state-transition table must enumerate every permitted edge and actor, compare expected versions atomically and reject all others. Existing TaskStatus/ExecutionStatus are not silently extended by this document.

## 6. Durability, ownership and effects

Reuse existing recoverable workflow APIs for trusted deterministic replay-safe operations where their contracts fit. Their OS-held exclusion lock must not be stolen by a clock lease. Do not wrap a dynamic model/provider call in a callback and mark it replay-safe to obtain automatic recovery.

A peer-local task journal is needed for dynamic decisions/observations; extending existing storage versus adding bounded tables requires a migration/reuse decision in the storage slice. It is not a second global task authority. Proposed logical persisted sets: tasks/contracts, assignments/dependencies, operations/intents/outcomes, shared-budget reservations, checker reports, immutable results, inbox dedup and ordered outbox. Require uniqueness of requester+request ID, assignment revision, operation ID and result revision. State change plus dedup/outbox insertion must be one database transaction, not best-effort writes across independent stores.

Use file-backed SQLite with verified WAL/FULL settings for acknowledged local state, bounded transactions/busy waits and explicit schema versions; refuse in-memory task journals. SQLite documents weaker power-loss durability for WAL/NORMAL than FULL; storage hardware/OS behavior and host compromise remain outside an application test's proof.[5]

A database transaction cannot atomically encompass arbitrary filesystem/provider/Telegram effects. Output publication needs a reviewed staging + flush + content-digest + immutable publish + reconciliation protocol, task-scoped path ownership and target-side stale-generation rejection. Repeating an identical authorized operation may return the same receipt; conflicting bytes under the same operation ID must fail. Do not overwrite existing unrelated outputs. Resolve actual file handles under the permitted root and reject traversal, symlink/junction/reparse escapes and check/open races; path-string prefix checks are insufficient. Exact cross-platform actuator implementation is a later gate.

Persist outgoing exact message bytes/identity sequence before send, then retry the same record. Use durable inbox dedup and local state transitions atomically. The transactional-outbox pattern addresses state/message dual writes and still requires consumers to handle duplicates; it is not proof of exactly-once external effects.[3]

If audit completion fails after an artifact/model call succeeded, expose uncertainty, suspend affected work and reconcile; never send a success or write success memory. Recovery must not erase pending records or lower authorization thresholds. Backups include replay/signing state; restore cannot reuse signing sequences or revive revoked assignments. Process-kill/reopen evidence is not a power-cut or rollback-resilience test.

## 7. Useful checking, not verification theatre

The first fixture corpus should contain explicit identifiable claim records: subject, attribute, value, document/span and version. Include a known pair of contradictory claims and unrelated distractors. A deterministic checker can validate exact source digests/spans, claim provenance, required corpus coverage, expected conflict pairs and artifact schema against a versioned test oracle.

A separate checker peer consumes worker artifacts without sharing the worker's mutable task memory. It cannot authorize publication by its own opinion alone; policy governs its role and the actuator still validates the applicable grant. A missing/unavailable checker blocks completion. Adversarial cases include forged citations, a copied success label, omitted documents, a nonexistent conflict, a changed candidate after checking and a worker signing its own checker identity.

These fixture checks prove the declared bounded criteria, not universal factual correctness or discovery of every natural-language contradiction. Human review of a real-model sample remains an acceptance gate. Do not label the existing heuristic AgentVerifier or majority model judges as independent security/peer approval.

## 8. Authority decisions still open

D-01: validator set, fault count, application approval rule and adoption/version of the consensus engine. The existing four-process CometBFT 0.38.26 KV lab is evidence for an engine experiment, not approval to use that configuration for Agency authority.

D-02: membership genesis/admission/removal, rotation/revocation, cross-epoch conflict handling and replaceable coordination. No central membership administrator silently grants itself voters.

D-03: authenticated affected-set derivation/completeness, attributable consumer input, override rule and independently verifiable human consent/enrollment/recovery. Gateway signatures alone cannot satisfy the selected compromised-gateway threat model.

D-04: exact resource/tool grant format, finality proof verification, expiry/revocation behavior and target-side idempotency/fencing. Proposed standing task-class authorization must be explicitly approved, revocable and independently verifiable; it is not a bypass for D-01 through D-03.

D-05: approved fixture/output resources, numeric limits, model/provider/data policy and evidence retention. The Telegram-bound MeteredBinding cannot be reused by fabricating a Telegram principal for a local request; a platform-neutral request-metering adaptation needs a reviewed compatibility contract. Free CLI workers used to BUILD the runtime are distinct from a model used BY the runtime.

The deterministic application state machine must validate ordered commands on every correct peer. CometBFT's versioned ABCI application requirements require deterministic proposal processing; nondeterministic model/tool execution must stay outside consensus transitions, with validated receipts as inputs.[4]

A1 maintenance can proceed after its own approval without deciding D-01 through D-05. The real peer-authorized demonstration cannot. No requirement is removed merely because a simpler centralized demo would pass.

## 9. Acceptance scenarios and claim boundaries

Each scenario must record exact revision, configured limits/identities/policy version, actual observations, artifact digests, child cleanup and outcome. **Not run in this documentation checkpoint.**

AC-01 Useful goal: authenticated request -> Butler -> Lattice assignment -> actual worker tool choices/observations -> independent checks -> immutable report -> durable result -> Butler.
AC-02 Dynamic choice: varied/reordered fixtures produce observation-dependent actions rather than a canned final answer or a hardcoded sequence falsely described as agent reasoning.
AC-03 Context: correctly scoped earlier context reaches permitted paths; arbitrary caller memory/identity cannot become authority; isolated browser chat stays memory/tool-free.
AC-04 Multi-step progress: completed observations feed later choices; specialist assignments, if used, contain distinct objectives and authorized dependency outputs. One worker plus checker suffices initially; no new domain roster is required.
AC-05 Checks: corrupted/missing/foreign citations, wrong conflict pairs, missing required coverage and post-check artifact changes prevent completion; publication before independent checking and applicable final authorization causes zero publication.
AC-06 Honest status: no check -> NOT_PERFORMED; no risk assessment -> NOT_ASSESSED; provider/tool execution alone does not become verified task success.
AC-07 Interruption: kill the actual worker at intent, post-effect/pre-outcome, post-outcome, candidate, finalization and delivery boundaries; restart retains acknowledged steps and exposes uncertain outcomes.
AC-08 Duplicate request/message: one durable task/assignment/effect receipt; conflict under the same ID rejected; message retries do not consume a new execution budget or rerun completed work.
AC-09 Stale worker: a paused worker returning after replacement cannot publish effects with an old generation. Local lease expiry alone is not the proof.
AC-10 Audit/storage failure: pre-intent failure causes zero dispatch; post-effect/finalization failure causes no success/delivery/memory claim; store reopen retains blocking uncertainty.
AC-11 Budget: every model attempt across routing, workers, checker, parse repair, retries and forced final is reserved before dispatch; exhaustion/restart/clock rollback does not expand limits; provider drift rejected.
AC-12 Scope: out-of-manifest reads, oversized content, output traversal/reparse escapes, unapproved tool/provider or grant substitution cause zero protected effects.
AC-13 Peer boundaries: independent processes/stores/identities; forged/replayed/stale-epoch/unauthenticated messages and worker-as-checker impersonation rejected.
AC-14 Missing affected input, invalid finality, revoked authority or insufficient quorum: zero protected effects; no Butler/"user" override.
AC-15 Butler offline: already admitted work may complete under existing authority, result remains durable and is delivered after gateway recovery; missing new human input blocks honestly.
AC-16 Coordinator loss/partition: replace coordination only under policy, preserve assignment conflict safety, no unauthorized split-brain effects; heal and verify convergence. Test within approved fault assumptions, not invented quorum counts.
AC-17 Cancellation/deadline: authenticated task-scoped cancellation, bounded shutdown and retained in-flight uncertainty; no provider/tool calls after denial and no claim that remote effects were undone.
AC-18 Isolation/privacy: two principals cannot read each other's task/context/result; audit/log/error/export contains no prompts, raw private content, credentials or unrestricted source paths; failure paths included.
AC-19 Real model: one owner-authorized bounded actual-model run produces a reviewed artifact; synthetic/echo runs do not satisfy this gate. No hidden paid fallback or account/region/quota bypass.
AC-20 Delivery: durable result survives gateway restart; transport uncertainty remains distinguishable from execution completion and human acknowledgement; disclose possible duplicate replies.
AC-21 Restore: controlled restore preserves identity/signing/replay/budget/policy state or safely quarantines it; backups alone are not proof.
AC-22 Quality: focused RED/GREEN regressions, full exact-revision offline suite, Ruff lint/format, strict mypy, UI checks, supported-platform CI and independent security review. Historical totals are not fresh results.
AC-23 Operations: supported launcher, resource isolation, backup/rollback and separate owner rollout approval before live canary. No healthy bot stopped during development.
AC-24 Evidence: preserve failed and passing runs, exact harnesses and source hashes outside temporary cache; unresolved/flaky/unrun scenarios remain explicit.

Milestone completion requires applicable AC-01 through AC-24, with any truly inapplicable scenario justified by an approved narrowed contract, never silently skipped. A1 passing is only A1 closure, not this milestone.

## 10. Dependency-first delivery and exclusive ownership

A1: [Context propagation maintenance](SPEC_AUTONOMY_CONTEXT_REPAIR_A1.md), one writer for `orchestrator.py`, focused tests, preserve all existing denial/isolation boundaries. Await approval before edits.

A2: truthful externally observable result/check contract (R-03). Freeze result schema and caller handling before coding; changing an unreturned local dictionary's literals alone is insufficient. Preserve classic heuristic limitations explicitly. Serialize `orchestrator.py` ownership after A1.

A3: admission/principal provenance, complete provider-dispatch map, durable shared budget and audit/result failure propagation. Include Butler routing and optional model/checker paths. No beta enablement until positive and negative end-to-end cases pass. Resolve D-05; do not introduce arbitrary model-object bypasses.

A4: peer application/transport/membership/affected-input/grant contracts and independent process integration, reusing the existing lab/envelope work without claiming it already provides authority. Resolve D-01 through D-04 before real actuation.

A5: durable task/assignment/journal/outbox and recovery bridge; keep the existing workflow OS lock invariant, revalidate permissions after restart and make unsafe unknown effects non-replayable. Freeze schema/migration and artifact publish/reconciliation protocol first. Documentation/test research can parallel A4; shared consumer edits wait for stable contracts.

A6: integrate proposed fixture tools, worker loop and checker with actual authorized identities and resource enforcement. Use actual system entrypoints, no manual database injection or final-result fabrication. Lattice coordination remains replaceable; Butler is only gateway.

A7: independent process/fault/adversarial harness, exact-revision full qualification and owner-authorized real-model demonstration. Deployment remains separate.

Implementation briefs must name exact base revision, owned files, contracts, stop gates, hermetic checks and report-back evidence. One writer per shared integration file. Bulk CLI work uses only a freshly verified eligible free lane; the handoff's auth/quota/terms blockers are historical observations and must be rechecked before launch. No workers are launched by this specification; no automatic paid/subscription fallback.

## 11. Alternatives considered

- Add an autonomous Butler boss: rejected, violates the product's authority boundary.
- New agent framework/rewrite: unnecessary for the first slice; reuse ToolDriver, adapter, storage and recovery foundations.
- Fixed scripted workflow only: useful for deterministic substeps/recovery but insufficient evidence of model-directed autonomy. Anthropic distinguishes predefined workflows from model-directed agents and recommends increasing complexity only when useful.[2]
- Shared global authoritative SQLite queue: convenient locally, rejected as decentralized authority; local durable stores are allowed.
- Every model turn as a new BFT decision: avoid unless approved policy specifically requires it. Recommend a bounded standing task-class grant; deviations still block or require policy review. This is a proposal, not existing authorization.
- Central orchestration copied from Hermes: copy the bounded observe/act/continue behavior, not central authority. Hermes documents a model/tool feedback loop with persistent session state and iteration tracking.[1]
- Build all domains, Factory or Forge now: deferred until one complete task path is proven.

## 12. Evidence and next approval

The baseline diagnostic reproduced missing classic recall and the tool path's unearned passed/none labels using synthetic inputs and a legacy-path model double. It also observed recalled-context exclusion from isolated chat and beta denial before model calls. It is not a real-provider, peer-authorization, independent-QA or autonomy acceptance run. The first diagnostic attempt failed during Windows event-loop self-pipe creation because the network guard was installed too early; both attempts are retained. The successful guard was installed inside the already-created event loop before Agency startup. It is Python-level denial, not an OS sandbox.

Only documents/research evidence are changed at this checkpoint. Approval requested next: implement A1 under its separate bounded contract. Full autonomy remains gated by the later contracts and owner decisions above. No runtime quality-gate totals are newly asserted here; the handoff remains the record of the prior full qualification.

## Sources

[1] https://hermes-agent.nousresearch.com/docs/developer-guide/agent-loop
[2] https://www.anthropic.com/engineering/building-effective-agents
[3] https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/transactional-outbox.html
[4] https://raw.githubusercontent.com/cometbft/cometbft/v0.38.26/spec/abci/abci%2B%2B_app_requirements.md
[5] https://www.sqlite.org/pragma.html
