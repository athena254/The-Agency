# Plan: Multi-submitter, parallel task execution for The Agency

## Context

The Agency's intent: humans **and** agents assign tasks → the system decomposes → assigns to agents → returns results. Today three gaps block that:

1. **No external intake** — both FastAPI servers deny everything except `GET /v1/health` with a blanket 503 middleware (`api/server.py:274-290`, `butler/server.py:181-194`), and `require_peer_authority` (`api/authority.py`) 503s unconditionally. `POST /v1/tasks` and `POST /v1/message` are dead code.
2. **No internal delegation** — `run_autonomous` assigns subtasks but never executes them; no structured agent→agent handoff exists.
3. **No parallelism** — every submitter (Butler chat, Telegram) awaits `execute_task` inline; `TaskManager.take_next_pending()` (the consumer seam) has no consumer.

Owner decisions (confirmed): **bearer-token auth** (peer grants stay a follow-up per PLAN_V1 sequencing) and **SQLite-backed tasks now** (survive restarts).

Key architectural facts this plan builds on:
- `Task.output` field already exists but is never populated; `Task.parent_id` exists (hierarchy ready); `take_next_pending()` exists with priority ordering.
- `execute_task` is reentrant-safe today (ToolDriver stateless per run, AgentExecutor keyed by task_id, stores lock-guarded) — parallel execution needs a dispatcher, not orchestrator internals changes.
- Orchestrator has `start()/stop()` lifecycle (`orchestrator.py:268,331`); `ButlerService.start()` calls `orchestrator.start()` — dispatcher wires in there.
- Bearer auth precedent: `OwnerAuthenticator.authenticate_local` constant-time compare (`admission/auth.py:29-40`).
- SQLite WAL precedent: `loop_state.py`, aiosqlite stores in memory/evidence/audit.

## Workstream 1 — SQLite-backed TaskManager (durability + shared queue)

**NEW `src/agency/kernel/task_store.py`** — aiosqlite store (WAL), following `loop_state.py`/memory-store patterns:
- Tables: `tasks(task_id PK, title, status, created_by, input JSON, output JSON, error, parent_id, priority, seq, created_at, updated_at)`, `task_messages(id PK, task_id, type, content JSON, metadata JSON, created_by, created_at)`.
- **Atomic claim**: `take_next_pending` becomes a `BEGIN IMMEDIATE` transaction (SELECT highest-priority pending → UPDATE to RUNNING) so two processes' dispatchers can't claim the same task. Pending order = `priority DESC, seq ASC`.
- Startup recovery method: tasks found RUNNING at boot → requeue to PENDING + append message "requeued after restart" (dispatcher died mid-run; LLM-side work is safe to redo).

**MOD `src/agency/kernel/tasks.py`** — `TaskManager(store: TaskStore | None = None)`: write-through on create/update_status/add_message; `update_status` gains `output: dict | None` kwarg (populates `Task.output` on COMPLETED); `list_tasks` gains `created_by: str | None` filter (scoped reads); pending queue backed by the store when present (in-memory fast path unchanged when store is None — existing tests keep working).
- DB path: `AGENCY_TASKS_DB` env, default `data/tasks.db`. Both servers share the same file → tasks created via butler are visible via api and vice-versa.

## Workstream 2 — Bearer-token auth

**NEW `src/agency/api/auth.py`**:
- `Principal(caller_id, role, scopes)`; roles: `owner | human | agent`. Default scopes per role: owner=all, human/agent=`tasks:write, tasks:read, chat:send`.
- `TokenRegistry` — loads JSON file at `AGENCY_API_TOKENS` path: `[{"token", "caller_id", "role", "scopes"?}]`; indexed by `sha256(token)` in memory; constant-time comparison. Empty/unset file → registry disabled → authed routes stay 503 (fail-closed posture preserved).
- FastAPI dependency `require_principal(*required_scopes)` → 401 (missing/bad token), 403 (scope missing); attaches `request.state.principal`.
- Log a warning when tokens are configured but server binds a non-loopback host (token auth over plain HTTP exposure).

**MOD `api/server.py` + `butler/server.py` middleware** — replace blanket deny with: allow `GET /v1/health` (+ signed ballots on api, unchanged); allow requests with a valid bearer token to the **enabled authed paths only** (`/v1/tasks*` on api, `/v1/message` on butler); deny everything else 503 as today. Routers swap `Depends(require_peer_authority)` for `Depends(require_principal(...))` on the enabled routes only; other routers (memory, evidence, bridges) keep the 503 dependency.

## Workstream 3 — Enable the intake surface

**MOD `api/routers/tasks.py`**:
- `created_by` is bound server-side to `principal.caller_id` (client-supplied value ignored unless role=owner). Unauthenticated submitters cannot spoof identity.
- Scoped reads: GET list/get/messages filtered to `created_by == caller_id` unless owner.
- Guards: non-owner priority clamped to ≤5; `input.description` capped at 8000 chars (parity with butler).
- PATCH status restricted: creator may set BLOCKED (cancel) on own PENDING/RUNNING tasks; other transitions owner-only.
- New optional field `input.agent_id` — validated against the registry; names the agent that will execute.

**MOD `butler/server.py`**: enable `POST /v1/message` behind auth — `sender = principal.caller_id`, `thread_id` allowed for authenticated callers (drop the 403 when authed), `memory_enabled=True` (threads/memory are already sender-scoped via ThreadStore). Unauthenticated POST → 401.

## Workstream 4 — TaskDispatcher (parallel execution)

**NEW `src/agency/dispatch.py`** — `TaskDispatcher(task_manager, execute_fn, max_concurrency, poll_interval=0.25)`:
- Loop: `take_next_pending()` → spawn `asyncio.create_task(self._run(task))` under an `asyncio.Semaphore(max_concurrency)`; sleep when queue empty or pool full.
- Holds in-flight map `{task_id: asyncio.Task}` + completion events; `wait(task_id, timeout)` awaits a task's completion (returns final status + `task.output`).
- Graceful stop: stop polling, drain in-flight with timeout.

**MOD `src/agency/orchestrator.py`**:
- `start()` launches dispatcher, `stop()` drains it (config: `max_concurrent_tasks`, default 2, via `AgencySettings`).
- `execute_task` tolerates an already-RUNNING task (dispatcher claims first via `take_next_pending` — currently `RUNNING→RUNNING` transition raises at `tasks.py:262-276`); adds a per-task `asyncio.Lock` so a second execution of the same task is refused.
- **Agent routing for external tasks**: execution agent = `input.agent_id` (if registered) → else `created_by` (if registered) → else first registered `general`-domain agent. Submitter identity stays in `created_by` for audit.
- On COMPLETED (orchestrator.py:1043): write `task.output` (summary + result) so pollers get the payload.
- New `wait_task_result(task_id, timeout)` delegating to the dispatcher's `wait` (raises `NotStartedError` when no dispatcher → library callers unaffected).

**MOD `src/agency/butler/service.py`** — `execute()` uses dispatcher-wait when the orchestrator has a dispatcher (submit → `wait_task_result`); falls back to today's inline `submit_task`+`execute_task` otherwise (keeps existing butler tests green, eliminates the claim race in production).

Result: Telegram chat, butler HTTP, API submissions, and delegated children all share one queue; tasks from different submitters run concurrently up to `max_concurrent_tasks` per process (×2 processes with both servers running).

## Workstream 5 — Internal agent→agent delegation

**MOD `src/agency/orchestrator.py`**:
- `delegate_task(parent_task_id, target_agent_id, title, description, priority=0, timeout=300)` — resolves target agent from the identity registry (404-style error if unknown), creates a child task (`parent_id` set, `created_by=target_agent_id`, `input.description` set), waits via `wait_task_result`, returns child output.
- **Depth guard**: walk `parent_id` chain; refuse beyond `max_delegation_depth` (default 2) — prevents recursion storms and A→B→A cycles.

**MOD tool registry** (new builtin `delegate_task` tool, following `tools/builtin/web.py` pattern): params `{target_agent_id, title, task, priority?}`; uses `ToolContext.agent_id/task_id` (built fresh per run at orchestrator.py:315-329) to call `delegate_task`; returns the child's result text to the delegating agent's tool loop — "assigns work, gets back a result."

Explicitly deferred: wiring `run_autonomous`'s assignments to executed child tasks (owned by the BlacksmithOps plan, `docs/PLAN_BLACKSMITH_FORGE_ORCHESTRATOR.md`); forge-domain execution leg.

## Workstream 6 — Ops/CLI + docs

- `agency task create` (`cli/main.py:457-496`): add `--token`/`AGENCY_API_TOKEN` env → sends `Authorization: Bearer`; add `agency task get <id>` (wait/poll for result) — thin client of the same endpoints.
- Config additions in `config/settings.py`: `max_concurrent_tasks`, `max_delegation_depth`, `tasks_db_path`.
- **MOD `gaps.md`**: update #6 (external entry exists, token-authed; peer-grant actuation still D6); add: tasks DB single-writer-process assumption for claims is relaxed but audit/memory stores remain per-process; engine assignments still unexecuted.

## Tests

- NEW `tests/test_task_store.py` — persistence round-trip, atomic claim under contention, startup recovery requeue, WAL.
- NEW `tests/test_api_auth.py` — token parse/roles/scopes, 401/403, fail-closed when unset.
- NEW `tests/test_task_dispatcher.py` — N tasks with max_concurrency=2 (fake execute asserts ≤2 concurrent), priority order, wait(), graceful drain.
- NEW `tests/test_http_tasks_auth.py` — TestClient: 401 unauthed, 201 authed, `created_by` binding, scoped read isolation between two principals, priority clamp, result visible via GET after completion (echo-mode LLM).
- NEW `tests/test_orchestrator_delegation.py` — delegate tool → child executed by target agent, result returned to parent loop, depth guard, unknown target.
- UPDATE existing middleware-posture tests (`test_api_server_coding_agent.py`, `test_butler.py`, governance/ballot boundary tests) for the new allow-list behavior.

## Verification (end-to-end)

1. Targeted: `pytest tests/test_task_store.py tests/test_task_dispatcher.py tests/test_api_auth.py tests/test_http_tasks_auth.py tests/test_orchestrator_delegation.py tests/test_butler_http_auth.py` (new butler auth test).
2. Full suite (~20 min, do not pipe through tail) — must stay green.
3. Live E2E on this machine: write a 2-entry token file; start `agency start` and `butler start`; `curl POST /v1/tasks` from two different tokens + a Telegram message simultaneously → all three run concurrently (verify via interleaved audit/task logs and `GET /v1/tasks`), each principal reads only its own tasks, results appear in `task.output`.
4. Restart durability: create task → kill process → restart → task still listed; a task caught RUNNING at boot is requeued and completes.
5. Delegation E2E: a chat task whose prompt instructs the agent to `delegate_task` a subtask to the coding agent → child executes (worktree/audit evidence) → result present in parent reply.

## Non-goals (this round)

Peer-grant actuation (D6/D8), `run_autonomous` execution leg / BlacksmithOps phases 2-6, webhook callbacks to submitters (poll-only), rate limiting/budgets for external callers, enabling memory/evidence/bridge routes, multi-node consensus — each recorded as follow-ups.