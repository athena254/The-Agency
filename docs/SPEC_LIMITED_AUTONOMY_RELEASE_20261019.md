# Limited-autonomy usability release — October 19, 2026

## Authorization and objective

Owner request (October 19): make a plan, launch subagents, make Agency fully usable with limited autonomy, perform live tests of every route, research and select the best option rather than interrupting for routine questions, use available OpenCode/DeepSeek V4.1 Flash for assistance and provide milestone reports. This authorizes scoped implementation and harmless live qualification, not paid-provider fallback, publication/merge/release, unrestricted computer access, destructive actions or invention of consequential BFT policy.

This checkpoint starts from published development c852419 (src/tests identical to 6b1d4c4). Canonical main and private data remain untouched. Existing normative boundaries remain: Human <-> Butler <-> Agency; Butler has no validator vote or unilateral consequential authority. A restricted pilot is not decentralized production.

## Product acceptance target

Single owner, loopback local interface and optionally private Telegram; persistent conversations and task visibility; bounded low-risk tool/model iterations; explicit permitted operation set; owner-bound resources; durable request/task intent/result/uncertainty; fail-closed budgets/audit and no blind uncertain retries. Each supported route must have an inventory entry and positive/negative/error/replay tests. Legacy governance, agent activation/release, arbitrary shell/code, unrestricted network fetch and multiuser exposure remain closed until their separate authority/isolation prerequisites exist. A denial route working as specified counts as tested denial, not as enabled functionality.

## Alternatives and sequencing

1. Blindly open current legacy HTTP or beta paths: REJECTED; bypasses existing peer/admission/privacy boundaries.
2. Build a replacement parallel assistant: REJECTED; creates duplicate execution/accounting paths and dead integration.
3. Integrate a narrowly scoped owner pilot with existing stores/Butler/tools behind explicit admission and consistent accounting: SELECTED, subject to a concrete same-revision contract amendment after source mapping. Do not fabricate a Telegram principal for a local request.

Research checked installed OpenCode 1.19.0 and its model registry. Exact available free alias is opencode/deepseek-v4.1-flash-free; no distinct 1017 snapshot was listed. A bounded completed smoke and actual worker events are required before assigning work. Only public repository source/synthetic tests may reach free workers; no credentials, runtime DBs or personal transcripts.

## Milestones

M1 Baseline: reproduce/repair Linux Mypy portability in workflows/recovery.py; attribute admission bootstrap failure without weakening deadlines/assertions; explicitly isolate every fixture-owned store; verify policy denial/unknown-outcome propagation from actual model leaf rather than gateway-only fakes. Commit each reviewed repair.

M2 Safe execution: freeze platform-neutral owner/request identity and effective provider configuration, durable shared attempt accounting, fail-closed task outcome and principal-bound memory resources. Do not enable any new ingress before complete accounting/authorization contract tests and independent review.

M3 Interface: integrate supported UI/Telegram with the approved runtime; persistent thread/task views, accurate availability/disabled messaging, cancellation/result state. No cosmetic claim substitutes for real request wiring. Existing isolated chat remains isolated unless an explicitly different admitted owner path is implemented.

M4 Live qualification: isolated data/config; actual loopback server exercised over HTTP and browser DOM; real approved model/provider harmless prompt and bounded permitted task; denied foreign identity, replay, exhausted budget, unsupported actions, audit/store failures, restart/recovery. Do not fabricate owner Telegram ingress or consume updates while another poller owns them. Owner-side message may remain a separately disclosed gate if no legitimate self-contained test path exists.

M5 Release checkpoint: exact-revision full offline pytest, UI tests, Ruff lint/format, strict mypy including Linux target, independently reviewed ingress-to-actuator paths, no tracked secrets/runtime data, tested launch/stop and rollback. No production-ready claim unless all applicable gates pass. Report residual disabled routes explicitly.

## Initial worker ownership and interfaces

W1 baseline worker: src/agency/workflows/recovery.py; tests/test_local_recovery_v1.py; tests/test_admission_worker_protocol.py; tests/worker_support/admission.py; new narrowly named baseline regression tests. No policy changes or blanket timeout increases. Own isolated worktree.

W2 metering leaf worker: src/agency/agents/executor.py; src/agency/tools/driver.py; tests/test_executor.py and existing driver/metering tests plus new typed-error regressions. Preserve concrete strict model admission. Denials/unknown outcomes must never be automatically retried or converted into successful/fallback outcomes; provider attempt counts asserted. No new ingress or provider calls. Own isolated worktree.

W3 source-map investigator (read only): design the smallest reusable platform-neutral owner admission/service contract by tracing current MeteredModel, budget_store, Butler, memory and prototype APIs. Return exact models/signatures, route matrix, dependency/ownership proposal and acceptance tests. No production code until coordinator freezes amendment.

W4 OpenCode/UI (initially read only): map prototype client/server and Telegram capability discovery; propose exact UI integration contract. Bound model smoke first; implementation only after contract amendment, using exclusive UI files. No permissions escalation or secret/runtime access.

## Verification discipline

TDD RED -> GREEN for behavior repairs; root-cause first for flakes. Project toolchain C:/Users/alphi/theagency/.venv/Scripts/python.exe (verify before use), absolute PYTHONPATH to worker src and absolute test paths. Work CWD/data/temp/logs under C:/Users/alphi/AppData/Local/hermes/cache/scratch/agency-build-20261019/<worker>. Explicit AGENCY_LLM_PROVIDER=echo and AGENCY_LLM_MODEL=echo for offline tests; LATTICE_SQLITE_PATH and all fixture stores scratch-only. No .env in worker checkouts. Capture raw logs and failures outside idle-pruned scratch at C:/Users/alphi/agency-evidence/limited-autonomy-20261019/. Independent review and parent rerun required before integration claims. No code/comment/format/check suppression solely to turn CI green.

## Implementation contract gate

Owner has authorized implementation of this bounded objective in this request; no additional routine approval round is required for the maintenance ownership above. New shared M2/M3 data/API interfaces must be researched, written as an amendment and committed before dependent implementation. Consequential peer policy remains unimplemented rather than guessed. Operational rollout only after applicable qualification and prerequisite verification; no main merge or public release is authorized here.
