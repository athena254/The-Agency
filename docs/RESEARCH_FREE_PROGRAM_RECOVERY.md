# Research Free Program Recovery — Failure Recovery & Autonomous Execution
Base: 1e38912 | Durable refs checked by coordinator; snapshot refs are NOT durable.

## 1. Owner-mandated requirements (corrects prior crash-only error)
- Byzantine/compromised-peer tolerance IS REQUIRED, not crash-only. Prior crash/omission-only claim is withdrawn.
- Affected-input mandatory: missing blocks; rejection can be overridden only under approved peer policy. An approved quorum policy may override rejection; neither a coordinator nor a lone peer may do so.
- Beta model paths are disabled: no positive beta execution claimed. Only denial path observed; all else UNVERIFIED.

## 2. Evidenced (durable refs only)
- Volatile state: `src/agency/agents/executor.py:126-129` — in-memory run state, lost on restart.
- No resume claim: `src/agency/workflows/executor.py:1-6` — module explicitly disclaims automatic resume and exactly-once execution.
- FULL durability: `src/agency/telegram/budget_store.py:156-167` — connections explicitly request WAL and synchronous=FULL; this is configuration evidence, not a power-loss durability test.
- Model reservation API: `src/agency/telegram/budget_store.py:563-572` — reserve_model_call entry point. It reserves attempts; no refund/release guarantee is implied. Production dispatch wiring is still absent.
- Indeterminate on audit failure: `src/agency/tools/registry.py:315-334` — failed ToolResult with INDETERMINATE evidence on outcome-audit persistence failure. The external effect may already have occurred.
- All other prior snapshot line refs (:708-725, :796-850, etc.) are NOT durable; treat as UNVERIFIED.

## 3. Gaps / UNVERIFIED (do not fabricate)
- UNVERIFIED: durable outbox/inbox, deadlines/cancel enforcement, lease fencing, worker supervision, provider exhaustion, disk-corrupt mode, backup/restore, chaos harness, quorum/TTL/governance enforcement.
- Queues/gossip/signatures alone are not consensus: no quorum commit, leader/fencing, or exactly-once actuator evidenced.
- Unknown external effects must not be blindly retried.

## 4. Recovery rule (replaces unsafe retry-or-compensate)
- Restart state is INDETERMINATE until reconciled: RECONCILE RESERVED/RUNNING -> INDETERMINATE -> reconcile-before-any-retry.
- Retry only known-safe idempotent ops after reconcile. Compensate only with explicit authorization. Never auto-retry unknown effects.

## 5. Data/API proposals (do not open default-deny routes)
- `TaskJournal(task_id,op_id,kind,state: PENDING|RUNNING|DONE|FAILED|CANCELLED|INDETERMINATE,lease_id,deadline,attempts,idem_key)` — INDETERMINATE required.
- `Outbox(op_id PK,dest,payload,state,next_retry_at)` / `Inbox(key PK,first_seen)` — proposals only.
- Proposed (not existing): `POST /v1/bridges/{name}/execute {task,idem_key,deadline,lease_id}`; `POST /tasks/{id}/cancel`; `GET /tasks/{id}/journal`. Existing default-deny routes must not be opened to implement these.
- Ownership proposal: kernel=tasks/journal; workflows=retry/deadline; tools=loop budget; telegram=admission; lattice=vote log only; bridges=idempotent route; audit=append-only. UNVERIFIED.

## 6. Tests + gates
- Tests: kill-9 mid-op -> INDETERMINATE on restart, no dup without reconcile; partition/duplicate/reorder; corrupt db; exhaust budget; cancel-mid-run. Assert denial + indeterminate paths AND successful authorized read-only recovery/completion; always denying is not operational readiness.
- Gates: chaos green, restore drill, audit-loss drill, deadline/cancel SLO, no silent paid fallback.


Reviewer: proposal sketches are not frozen interfaces. The main program specification controls ownership/dependencies and requires BFT peer authority separately from local recovery. The earlier crash-only worker statement was rejected and corrected before integration.
