# Production readiness plan

Status: **NOT PRODUCTION READY**. This is a gated engineering plan, not authorization to deploy. Owner requested October 15, 2026. Current implementation evidence is maintained in `CURRENT_STATE.md` and `research-evidence/late-review-verification.json` after integration. The 51-section source brief remains product direction; this plan does not pretend its planned features are implemented.

## 1. Define what will be released

Two distinct deliverables must never be conflated:

- **Restricted single-owner read-only pilot:** private Telegram or local UI, explicitly disclosed provider data flow, no consequential actions, no agent creation/governance mutations, no hostile code execution, no unrestricted fetch, bounded cost and storage. Useful for operational qualification, but it is NOT the decentralized Agency product or public production approval.
- **Agency production v1:** real independently authenticated peers enforce the approved task/decision contract, affected consumers provide attributable input, durable decisions control actuators, and no Butler/orchestrator can grant authority alone. A single host is an accepted first topology; one host/administrator is still a shared compromise/availability domain. The engine-only lab does not meet this product gate.

Default: qualify the restricted pilot first while building the peer application contract. Do not relabel the pilot as full production. Owner must choose intended audience, deployment host/OS, supported surfaces, data classification, permitted operations, provider privacy/cost terms and named release approver before opening access.

## 2. Baseline hazards to fix before any rollout

These findings are grounded in the checked-out code, not hypothetical framework advice:

- `Dockerfile` copies `src/agency/` but not the top-level Telegram bot; its command invokes `python -m agency.api.server` although that module defines an ASGI app rather than a module runner. Its health probe `/health` does not match the Agency API's `/v1/health`. Installer location/runtime dependency locking and actual image startup must be tested; a successful wheel build alone is insufficient.
- `docker-compose.yml` publishes Redis and PostgreSQL host ports, uses a `changeme` database-password fallback, adds services without proving the runtime uses them, and provides `AGENCY_TELEGRAM_BOT_TOKEN` while the bot reads `TELEGRAM_BOT_TOKEN`. Do not ship this file unchanged or assume all these services are necessary. Prefer one explicit process/service per boundary and only dependencies actually used.
- `scripts/run_telegram_bot.py` pins exact tested `src` and `tests` Git trees. This pass repinned the trees tested at `59e9bc9`, and `--check` passed without starting the bot. Future source changes must pass gates before re-pinning; keep beta off. Preflight success is not deployment or owner-side Telegram reply proof.
- The legacy Lattice governance keeps authoritative proposal dictionaries in one process, best-effort mirrors state, and supports a special `user` override. It is a prototype, NOT Byzantine agreement. Public HTTP business routes remain closed until a real authority chain replaces these bypasses.
- The local browser app has loopback/Host/Origin/CSRF guards and an isolated model-only chat path, not multiuser authentication or distributed authorization. Enforce the actual server bind; app headers alone are not a deployment boundary.
- `web_fetch` DNS preflight is not connection-address pinning. Keep it disabled for beta until the actual egress connection is constrained and rebinding/redirect tests pass.
- Process sandbox is not hostile-code isolation. No L2/hostile-code claim until actual backend containment is demonstrated on the deployment OS.
- Factory activation has an explicit restart limitation: durable ACTIVE metadata is not live runtime rehydration. Forge currently inspects hashes/syntax only; its PASS cannot authorize merge/release.
- Runtime databases and credentials are not source artifacts. Current Git tracking is corrected, but old commits/bundles may retain historical DB blobs. Audit historical exposure and rotate any affected secrets; removing current tracking is not history erasure.

## 3. Ordered milestones and acceptance gates

### P0 — Reproducible source and release baseline

Dependencies: consolidated reviewed source.

Work: one canonical branch/worktree, documented optional working branch, source/data separation, dependency lock validation on Python 3.11/3.12, clean-clone install, exact version/build ID, automated offline test network controls, secret/history scan, warning triage, supported launch preflight, threat model and data-flow inventory.

Acceptance: a clean clone installs and runs all offline Python tests, Node UI tests, Ruff lint/format and strict mypy; CI passes on the **exact release SHA** on Ubuntu/Windows and supported interpreters. Any known warnings have issue IDs, reproduction and disposition; do not silently disable them. Verify no tracked runtime DB/credential files, no live-provider calls from tests, and that startup refuses a dirty/unverified source tree. A staging process reports the tested revision.

### P1 — Complete request identity, budget and audit chain

Dependencies: P0; explicit pilot scope/provider approval.

Work: trusted polling -> request capability -> Butler -> every model dispatch/tool boundary -> durable request outcome. The internal metered ToolDriver wrapper is not enough: include routing/classification, executor retries/subtasks, forced finals, demo fallbacks, future router paths and cancellation. No user/model/provider context may switch the bound identity or provider. Treat audit uncertainty as denied availability, not permission. Inventory each callable public/private seam and deny unbound execution.

Acceptance: end-to-end synthetic admitted/denied private updates, two-user isolation, restart/replay, request/model ceilings, race across processes, audit/quota failures before/after dispatch, cancelled/unknown provider outcomes, redacted logs and failed task persistence. Every unauthorized/over-budget path makes zero provider/tool calls. No beta ingress flag turns on until this gate is independently reviewed and the full suite remains green. Telegram response duplicates across send/commit crash boundaries must be documented and tested; do not promise exactly-once delivery.

### P2 — Peer application authority (the core production blocker)

Dependencies: P0; approved consensus/membership/human-attestation policy.

Work: specify deterministic task/proposal/affected-input/assignment/result/grant state machine on the already exercised consensus engine. Pin peer membership/identities/epochs, signatures, durable replay prevention and revocation. Every affected consumer must contribute attributable input; missing input blocks. A negative vote is not an individual veto under the owner's selected policy, but override rules must be explicitly approved. Bind verifiable human consent to the exact request/action; Butler has no validator key or vote. Separate ordering finality from application approval and evidence quality. Actuators verify the final grant themselves; no central checker bypass.

Owner already selected Byzantine/compromised-peer tolerance. Decisions still required: the exact tolerated fault count and validator count, membership changes/key recovery, exact application approval thresholds, human attestation method, required consumer graph semantics and operator recovery powers. Do not invent defaults to bypass these choices.

Acceptance: real separate peer processes/keys/stores execute one harmless read-only task human -> Butler -> peers -> authorized worker -> durable result -> Butler. Kill Butler after admission and a replaceable coordinator; existing admitted work remains recoverable without unilateral permission. Prove invalid/replayed/stale signatures, foreign grants, missing consumers, rejected/overridden ballots under policy, dishonest peer, durable-boundary crashes, partitions/reconnect, quorum loss, restored convergence and bounded overload. Direct API/orchestrator/tool/bridge calls cannot bypass grant checks. Single-host evidence is labeled as such.

### P3 — Isolation, storage and privacy

Dependencies: P1 for pilot; P2 before consequential execution.

Work: least-privilege service accounts, no host credentials in worker sandboxes, secret rotation, authenticated storage scopes, bounded retention/deletion, SQLite backup/migration contracts, encrypted protected backups where required, durable schema migration/version checks and atomic rollback strategy. Bind network egress to validated destinations with TLS/Host correctness. Audit OS-specific filesystem/symlink/race behavior. Keep prompt/history out of operational logs by default; audit evidence may contain sensitive outputs and needs access controls.

Acceptance: real containment tests on target OS for filesystem/network/CPU/memory/process limits, timeout/cancellation cleanup, token/log redaction, cross-owner read/write denial, full disk/locked DB/corruption/partial startup cases, backup restore into a separate runtime and successful synthetic reconciliation. Record chosen recovery point/time objectives and measured restore results. A mock Docker backend is not isolation evidence.

### P4 — Product integration and capability lifecycle

Dependencies: P2/P3 for authority-bearing runtime paths.

Late source-review backlog (details in `LATE_REVIEW_DISPOSITION.md`): R-01 consistent owner-scoped conversation context in every routed domain; R-02 actual subtask-specific specialist dispatch/dependency outputs rather than repeated whole-task prompts; R-03 evidence/QA outcomes that are not hardcoded to passed and have the approved blocking semantics; R-04 principal-bound memory-tool resource scope/redacted errors before multiuser exposure; R-05 durable task/audit/evidence lifecycle and explicit memory aging; R-06 strict authenticated/evaluated capability approval/publication. P1/P3 must close the privacy/authority subsets before any pilot that could expose them; this list does not defer a tenant leak until after deployment.

Work: close AgentFactory restart/reconciliation/evaluation gaps; bind versioned published skills/workflows to actual permitted execution and evidence. Review the proposed unified capability contract before implementation. Forge expands in independently gated slices: isolated tests -> reviewer/security evidence -> bounded coding -> immutable artifacts -> authorized release. Workspace/project/authenticated UI is separate from current local chat shell. Domain agents beyond general/research/demo need actual implementations and evaluations, not seeded names.

Acceptance: each capability's exact version, permissions, dependencies and provenance is reproducible; revoked/unpublished/incompatible capabilities cannot execute; restart preserves safe lifecycle state without automatic permission escalation. A real task exercises the bound skill/workflow, its recovery and audit. Forged PASS/security reports cannot authorize release. Native coding/release actions wait for P2/P3.

### P5 — Deployable artifacts and operations

Dependencies: P0; P1/P3 for restricted pilot; P2/P4 as required for full v1.

Work: rebuild Docker/Compose from the chosen supported entrypoint, non-root user, read-only root filesystem where compatible, separate persistent data mount, no public DB/cache ports, strict required secret validation, least-privilege network, bounded resource limits and one-poller ownership. Remove unused dependencies/services rather than maintaining speculative infrastructure. Build versioned wheel/image, vulnerability scan/SBOM/provenance, immutable digest, staging smoke, release approval and retained rollback artifact. Protect release workflows; no publishing as an accidental side effect of a local check.

Work: supervisor health/restart policy, redacted structured events, liveness separated from real readiness (provider/audit/quota/store/peer quorum), task/queue/backlog/latency/error/denial metrics, disk/memory alerts, log retention, key revocation procedure and incident runbook. Monitoring cannot declare readiness from local Butler startup alone.

Acceptance: fresh staging host installs an exact artifact without a checkout-specific venv or secret in image layers; health URL/port/command are exercised. One running poller handles an owner-side deterministic command, a permitted provider reply (separately authorized), denied request and restart. Restore a backup and roll back to previous artifact without data loss or schema incompatibility; supervisor restart cannot repeat unknown consequential effects. Measure agreed load/latency/cost/availability ceilings and test saturation/backpressure.

### P6 — Independent release review and staged launch

Dependencies: every gate for the chosen release scope; exact release SHA CI.

Work: independent adversarial review of all ingress-to-actuator routes, isolation, durable authorization, provider data handling, secrets/supply chain and operational recovery; resolve blockers, publish honest scope and operator instructions, small allowlisted canary, observation window, then explicit expansion approval.

Release packet: exact SHA/tree hashes + artifact digest; CI URLs; full test/warning results; denied/admitted live evidence without private payloads; threat model and resolved findings; backup/restore/rollback results; scope/disabled-feature list; privacy/cost terms; monitoring/runbook; named approver. Unresolved authority bypass, tenant leak, unknown-effect retry or missing audit acknowledgement is a NO-GO.

## 4. Parallelization and decision rules

- Parallel after contracts stabilize: recovery qualification, dormant metering qualification, trusted nonbeta thread completion, CI/container packaging research and documentation.
- Keep peer state-machine foundation first; gateway, grant consumer and distributed worker implementation depends on that committed reviewed interface.
- Keep worker file ownership disjoint, source reviewed, models explicitly free with successful smoke/benchmark, no paid fallback, no secret/private-runtime access, bounded sessions, actual tests and scoped commits. A running process is not delivery. Free provider access/privacy can change and is not a production SLA.
- Use short spec -> review -> implement -> test -> integrate checkpoints. Owner approval is required for new security policy, privilege/ingress expansion, paid provider or release/deployment. This consolidation authorization does not grant any of those.

## 5. Immediate next build after this completion pass

First close any integrated test/review findings and confirm the canonical preflight. Then freeze P1 end-to-end identity/metering contract and P2 deterministic peer application contract with the owner policy decisions. Finish P1 without opening beta, independently review it, then execute the harmless P2 peer task. Container repair may proceed in parallel in staging but cannot waive either authority or privacy gates. No calendar promise or completion percentage substitutes for these observable gates.

## 6. External references and free-model privacy

Reviewed official sources on October 15, 2026:

- OpenCode Zen documentation: https://opencode.ai/docs/zen/ — the retrieved official search listing identifies Muse Spark 1.3 Contributor Free as free. Full live privacy/retention terms could not be fetched reliably during this session, so training/retention permissions remain unverified and must be checked before submitting confidential material. Treat contributor/free source submissions as potentially retained; only owner-authorized repository source and synthetic tests belong in this lane, never credentials or runtime/user data. Reported zero cost from actual local runs does not establish future pricing or retention guarantees.
- OWASP Docker Security Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Docker_Security_Cheat_Sheet.html — informs P3/P5: unprivileged users, constrained resources/network/filesystems, no exposed Docker socket, scanning and controlled dependencies. Containers share the kernel and are not proof against a hostile host administrator.
- SLSA Build Track Basics: https://slsa.dev/spec/v1.2/build-track-basics — informs the release packet: provenance identifies the builder, build process and inputs. The plan does not claim this repository currently meets a SLSA level.

