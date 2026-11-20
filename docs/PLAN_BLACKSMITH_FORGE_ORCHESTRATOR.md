# PLAN: Blacksmith — Forge Orchestrator & Manager

**Date:** 2026-11-20 · **Status:** DRAFT for owner review · **Branch target:** `feat/blacksmith-orchestrator` (off `feat/self-improve-forge`)

**Goal:** Make the Blacksmith the real orchestrator and manager of the Forge: it takes build
requests, dispatches bounded jobs to striker workers through the existing gate pipeline,
tracks verdicts/retries/costs, and reports honest evidence. It never writes code itself and
never authorizes merge or release.

**Architecture:** A deterministic `BlacksmithOps` service (new, `src/agency/forge/orchestration.py`)
owns a persisted job lifecycle and calls the EXISTING Forge entry points (`run_external_agent`,
`run_pipeline`, `build_tool`). The Blacksmith identity gets a real pinned system prompt. The
autonomy engine's simulated tool-gap "self-publish" is replaced with real builds whose real
verdicts feed the proof chain. Management surfaces reuse `BacklogStore`, `MergeQueueStore`,
`CostStore`, `SchedulerConfig`.

**Tech stack:** Python 3.12, pydantic v2 (frozen, extra=forbid), SQLite, structlog, pytest.
LLM only where already configured (review gate, optional intake); orchestration logic is
deterministic code, not LLM improvisation.

---

## 1. Role definition (agreed with owner)

- **Blacksmith = manager.** Intake → slice → dispatch → monitor gates → retry/escalate → report.
- **Strikers = builders.** External CLI workers (opencode etc.) do the editing, in isolated
  worktrees, via the existing pipeline. "Striker" remains a display name only.
- **Human = ship authority.** PASS jobs land in the merge queue awaiting human approval.
  `release_not_authorized` stays. No auto-merge, no deploy, ever.

### Non-goals
- No peer authority / consensus changes (P2 stays gated).
- No beta ingress, no new public HTTP endpoints.
- No NYX/ATHENA dependency; reviewer stays Agency-native.
- No LLM-driven orchestration decisions in v1 (deterministic state machine; LLM assists intake
  slicing and review only, and fails closed).

## 2. Verified current state (evidence, 2026-11-19/20 session)

| Fact | Evidence |
|---|---|
| Blacksmith has NO system prompt, no skills | activated in-memory: metadata keys = blueprint_id/version/factory/skill_refs/workflow_refs only |
| Domain `forge` not tool-capable → classic no-tools LLM path with GENERAL prompt | `orchestrator.py:74` `TOOL_CAPABLE_DOMAINS = {"research","general"}` |
| Engine tool-gap "forge build + self-publish" is a SIMULATION | `engine.py:158-166`: hashes tool NAME, literal `"PASS"`, digest==itself → proof `published=true` with no build (observed live for `weather_lookup`) |
| Default planner never requires `forge` cap → Blacksmith never spawned | live `run_autonomous`: spawned `spawned-*-general`, not Blacksmith |
| Real Forge pipeline WORKS | live `forge code` run 2026-11-20: overall PASS, 4/4 sub-gates, isolation verified, report `D:/agency-cleanup/forge-run.json` |
| Management modules exist, unused by Blacksmith | `forge/backlog.py` (BacklogStore, slice_prd), `forge/merge_queue.py` (MergeQueueStore, evaluate_merge), `forge/cost.py` (CostStore, record_cost), `forge/scheduler.py` (SchedulerConfig.model ← cron_model), `forge/evidence.py` |
| Real tool build path exists | `autonomy/forge_auto.py:137 build_tool(name, code, tests, registry)` — gates + register iff PASS + self-publish |
| Review LLM not shown test results | live run: review finding `tests-not-run` beside tests gate PASS (2/2) |

## 3. Design

### 3.1 Job lifecycle (deterministic state machine)

```
QUEUED → DISPATCHED → GATED ─┬→ PASSED → AWAITING_HUMAN (merge queue)
                             └→ FAILED → RETRYING (≤ max_retries, default 2)
                                          └→ BLOCKED (escalate to human, no auto-retry)
```

- One job in flight at a time (RAM constraint: each striker worker ≈ 600MB).
- Every transition: SQLite row update + kernel audit event + proof event with the REAL
  `report_id`, gate values and artifact digests. No transition without evidence.
- Retry feedback = previous attempt's failure detail (bounded tail, secrets redacted —
  reuse `loop_workers._failure_tail` pattern).
- UNCERTAIN on crash/interrupt: job marked UNCERTAIN at startup, never auto-resumed
  (same rule as owner pilot).

### 3.2 New module: `src/agency/forge/orchestration.py`

```python
class ForgeJob(BaseModel):        # frozen, extra=forbid
    job_id: str; title: str; task: str; repo: str
    acceptance: tuple[str, ...]; test_paths: tuple[str, ...]
    state: JobState; attempts: int; report_id: str | None
    gate: str | None; changed_paths: tuple[str, ...]
    artifact_sha256: str | None; cost_usd: float; created_at: str; updated_at: str

class BlacksmithOps:
    def __init__(self, *, db_path, evidence_store, cost_store, merge_queue,
                 audit, worker_factory, max_retries=2, agent_label="blacksmith"): ...
    def submit(self, title, task, repo, *, acceptance=(), test_paths=()) -> ForgeJob
    def dispatch_next(self) -> ForgeJob | None      # calls run_external_agent (absolute paths)
    def record_verdict(self, job_id, report) -> ForgeJob   # real gates → state transition
    def build_tool_job(self, name, code, tests, registry) -> ToolBuildResult
        # wraps autonomy.forge_auto.build_tool; returns REAL gate + digests
    def status(self) -> BlacksmithStatus            # queue, recent verdicts, costs, blocked
```

- `dispatch_next` passes the worktree as an ABSOLUTE path (isolation rule) and snapshots the
  original repo before/after (`worktree.snapshot_changes`); any `isolation_violations` → FAILED.
- Worktrees for jobs on C:-full hosts: repo must live on D: (validated at submit; refuse C:
  repos when free space < 1GB — honest refusal, not a crash).
- `record_verdict` reads sub-gates individually; a FAIL names the failing sub-gate in the job
  row and the escalation note.

### 3.3 Blacksmith identity + prompt

- New builtin segment `segments/agency-forge-orchestrator.md` (BASE kind), registered alongside
  `agency-base-identity` in `prompts_builtin/__init__.py` (NOT in the REAP manifest — that is
  upstream-generated). Content:

```markdown
# Forge Orchestrator (Blacksmith)
You are the Blacksmith, the Agency's forge manager. You orchestrate builds; you do not write code.
- Dispatch bounded, single-purpose build jobs to striker workers through the Forge pipeline.
- Read every sub-gate (inspection, security, tests, review) before judging a result.
- Never claim a build, test or check that did not happen; quote real report ids and digests.
- Retry a failed job at most twice with concrete feedback, then BLOCK and escalate to a human.
- A PASS is evidence, not permission: ship/merge/release always requires human approval.
- Stay inside the forge domain; refuse requests outside it and say why.
```

- Extend `activate_for_caps(..., prompt_refs=None, skill_refs=None)` (additive, default
  unchanged) so `activate_blacksmith` pins
  `[base-identity, evidence-discipline, l0-scope, forge-orchestrator]` (all 1.0.0).
  Factory must be constructed with `is_prompt_published` + `prompt_resolver` (as in
  `tests/test_factory_prompt_refs.py`); `api/server.py` startup wiring updated accordingly.
- Idempotent: repeated activation returns the same agent id; prompt pinning is versioned.

### 3.4 Engine honesty fix (replace the stub)

`engine.py` tool-gap loop becomes:

```python
if g.action == "BUILD":
    if ops is None:                      # no forge wired → honest BLOCK, never fake PASS
        proof.append("forge", "build_blocked", {"tool": t, "reason": "no forge ops configured"})
        continue
    result = ops.build_tool_job(t, code=..., tests=..., registry=tool_registry)
    published = can_self_publish(result.gate, result.artifact_sha256, result.manifest_sha256)
    proof.append("forge", "self_publish", {
        "tool": t, "published": published, "gate": result.gate,
        "report_id": result.report_id, "sha256": result.artifact_sha256})
```

- `run_autonomous(..., ops: BlacksmithOps | None = None)` — additive parameter.
- `can_self_publish` unchanged (its logic is correct; the stub fed it fake inputs).
- Tool code/tests for gap builds come from a bounded template or an explicit job spec —
  NOT from hashing the name. If no spec is available → `build_blocked` (honest).

### 3.5 Planner + routing

- New decomposition strategy `forge_build_strategy` (in `agents/planner.py` or
  `autonomy/strategies.py`): subtasks whose title/description match build verbs
  (build/create/implement + tool/module/function) get `required_capabilities=["forge"]`.
  Default strategy unchanged (opt-in via `AgentPlanner(strategy=...)`).
- Engine already spawns Blacksmith when `forge` is missing (committed wiring) — no change.
- `AgencyOrchestrator`: forge-domain agents route to `BlacksmithOps` (manage/dispatch), NOT
  the classic LLM chat path. Add explicit branch before `tool_capable`; do NOT add `forge` to
  `TOOL_CAPABLE_DOMAINS` (Blacksmith manages, it doesn't chat with tools).

### 3.6 Management surfaces (reuse, don't rebuild)

- Intake: `slice_prd` → BacklogStore items → `BlacksmithOps.submit` per item.
- PASS jobs → `MergeQueueStore` entry (human approval; `evaluate_merge` unchanged).
- Each dispatch → `record_cost` (worker cost_usd + review model estimate; estimate labelled).
- Cron: `SchedulerConfig` already carries `model=cron_model()`; scheduled runs call
  `dispatch_next` bounded by quota. No new scheduler.
- CLI: `agency forge status [--json]` → queue, last verdicts, blocked jobs, costs.
  Read-only. No ship command (ship.py human approval path unchanged).

### 3.7 Reviewer test-results gap (small, separate)

`run_review` receives the tests sub-gate result (rc, passed/failed counts, note) and includes
it in the LLM review context; the `tests-not-run` finding may only appear when tests truly
did not run. Fails closed on LLM error (unchanged).

## 4. Phases (TDD, dependency order, gate per phase)

Worktree: `D:/agency-blacksmith-work` (branch `feat/blacksmith-orchestrator` off
`feat/self-improve-forge`) — keeps the concurrent agent's canonical checkout untouched.
Gate set per phase (from repo root):

```bash
.venv/Scripts/python.exe -m ruff check src/ tests/
.venv/Scripts/python.exe -m ruff format --check src/ tests/
.venv/Scripts/python.exe -m mypy src/
.venv/Scripts/python.exe -m pytest <phase tests> -q
```

### Phase 1 — Identity + prompt (no behavior change yet)
1. RED: `tests/test_blacksmith_prompt.py` — activation pins 4 prompt refs; issued
   `system_prompt` contains "do not write code" and "human approval"; idempotent re-activation.
2. GREEN: segment file + builtin registration + `activate_for_caps(prompt_refs=...)` +
   `activate_blacksmith` pins refs; update `api/server.py` startup factory wiring.
3. Verify: `pytest tests/test_blacksmith_prompt.py tests/test_blacksmith.py tests/test_forge_factory_auto.py tests/test_factory_prompt_refs.py -q`
4. Commit: `feat(forge): pin forge-orchestrator prompt on Blacksmith identity`

### Phase 2 — BlacksmithOps core (hermetic)
1. RED: `tests/test_forge_orchestration.py` — lifecycle transitions with a FAKE worker
   (no CLI, no network): submit→dispatch→PASS→AWAITING_HUMAN; FAIL→retry→BLOCKED at
   max_retries; crash→UNCERTAIN on reopen; isolation_violations→FAILED; one-job-in-flight;
   every transition writes audit + evidence with real report ids.
2. GREEN: `src/agency/forge/orchestration.py` per §3.2.
3. Verify: focused file 10× (flakiness rule), then gate set.
4. Commit: `feat(forge): add BlacksmithOps job lifecycle on the real pipeline`

### Phase 3 — Engine honesty fix (depends: Phase 2)
1. RED: `tests/test_autonomy_engine_honesty.py` — with ops=None, missing tool → proof
   `build_blocked`, NEVER `self_publish published=true`; with a fake ops returning a real
   FAIL report → published=false; PASS + matching digests → published=true with report_id.
   Regression: the old hash-the-name behavior must be impossible.
2. GREEN: §3.4 changes in `engine.py`.
3. Verify: new file + `tests/test_autonomy_e2e.py tests/test_autonomy_router.py -q`.
4. Commit: `fix(autonomy): replace simulated forge self-publish with real gated builds`

### Phase 4 — Planner + orchestrator routing (depends: Phases 1–3)
1. RED: `tests/test_forge_routing.py` — build-verb task under `forge_build_strategy` →
   subtask requires `forge` → engine spawns Blacksmith (not generic); forge-domain agent in
   AgencyOrchestrator routes to ops, not classic LLM path; default strategy unchanged.
2. GREEN: §3.5.
3. Verify: new file + `tests/test_orchestrator.py tests/test_orchestrator_skills.py -q`.
4. Commit: `feat(autonomy): route build subtasks to the Blacksmith`

### Phase 5 — Management surfaces + reviewer gap (depends: Phase 2)
1. RED: `tests/test_forge_management.py` — backlog slice → jobs; PASS → merge queue entry
   (release_not_authorized); cost recorded per dispatch; `agency forge status --json` shape;
   reviewer sees test results (no false `tests-not-run` when tests ran).
2. GREEN: §3.6 + §3.7.
3. Verify: new file + `tests/test_forge_backlog.py tests/test_forge_merge_queue.py tests/test_forge_cost.py tests/test_forge_llm_review_gate.py tests/test_forge_reviewer.py -q`.
4. Commit: `feat(forge): Blacksmith management surfaces; reviewer sees test results`

### Phase 6 — Live acceptance + docs (depends: all)
Run the real demo (recipe: skill `agency-development/references/real-end-to-end-verification.md`):
1. Scratch repo on D: with a missing-tool spec.
2. `run_autonomous(..., ops=BlacksmithOps(real worker))` on "build weather_lookup tool".
3. Assert from OUTSIDE: real worktree diff, real gates (read sub-gates), proof carries real
   report_id + artifact sha, tool registered iff PASS, original repo + canonical Agency
   checkout unchanged (`git status --porcelain` before/after), sibling repos untouched.
4. FAIL path demo: impossible task → BLOCKED after 2 retries, honest proof, no publication.
5. Update `docs/CURRENT_STATE.md` (Blacksmith section), `KNOWN_ISSUES.md` (stub removed),
   record evidence paths. Gate set + focused full-area pytest.
6. Commit: `test(forge): live Blacksmith orchestration acceptance; refresh state docs`

## 5. Acceptance scenario (the definition of done)

Human: "build a weather_lookup tool" →
Blacksmith job QUEUED → striker dispatched into isolated worktree (absolute path) →
real inspection/security/tests/review gates → verdict recorded with report_id + digests →
PASS: tool registered via real `can_self_publish`, merge-queue entry awaits human;
FAIL: ≤2 retries with feedback, then BLOCKED + human escalation →
proof chain contains NO event without real evidence → human ship approval still required.

## 6. Risks / constraints

- **RAM:** one striker at a time; queue the rest. Review LLM calls are small.
- **Disk:** C: effectively full — all job repos/worktrees on D:; submit refuses C: repos
  under 1GB free. Evidence DBs small (SQLite).
- **Concurrent agent:** canonical checkout is owned by another running agent; this plan
  executes in a separate D: worktree/branch and merges only with owner approval.
- **Isolation:** absolute paths + before/after snapshots + external `git status` checks;
  the guard only sees the main checkout, so siblings are checked manually in Phase 6.
- **Security scan scope:** production `subprocess.*` still blocks PASS (by design); job
  specs must not require it. Test-path exemption already scoped.
- **No authority creep:** every phase keeps `release_not_authorized`; merge queue is
  human-gated; audit/policy/sandbox never bypassed.

## 7. Open owner decisions

1. Max retries default 2 — confirm.
2. Gap-build tool specs: bounded template library vs. explicit per-job spec only (plan
   assumes explicit spec; absent spec → honest `build_blocked`).
3. Whether `agency forge status` should also surface in Mission Control (P2-gated panel
   stays locked; read-only status may be allowed later — not in this plan).