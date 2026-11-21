# Agency status and resumable handoff — October 18, 2026

**Verdict: substantial pre-production prototype; not daily-driver-qualified and not a complete decentralized Agency.** This is a status/preservation checkpoint, not new runtime implementation, merge approval or deployment. GitHub issue **#11** is the active completion tracker.

## 1. What The Agency is

The Agency is a security-first personal multi-agent system. You speak to **Butler**; the **Lattice** connects peers, memory, messaging, coordination and orchestration. Specialized peers should do work under independently authenticated policy, with affected-consumer input and independently checked execution grants. Butler carries requests, context, confirmations and results; it is not the boss, a validator or a unilateral permission source.

Target: useful multi-step work, reusable versioned capabilities, durable progress/recovery, honest checking and permitted actions. Agent Factory manages capability/agent lifecycle; Forge is the embedded software-engineering department. These targets are broader than today's implemented features. The Agency and ATHENA are independent platforms.

Separate processes on one machine are a valid first decentralized milestone. One OS/administrator is still a shared compromise and availability domain. Central SQLite/in-process voting is not decentralized consensus.

## 2. What is actually built

- **Conversation foundations:** private Telegram commands, per-user naming, persisted owner-scoped workspaces/threads/messages, active-thread continuation and bounded recall.
- **Local browser prototype:** guarded FastAPI-served HTML/JS with model-only isolated chat; not a fully authenticated multiuser desktop/product UI.
- **Runtime foundations:** general/research/demo execution, routing/planning, model adapters, tool registry/driver, scoped persistent memory and FTS retrieval. Seeded agent names are not evaluated specialist implementations.
- **Safety primitives:** request admission/budget/replay stores, strict metered-model boundary, durable audit intent/outcome/uncertainty, denied beta model/tool paths and closed authority-bearing HTTP business routes.
- **Recovery/capability foundations:** versioned skills/workflows, constrained deterministic workflow execution, file-backed recoverable workflow APIs, Factory blueprints/approval/activation/revocation metadata and Forge hash/syntax inspection. These do not provide arbitrary autonomous-task recovery, Factory restart rehydration or Forge coding/review/release.
- **Peer groundwork:** signed envelopes and a historically exercised four-process consensus-engine key/value lab. Neither is an Agency peer application authority chain.
- **Completed later development slices:** callback/lifecycle/resource-lease repairs; A1 quoted recalled context propagation across routed execution while preserving isolated-chat exclusion; A2 immutable byte-bound execution/check reports that do not present execution as independent verification; A3.1 exact gateway audit acknowledgement, fail-closed success/conversation finalization and fixed sanitized consumer failures.

A2 reports are diagnostic snapshots, not signed proof or permission. A3.1 does not roll back earlier provider calls, memory/evidence writes or command mutations after a late failure. Cross-store atomicity and reconciliation remain open.

## 3. How far from daily use versus full completion

### Restricted supervised personal assistant

Closer than the full autonomous product, but **not yet a qualified daily driver**. Existing chat/research foundations can be experimented with under supervision; that is not permission to trust unattended work or enable public beta. No Agency Python/Node runtime process was found in the read-only process inspection during this audit. No live provider or Telegram reply was exercised.

Required gates: fresh reliable qualification and exact-SHA CI; supported launcher qualification; complete identity/provider/budget/audit chain; principal-bound memory/tool resources and privacy; constrained operations; exercised backup/recovery/rollback and one-poller supervision; independent review; separately authorized provider/Telegram canary. Narrowing scope can exclude unsafe fetch, hostile code, consequential actions and multiuser access, but cannot waive the gates applicable to what remains.

### Bounded autonomous Agency

A1 and A2 are implemented; A3.1 is implemented, but the rest of A3 remains incomplete. A4 peer authority, A5 durable task/recovery/outbox/fencing, A6 actual useful independently checked fixture task and A7 fault/adversarial/real-model qualification remain open. The 24 bounded-autonomy acceptance scenarios are not signed off as complete. See `AUTONOMY_READINESS_20261016.md`.

### Full product vision

Beyond bounded autonomy: real specialist/DAG work, evaluated domain capabilities, authenticated publication, Factory restart/lifecycle, permitted runtime binding of skills/workflows, Forge engineering/review/release and complete operations/security/privacy productization. There is no defensible overall percentage or calendar completion date: important interfaces/policies remain undecided and the first complete peer-authorized useful task has not been demonstrated.

## 4. Finished work and branch accounting

Initial inventory: five existing worktrees were clean. A sixth isolated worktree/branch, `docs/agency-status-20261018`, was created for this audit; canonical main and runtime data were not modified.

- Remote main at audit: `3417c79bf2056ab414f49b3c0281b5c723377493`.
- Local main: `713a4e311b1e071fbd6c5cf103b995d17aa7e5a2`, 27 commits ahead of remote main.
- Lattice foundation handoff: `1c7e7da2774ee4687741a0e346cbf2d593a9c845`, already published on its existing branch.
- A1 handoff: `c958b874e24938e2a3713963d4d5aa73f21a97ba`.
- A2 handoff: `d9377606de25e7dadbcf85d51480026022068085`.
- Latest A3.1 handoff/base of this checkpoint: `6b1d4c4347ad51e48a3ef478f0e2a2f6f5bcfbbf`, another 32 commits after local main.

All 15 local branches, including the new checkpoint branch, were programmatically checked against that latest checkpoint by ancestry and `git cherry` patch equivalence. There were **zero unrepresented non-equivalent branch patches**. Historical repair-worker branches need not be merged or pushed individually to preserve completed code. Original refs/worktrees were retained; no history rewrite, branch deletion, main merge or deployment occurred. Six pre-existing Dependabot PRs remain separate unreviewed dependency work.

Publishing this checkpoint preserves the completed development ancestry; it does not upgrade experimental/planned features to completed product claims. The first/current full-suite failure below prevents a green-build or release claim.

## 5. Fresh verification — do not substitute historical totals

Exact tested base: `6b1d4c4347ad51e48a3ef478f0e2a2f6f5bcfbbf`.

- Source tree: `5e24627025f00fe6a5e775e297f80c3fc1175ed1`.
- Tests tree: `713bfd75b939b913113d3b4cd1c424e87497022f`.
- Ruff lint **passed**.
- Ruff format **passed**, 246 files already formatted.
- Strict mypy: initial 180-second timeout retained; a separately recorded repeat **passed on 151 source files**.
- Node UI: **16 passed**.
- Fresh cold imports of `agency.agents.assessment` and `telegram_bot`: **passed**. This audit did not repeat the other three historical cold-import checks.
- Initial full pytest: partial progress included a failure marker, then the outer tool timed out at 420 seconds. No final JUnit or trustworthy full-suite total exists for that run.
- First diagnostic invocation accidentally omitted the explicit tests target: exit 5, zero collected. It is harness error, not a product test or pass.
- Corrected diagnostic: **1,870 collected; stopped at first failure after 14 passed / 1 failed**, one Starlette/httpx deprecation warning. `tests/test_admission_worker_protocol.py::test_parent_rejects_real_split_claim_two_ran_mutant` expected `two tools ran before release` but received `bootstrap deadline expired; pids=[15472, 3980]`. Root cause is not established; do not automatically extend the deadline, suppress the test or retry until green. The earlier full run's failure marker is not conclusively the same failure.

Historical A3.1 evidence records 1,870 Python/16 Node passes at identical source/test Git trees on October 16. That remains genuine historical evidence, **not a fresh passing suite, current CI, live readiness or elimination of flakiness**. New documentation commits may reference these checks only with unchanged source/tests.

Tests used the existing verified Python 3.11.16 toolchain, a clean isolated checkout without dotenv, scratch CWD/data/temp, stripped provider credentials, explicit echo provider/model and the preserved Python external-DNS/socket guard. The guard permits loopback and is not OS/native-child containment; Node tests are outside that Python guard. Source/test fingerprints were unchanged during completed checks. Canonical private data was not used for testing.

Evidence: `research-evidence/status-audit-20261018.json`. Raw failed/passing logs, JUnit, receipts and harnesses: `C:/Users/alphi/agency-evidence/status-audit-20261018/`. Independent read-only review and remote CI are separate gates, not inferred from these tests.

## 6. Pick up here — ordered unfinished work

GitHub **#11** is the checkbox tracker. Preserve this order:

1. **Qualification blocker first:** diagnose the admission bootstrap deadline failure with retained child/phase diagnostics; account for the incomplete earlier full run. Reproduce without weakening race assertions. Resolve qualification reliability before claiming source is green.
2. **Reconcile status/operations:** current launcher pins are for the older baseline, not the A3 source; qualification and any repin require an explicit separate checked scope. Reconcile old warning status labels with accepted lifecycle/collection repairs. Historical failure/stability and test-client dependency work remain open.
3. **A3.2 specification checkpoint:** freeze effective provider/model/endpoint/credential/request-limit binding, authenticated platform-neutral admission, durable shared attempts and all consumer paths. Review/commit the contract and obtain implementation approval. Do not fabricate a Telegram principal for local requests. A subscription-backed build assistant does not authorize any runtime provider.
4. **P1/R-04 privacy/safety:** bind memory/tool resources to the authenticated principal, redact legacy logs/errors and retain fail-closed metering/audit at all routes. Beta model/tool paths remain disabled. Keep unsafe fetch/hostile-code execution out of the pilot.
5. **Owner decisions D-01–D-05:** approve validator/fault/application policy; membership/revocation/recovery; authenticated affected-input/override/human consent; exact actuator grants/fencing; permitted fixture/output/provider/data/cost/limit/retention policy. Existing Byzantine-tolerance direction stands; exact policy is not invented here.
6. **A4–A7:** implement the approved peer application chain, durable task/outbox/recovery, useful fixture task and independent checker/publication; qualify actual process/fault/adversarial/restore cases and an authorized real-model example. No central gateway override.
7. **Remaining product/operations:** real specialist/DAG dispatch; evaluated capabilities, Factory rehydration and runtime skills/workflows; Forge expansion in approved slices; deployment packaging, target-OS containment, backup/restore/rollback, monitoring, historical database/secret exposure audit and independent canary signoff. Containers need repair only if selected for the supported pilot scope; current Docker/Compose files must not be shipped unchanged.

Do not close a tracker item from a class name, historical test total, health endpoint, successful push or model response alone. No runtime implementation or rollout was authorized by this status request.
