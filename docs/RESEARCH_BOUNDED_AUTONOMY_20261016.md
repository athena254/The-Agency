# Bounded autonomy — source audit and design research

Date: October 16, 2026.
Scope: selected request-to-execution, context, metering, audit, recovery and authority paths at `1c7e7da2774ee4687741a0e346cbf2d593a9c845`. **Not a repository-wide security audit, live-provider test or proof of autonomy.**

## 1. Baseline and actual diagnostic

A clean, persistent isolated worktree was created at `C:/Users/alphi/agency-worktrees/bounded-autonomy-20261016` on `docs/bounded-autonomy-20261016`, from the handoff. Canonical main was `713a4e311b1e071fbd6c5cf103b995d17aa7e5a2` and was not switched. Git comparison confirmed the handoff's source/tests equal `ba9ec76`; this carries forward the handoff's qualification boundary, not a fresh full-suite run.

The existing Python 3.11.16 project interpreter was discovered and reused read-only. The diagnostic explicitly imported this worktree's source via absolute `sys.path`, used synthetic inputs and an injected legacy-path model double, set echo environment configuration and put all working data in scratch. No actual model provider was called and no arbitrary subprocess tool was executed.

Observed in the successful diagnostic:
- Classic execution reported completed, but its captured model prompt did not receive `SYNTHETIC_RECALL_MARKER_BA01`.
- General/tool execution received the marker and reported completed. Profiling the actual function's returned frame captured its internal tool record as `verification="passed"`, `risk_category="none"` without invoking those checks on that branch.
- The isolated execution path did not receive the marker, even when explicitly supplied in context.
- A beta-principal execution request remained denied with zero additional model calls.

These observations reproduce R-01/R-03; they do not repair them or establish accurate output, independent verification, tenant safety or a live task lifecycle. The diagnostic uses a model test double only on the legacy nonbeta path; the strict MeteredModel boundary is not bypassed or weakened.

The first diagnostic attempt failed before Agency startup: installing a blanket socket-connect guard before `asyncio.run` blocked Windows' loopback socketpair used to construct the event loop. The corrected harness installs the guard inside the created loop before Agency startup. Its second attempt exited 0. Both failed and successful logs, the exact final harness and a hash manifest are preserved outside the temporary cache. The initial failure is a harness failure, not an Agency repair or a suppressed passing run. Python-level denial is not an OS sandbox.

Machine result: [bounded-autonomy-baseline-20261016.json](research-evidence/bounded-autonomy-baseline-20261016.json). Archive/source inventory: [bounded-autonomy-audit-manifest-20261016.json](research-evidence/bounded-autonomy-audit-manifest-20261016.json). No pytest, lint, mypy, UI, CI, launcher, peer engine or live-provider qualification was run for a new runtime revision in this documentation-only checkpoint.

## 2. Source-backed gap map

All line numbers below refer to the handoff revision, not to canonical main. Hashes in the manifest permit checking freshness. Existing tests cited below were read as evidence of coverage intent; they were not rerun in this pass.

G-01 — Context: `src/agency/butler/service.py:296-336` drops caller recall and assembles thread/sender context; `orchestrator.py:580-587` adds it only to the tool prompt. Classic dispatch at `615-621` sends the unaugmented system context. `tests/test_memory_continuity.py:147-162` covers tool injection only. **First bounded repair: A1.**

G-02 — Isolation trap: `_system_context` is shared with `converse_isolated` (`orchestrator.py:473-505,714-744`). Adding memory to that helper risks the memory-free UI boundary. Repair only the routed task prompts; keep isolated calls memory/tool-free. Butler's isolated API at `service.py:389-462` preserves status rather than returning unqualified success text.

G-03 — Truthfulness: `orchestrator.py:596-605` hardcodes tool check/risk labels. Final task status at `675-678` depends only on execution status; the intermediate dictionary is local, not the returned public result. `ExecutionResult` at `agents/executor.py:66-77` has no check-report field. **A2 must define an observable report and its consumers, not claim success from changing unused literals.**

G-04 — Completion/accuracy: `AgentVerifier.verify` at `agents/verifier.py:135-177` runs an accuracy check only for dictionary results containing ground truth; naming "accuracy" in requested criteria does not guarantee it ran. Classic heuristics and optional model judges are not independent security/peer authority. The `all(...)` completion calculation also requires a separate nonempty-plan contract to avoid treating absence of work as proof of completion; this edge was source-inspected, not runtime-reproduced here.

G-05 — Real assignments: `agents/planner.py:77-117` defines assigned agent IDs, dependencies and readiness. Classic `orchestrator.py:615-621` instead dispatches the whole task and original agent ID each time. Freeze specialist/authorized dependency schemas before integrating those assignments. Repeating the original prompt is not delegation.

G-06 — Existing loop: `tools/driver.py:181-261,311-383` builds prompts, observes actual tool results and supports forced final/parse recovery. Its `history`, `steps`, `evidence` and counters are local variables. Reuse the loop, add durable operation/task boundaries through approved contracts; do not rename its in-memory history as crash recovery. A model final is accepted as driver completion, not checked task completion.

G-07 — Admission stays closed: `orchestrator.py:529-534`, `butler/service.py:279-282,530-533` reject beta before legacy dispatch. `api/authority.py:13-18` returns HTTP 503 because peer authority is unavailable. These are deliberate gates, not missing switches to enable for a demo.

G-08 — Metering substrate and mismatch: `llm/metered.py:24-56,127-238` binds a concrete model/audit/budget to bot ID, Telegram update/user, private BetaPrincipal and exact provider/model. It reserves attempts before dispatch and preserves uncertainty. `tools/driver.py:199-219` rejects arbitrary model objects/subclasses in strict mode. A local fixture task cannot honestly mint a dummy Telegram identity to reuse this seam. A platform-neutral adaptation needs its own compatibility contract.

G-09 — Outbound coverage: Butler routing can call a callable and separate Ollama/HTTP helpers (`butler/service.py:501-515,583-642`); executor dispatches its callable (`agents/executor.py:190-194`); ToolDriver has ordinary and forced-final calls (`tools/driver.py:240,340`); LLM router/providers add other dispatch/probe/streaming surfaces. This is a discovery map, NOT a completed all-call security audit. A3 must inventory every reachable path from enabled entrypoints and prove each reserved or disabled. Do not claim the metered internal driver protects them all.

G-10 — Task durability: TaskManager owns dictionaries/queue (`kernel/tasks.py:108-122`); AgentExecutor keeps status/running/cancellation in memory (`agents/executor.py:123-129`). Orchestrator defaults ordinary audit to memory and catches Lattice task-mirror failure (`orchestrator.py:133-144,429-454`); this is not durable task acknowledgement.

G-11 — Failure propagation/privacy: legacy Butler catches audit failure (`service.py:648-675`) and ordinary request audits include message/response excerpts (`338-345,375-385`). Orchestrator persists a task memory item under agent ID (`687-696`), rather than independently proving task owner scope. Closed beta paths must not be reopened before strict audit, scope and redaction are end-to-end.

G-12 — Reusable recovery, bounded scope: `workflows/executor.py:190-272,397-513` persists operation intent/outcome, skips acknowledged steps, requires trusted replay-safe operation registration, and checks input/actor/definition/operation bindings during resume. `workflows/recovery.py:96-145` provides OS-owned exclusion. Keep that lock rule; a lease timeout cannot steal a live lock. Dynamic model/provider calls are not automatically replay-safe callbacks.

G-13 — Audit substrate: `kernel/audit.py:123-158,353+` distinguishes ordinary audit settings from the concrete disk-backed BetaAuditLog. Strict registry documentation/implementation (`tools/registry.py:132-149,197+`) requires durable intent/outcome and blocks uncertain work. This is reusable machinery, not an integrated task journal or peer grant verifier.

G-14 — Peer substrate: `peer_envelope.py:1-6` explicitly disclaims transport, membership, durable replay, kind-specific payload schemas and grants. `SPEC_SINGLE_HOST_LATTICE_MILESTONE.md:11-16` requires application validation before a real peer-authorized task. The historical Comet lab proves selected synthetic engine scenarios, not Agency application authorization. No engine was started in this pass.

G-15 — Memory tools: the handoff and `LATE_REVIEW_DISPOSITION.md` retain R-04; `tools/builtin/memory.py` accepts argument-selected agent scope. Sender-scoped conversation recall is not permission enforcement for that tool. First task class excludes memory tools; full resource authorization/redaction remains open.

G-16 — Release evidence: `.github/workflows/ci.yml:3-7` runs push CI only for named integration branches and PRs targeting main/master; a feature-branch push alone is not a CI pass. Lint and formatter are separate gates, strict typecheck is separate, and the Python matrix explicitly selects echo. This checkpoint does not push or open a PR.

## 3. Primary-source research and engineering decisions

Hermes: the official loop documentation describes model calls, tool-result feedback, iteration tracking and session persistence. Reuse that behavior as the comparison target; it does not supply the Agency's decentralized authority contract. Its documented parent/child budgets should not be assumed to be a single shared aggregate budget; the proposed Agency task ceiling is explicitly aggregate.[1]

Anthropic: distinguishes predefined workflows from model-directed agents, recommends adding complexity only when warranted and grounding progress in environmental feedback. Use a dynamic worker loop around narrow deterministic enforcement/checking; a canned workflow-only demo is not proof of autonomous choices. Do not copy its central orchestrator-workers authority into Butler.[2]

AWS: transactional outbox addresses database/event dual-write failures; consumer deduplication is still necessary. Adopt peer-local transactional result/outbox and inbox/state boundaries where appropriate. Do not infer exactly-once filesystem/provider/Telegram effects from an outbox.[3]

CometBFT v0.38.26: ABCI proposal processing has deterministic application requirements. Keep model/tool work outside deterministic consensus transitions, submitting validated facts/receipts under a separately reviewed policy. Reading this pinned requirement is not selecting a production engine, membership or validator configuration.[4]

SQLite: WAL/FULL adds commit synchronization beyond WAL/NORMAL and supports stronger acknowledged local-state durability. Require verified settings and actual reopen/failure tests; process-kill tests are not power-loss, tamper resistance or multi-host resilience evidence.[5]

Alternatives: a new framework, autonomous Butler boss and globally authoritative shared DB do not solve the missing integration/authority boundaries. A deterministic workflow is useful for replay-safe substeps, while worker decisions remain bounded and observation-driven. Recommend a reviewed standing task-class authorization to avoid governance on every model token; it remains a proposal and cannot bypass missing consumer/human input or actuator checks.

## 4. Research provenance and limitations

The configured `web_extract` path returned a search-only backend error and could not extract pages. No configuration was changed to hide the failure. A read-only Python/httpx fetch retrieved the five public primary-source pages with HTTP 200, saved exact raw bytes plus extracted text and calculated SHA-256 values. No authenticated account, private page, API key or Agency model call was used for research. Extraction uses a basic HTML parser; the document text supporting the narrow claims above was read directly, including the pinned Comet and SQLite sections.

The task-specific citation ledger assigns sources 1 through 5, with short verbatim evidence checked against the fetched text. An unused sixth candidate was registered but not fetched or cited; it is not evidence. Raw public source snapshots, retrieval receipt, diagnostic harness and both diagnostic logs are retained in the private archive; repository evidence contains hashes/receipts and our analysis, not full third-party articles or private runtime data.

This is selected-path source research plus a narrow diagnostic, not a comprehensive capability extraction or an independent adversarial review. Existing handoff issues W-01, W-03, W-05, R-02 through R-06 and production/peer gates remain open; A1 cannot close them by implication. Free CLI readiness was not re-probed and no coding workers were launched. The previous authentication/403/terms findings remain handoff observations, not freshly asserted provider conditions.

## Sources

[1] https://hermes-agent.nousresearch.com/docs/developer-guide/agent-loop
[2] https://www.anthropic.com/engineering/building-effective-agents
[3] https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/transactional-outbox.html
[4] https://raw.githubusercontent.com/cometbft/cometbft/v0.38.26/spec/abci/abci%2B%2B_app_requirements.md
[5] https://www.sqlite.org/pragma.html
