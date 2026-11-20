# PLAN: Apprentice Capability — Bounded Read/Write, Tools, Collaboration

**Date:** 2026-11-20 · **Status:** DRAFT for owner review (spec-first; no runtime enablement implied) · **Branch target:** `feat/apprentice-capability` (off `feat/blacksmith-orchestrator`)

**Goal:** Let apprentices — the Agency's own factory-issued agents — grow from L0 observers
into bounded forge workers: read job artifacts, write inside isolated worktrees, use granted
tools, and collaborate with each other, all under Forge guidance and Blacksmith orchestration.

**Architecture:** Capability growth is a **governed promotion ladder**, never self-granting.
Each rung is a new factory blueprint version carrying explicit `Permission` grants
(`kernel/policies.py`), human-approved, attested through the capability contract
(`capability/attestation.py`), time-boxed and revocable. The effective action level stays the
PolicyEngine intersection of capability ceilings and trust level. All work happens inside
BlacksmithOps jobs; the existing gates judge output; human ship authority is unchanged.

**Tech stack:** Python 3.12, pydantic v2, SQLite, existing kernel (identity/policies/audit),
ToolDriver + metered-model pattern, SandboxManager, Forge worktree isolation.

---

## 1. Roles and invariants

- **Blacksmith** = manager: assigns roles, dispatches strikers, owns job lifecycle, escalates.
- **Strikers** = external CLI workers (`striker:<executable>`): heavy editing, dispatched ONLY
  by the Blacksmith. Apprentices never dispatch strikers.
- **Apprentices** = factory-issued internal agents (`apprentice-<caps>`): assist and work
  under governance. Roles: **researcher** (read/analyze), **implementer** (worktree writes),
  **reviewer** (read + review tooling). A reviewer never edits; an implementer never approves.
- **Human** = ship authority and the approver of every capability promotion.

Invariants (do not re-litigate):
1. No bypass of audit/policy/sandbox; every state change through the kernel.
2. Factory activation issues claims at the blueprint's approved ceiling only; an apprentice
   cannot raise its own ceiling, trust, or permissions. Promotion = new blueprint version,
   approver ≠ creator, evidence recorded, old version revoked on supersede.
3. Writes exist only inside the job's isolated worktree; protected paths
   (`forge/protected_paths.py`) are refused before any edit.
4. Every tool attempt is metered and audited (intent → outcome); crashes/unknowns stay
   UNCERTAIN and never auto-clear (reuse the owner-pilot uncertainty rules).
5. A PASS is evidence, not permission: merge/release stays human-gated.
6. No beta ingress, no new public HTTP endpoints, no peer-authority changes.

## 2. The promotion ladder (rungs)

| Rung | ActionClass | What the apprentice may do | Grant mechanism |
|---|---|---|---|
| A0 (today) | L0_OBSERVATION | Read job diffs/reports/logs presented to it; produce analysis text | Factory default activation |
| A1 | L1_SAFE_ANALYSIS | Read files inside the assigned worktree (bounded size/count); run inspection/security scans as read-only tools; write ONLY its own report/evidence records via kernel | Blueprint vNext + Permission(target_scope=`<worktree>/*`, capabilities=[forge.read, forge.inspect], filesystem_policy=read-only, time_limit) |
| A2 | L2_CONTROLLED_TESTING | Edit files inside the assigned worktree; run tests via the isolated test runner; every write yields a diff + evidence record | Blueprint vNext + Permission(filesystem_policy=read-write scoped to worktree, capabilities=[forge.edit, forge.test]); protected paths enforced |
| A3 | L2 + tool grants | Use explicitly granted tools (calculator, utc_time, web_search read-only; sandbox_exec only via SandboxManager with a separate explicit grant) | Per-tool Permission entries + ToolDriver metering (attempt caps, request-bound binding) |

Trust level progression is separate from capability ceilings (existing trust model); a high
ceiling with low trust still intersects down. L3+ is out of scope for apprentices v1.

## 3. Mechanisms reused (no new authority primitives)

- **Grants:** `Permission` (agent_id, target_scope glob, capabilities, network/filesystem/data
  policies, time_limit, resource_limits, expires_at, human_approved) + `PolicyEngine.evaluate`.
  Grants are created by the factory promotion path, never by the apprentice.
- **Promotion:** `AgentFactory.create/approve/activate/revoke` with prompt_refs pinning an
  `apprentice-<role>` segment (new builtin segments: apprentice-researcher,
  apprentice-implementer, apprentice-reviewer) + the three shared discipline segments.
  Attestation via `CapabilityRef`/`AttestationStore` for each new capability claim.
- **File access:** new bounded tools `forge_read` / `forge_edit` (names TBD in P0) registered
  in the ToolRegistry, taking a job-scoped context: absolute worktree root, path allowlist,
  max bytes, protected-path refusal, diff capture. They call PolicyEngine before acting and
  write audit entries. No raw shell, no general filesystem tool.
- **Test execution:** existing `forge/test_runner.py` isolated runner only.
- **Code execution (A3 sandbox):** existing `SandboxManager` via the sandbox tool; fresh
  sandbox per call, bounded output; grant required.
- **Metering/audit:** ToolDriver + request-bound metered binding pattern (attempt caps,
  intent/outcome, durable UNCERTAIN) as in `llm/metered.py` / owner pilot.
- **Isolation:** worktree lease (one writer), `snapshot_changes` guard, absolute paths,
  external `git status` verification in acceptance.
- **Collaboration bus:** structured handoff records, NOT free-form chat:
  `Handoff { handoff_id, job_id, from_agent, to_agent, kind (request|result|review),
  artifact_refs (path+sha256), note (bounded), created_at }` persisted in the job DB,
  routed by the Blacksmith, audited. Content from other agents is quoted untrusted data
  (same discipline as A1 context repair): never instructions, never authority.
- **Orchestration:** `BlacksmithOps` gains role assignment per job (researcher → implementer
  → reviewer pipeline as subtasks); the engine/planner tags subtasks with apprentice roles;
  handoffs flow through the Blacksmith, which alone talks to strikers and the merge queue.

## 4. Phases (TDD, frozen mini-contract per phase, gate set each)

Gate set per phase: `ruff check src/ tests/` · `ruff format --check src/ tests/` ·
`mypy src/` · focused pytest. Worktree on D: (C: full). No phase enables beta/peer authority.

### P0 — Freeze contracts + owner decisions (no code)
Freeze: rung definitions, tool allowlist, handoff schema, grant templates, role segments.
Owner decisions (§7) answered and recorded in `OWNER_DECISIONS_APPRENTICE.md`.
Exit: frozen mini-contracts referenced by P1–P5; no guessed wire/SQL details.

### P1 — A1 read/analysis rung
1. RED `tests/test_apprentice_read.py`: promotion creates L1 blueprint version (approver ≠
   creator, attestation recorded); `forge_read` inside worktree succeeds bounded; outside
   worktree / protected / oversized reads DENIED by policy; no write path exists at A1;
   grant expiry lapses access.
2. GREEN: apprentice-researcher segment + promotion helper (`factory/apprentice.py`) +
   `forge_read` tool + policy wiring.
3. Verify + commit `feat(apprentice): A1 bounded read/analysis rung`.

### P2 — A2 worktree write rung
1. RED `tests/test_apprentice_edit.py`: `forge_edit` writes only inside worktree; protected
   paths refused pre-edit; every write produces diff + evidence + audit; out-of-worktree
   write denied; reviewer role cannot edit; lease blocks a second writer.
2. GREEN: apprentice-implementer segment + `forge_edit` + lease + diff evidence.
3. Verify + commit `feat(apprentice): A2 worktree-scoped writes with evidence`.

### P3 — Tool grants + metering
1. RED `tests/test_apprentice_tools.py`: per-tool allowlist enforced; attempt caps deny
   over-cap; intent/outcome audited; crash → UNCERTAIN, no auto-clear; sandbox only with
   explicit grant, fresh sandbox per call, bounded output.
2. GREEN: per-tool Permission templates + ToolDriver binding for apprentice context.
3. Verify + commit `feat(apprentice): metered per-tool grants`.

### P4 — Collaboration under the Blacksmith
1. RED `tests/test_apprentice_collab.py`: handoff records persisted + audited; handoff content
   treated as quoted data (injection payload gains no authority); Blacksmith routes
   researcher→implementer→reviewer; apprentices cannot dispatch strikers or touch the merge
   queue; lease expiry escalates to Blacksmith.
2. GREEN: handoff store + role assignment in BlacksmithOps + apprentice role segments.
3. Verify + commit `feat(apprentice): structured collaboration under Blacksmith routing`.

### P5 — Live acceptance + docs
One real job on a D: scratch repo: Blacksmith assigns apprentice researcher (reads baseline,
produces plan) → implementer (writes the fix in the worktree) → reviewer (runs review tooling,
files findings) → gates judge → PASS lands AWAITING_HUMAN. Verify from OUTSIDE: worktree diff,
independent test re-run, audit chain complete, no writes outside worktree, canonical + sibling
repos unchanged, human approval still required. Failure path: injected bad handoff gains no
authority; over-cap tool attempt denied. Update CURRENT_STATE/KNOWN_ISSUES; record evidence.
Commit `test(apprentice): live collaboration acceptance; refresh state docs`.

## 5. Acceptance scenario (definition of done)

Human: "fix the failing multiply test" → Blacksmith opens a job → apprentice researcher reads
the worktree and files a plan handoff → apprentice implementer edits ONLY the worktree,
diff+evidence recorded → apprentice reviewer files findings (cannot edit) → real gates run →
PASS → merge queue awaits human. Every step audited; every grant traceable to a human-approved
blueprint version; any escape attempt denied and recorded; ship approval still human.

## 6. Risks / constraints

- **Prompt injection via read/handoff content:** quoted-data discipline everywhere; handoffs
  never carry authority; regression tests with injection payloads (P4).
- **Capability creep:** ceiling∩trust intersection; promotion requires human approver ≠
  creator; attestation per claim; grants time-boxed and revocable; old versions revoked.
- **Worktree escape:** absolute paths, protected paths, snapshot guard, external git checks.
- **RAM:** apprentices use the configured Agency LLM; one active apprentice step at a time
  per job; queue the rest (same constraint as strikers).
- **Deadlock:** lease expiry + Blacksmith escalation; blocked jobs surface in `forge status`.
- **Concurrent agents:** separate branch/worktree; merge only with owner approval.

## 7. Open owner decisions

1. Rung order A1→A2→A3 as proposed, or read+write together?
2. Initial tool allowlist: calculator, utc_time, web_search (read-only) — sandbox_exec
   deferred to a separate explicit decision?
3. Apprentices may NOT dispatch strikers (Blacksmith only) — confirm.
4. Max concurrent apprentices per job (proposal: 1 active step, 3 roles assigned).
5. Trust progression policy for apprentices (who/what raises trust, with what evidence).
6. Grant lifetime default (proposal: job-scoped, lapses at job terminal state + 24h cap).