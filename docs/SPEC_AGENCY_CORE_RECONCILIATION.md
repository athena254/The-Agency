# Agency core reconciliation — implementation checkpoint

Status: target design, not a claim of completed functionality. Source: `SOURCE_CONSOLIDATED_BRIEF.md` (all 51 sections, preserved verbatim). This document maps the clean checkout at `3df50d2`; the original `main` worktree has unrelated uncommitted changes and is not the build target.

## Product boundaries

The Agency is independent of ATHENA. ATHENA is a peer integration at most; no Agency core ownership, control, or storage may depend on it. Butler owns human interaction and scoped context, not execution authority. Lattice owns coordination/governance state, not an opaque agent chat. Agent Factory owns versioned agent definitions; runtime and identity registries remain separate. Skills are reusable capabilities; workflows are versioned procedures; tools are executable operations. Forge is an Agency-native software factory, not the separate external factory. Security, approval, provenance, and QA enforce decisions in code, not prompts. Use deterministic validation/transitions wherever possible. No claim of production readiness follows merely from a passing unit suite.

## Code-backed gap matrix

| Area | Evidence in checkout | State | Next action |
|---|---|---|---|
| Butler | `src/agency/butler/service.py`, `server.py`, `router.py` | Partial | Preserve routing; introduce explicit thread context and API |
| Threads | `service.py:_recall_history/_store_turn` scope by `chat-{sender}` | Missing independent threads | Durable thread store, ownership checks, isolation tests |
| Workspaces/projects | No workspace/project module under `src/agency` | Missing | Model after thread isolation; do not invent an unbound UI |
| Agent Factory | `kernel/registry.py` identity, `agents/registry.py` runtime | Partial lifecycle, no factory | Versioned spec validation/evaluation then deployment gate |
| Skills | No Agency-native skill module under `src/agency` | Missing | Immutable versioned registry with explicit inputs/permissions |
| Workflows | No workflow module under `src/agency` | Missing | Validate bounded DAG; pin versions; deterministic executor |
| Deterministic execution | `agents/loop.py`, `tools/driver.py` and kernel policies | Partial | Put branching/validation/permission checks in software |
| Lattice | `lattice/api.py`, `backends/sqlite.py`, `models.py` | Partial | Specify ownership/task/approval APIs and durable records |
| Governance | `lattice/governance.py` stores proposals in `_proposals` memory | Partial | Persist proposals/votes; do not label durable yet |
| QA Critic | security red/blue/purple, `agents/verifier.py` | Partial | Define blocking decision contract and integration |
| Sandbox | `security/sandbox/manager.py`, `process.py` | Partial | Audit actual isolation guarantees before claims |
| Security | `kernel/policies.py`, `memory/sms/secrets.py`, sandbox | Partial | Enforce permissions at tool/resource boundary |
| Memory | `memory/sms/store.py`, retrieval; Butler sender scope | Partial | Hierarchical context scoping; no cross-thread leakage |
| Forge | `addons/dark_factory`, `ghost_factory` exist, no Forge module | Missing as integrated factory | Specify before implementation; reuse audited primitives |
| Coding Agent | external bridges, generic agent execution | Missing native Forge operator | Define bounded workflow and permissions later |
| Provenance | `kernel/audit.py`, evidence store | Partial | Link run, step, artifacts, tool/model and approval IDs |
| Observability | logs and HTTP health | Partial | Execution states, metrics, traceable failure IDs |
| Testing | 41 tracked test files | Partial | Baseline suite, isolation/restart/security tests |
| Documentation | README, ARCHITECTURE, HEALTH, ROADMAP | Contradictory/stale | Mark actual vs target; remove ATHENA-as-core diagrams |

No absence claim above means there is no undocumented equivalent elsewhere; it describes the inspected checkout's `src/agency` surface. `README.md` says ResearchAgent is 0%, yet `src/agency/agents/research/agent.py` and `/research` exist. `docs/ARCHITECTURE.md` depicts ATHENA Core within Agency, contrary to the new boundary. `docs/ROADMAP.md` says CI/CD planned, but `.github/workflows` exists. These must be corrected without declaring all subsystems complete.

## First build slice (bounded)

1. **Thread context foundation:** SQLite-backed thread identity and message metadata. `ThreadStore(db_path)` creates `workspaces(id, owner, title)`, `threads(id, workspace_id, owner, title, parent_thread_id, state, created_at, updated_at)`, `thread_messages(id, thread_id, role, content, created_at)`; create/list/get/append/list_messages require owner and reject cross-owner or cross-thread access. Explicit `thread_id` in Butler request must be validated against authenticated/trusted sender before recall; absent `thread_id` continues existing sender-scoped behavior. Branching copies references or explicitly documents snapshot behavior; no implicit memory sharing. A sender string alone is not real authentication: HTTP exposure requires separate authentication before claiming multi-user security.
2. **Agency-native skill registry:** immutable `(skill_id, version)` records with name, description, inputs/outputs schema, permissions, implementation reference, provenance and status (DRAFT/TESTING/APPROVED/PUBLISHED/DEPRECATED/RETIRED). Publication requires tests/evaluation evidence or explicit approval. Discovery and execution permissions differ. Initial registry does not execute arbitrary code or auto-import remote skills. SQLite or file-backed persistence, deterministic validation and duplicate rejection.
3. **Workflow registry/executor:** immutable `(workflow_id, version)` definitions with typed bounded DAG steps, dependencies, inputs, permission requirements, retry/timeout policy, provenance. Validate unknown deps and cycles before publish; pin the version in each run. Initial executor only allows registered deterministic operations via an allowlist and checks policy before calls. Agent/LLM steps are explicit later adapters, never eval of arbitrary step text. Fail closed on unknown operation/permission. Persist run/step status and inputs/outputs so restarts can inspect; do not promise exactly-once side effects until idempotency is implemented.
4. **Agent Factory** follows validated skills/workflow contracts, not an ad hoc agent-spawning prompt. Forge, full approvals, durable governance, and release automation remain later phases; the full brief remains normative for them.

## Contracts and integration constraints

- Existing `ButlerService.handle_message(message, sender, context)` remains backward compatible. New thread context comes from a trusted request field, not agent-generated text. Scope memory by both owner and thread; never default a thread ID to another person's record. Expose create/list/get thread and message operations through Butler HTTP only after ownership validation.
- `SkillRegistry.register(spec)`, `get(skill_id, version)`, `list_versions(skill_id)`, `publish(skill_id, version, evidence)`: immutable published versions; reject unknown permission and executable references not in an allowlist. No direct network fetch/import on registration.
- `WorkflowRegistry.register(definition)`, `get(workflow_id, version)`, `publish(...)`; `WorkflowExecutor.run(workflow_id, version, inputs, actor)` resolves version once, validates inputs, policy and DAG, records transition/evidence, then executes named trusted operations. Default deny on unknown actor/capability. Recovery and compensation require a separate spec before claiming durability.
- Follow existing async Python 3.11/FastAPI/Pydantic patterns. SQLite tests use temporary files; no Docker, live model key, ATHENA dependency, or external skill repository needed. Never modify dirty `main` files from the other worktree.

## Acceptance gates for this slice

- `PYTHONPATH=src python -m pytest tests/ -q` passes in an installed environment, including restart persistence and two-thread/two-owner isolation tests.
- `ruff check src/agency tests` reports no newly introduced findings; inspect `git diff --check` and every agent-created file.
- A direct API/service smoke test creates a thread, records a turn, restarts a store, reads it back, and proves another owner cannot read it. Registry tests prove immutable versions and invalid DAG rejection.
- Update README/architecture/roadmap with honest implemented/partial/planned statuses. Do not label Forge, Agent Factory, or production isolation complete because prototypes exist.

## Sequence and research decision

A heavyweight external durable engine (e.g. Temporal) offers replay/timers but adds an operational dependency and would contradict the small local slice. Begin with bounded SQLite state and version-pinned declarative definitions; revisit a mature engine when crash recovery, distributed scheduling, and human approvals are actually exercised. Deterministic permission checks and identity must happen outside model prompts. Sources consulted for alternatives: https://sammyorangkhadivi.com/papers/from-copilots-to-controlled-digital-operations ; https://youngju.dev/blog/culture/2026-05-15-workflow-engines-2026-temporal-inngest-trigger-hatchet-restate-dbos-airflow-deep-dive.en ; https://agentpatterns.ai/instructions/shared-context-bundle-registry . These inform trade-offs, not implementation-status claims.

## Follow-on phases

Reconcile existing identity/policy/QA into versioned Agent Factory; persist Lattice governance votes and approvals; integrate native Forge coding agent and bounded software workflow; adversarial security tests and audited sandbox boundaries; then workspace/project UI, thread branching, external skill adaptation, distributed execution, competitive benchmarks. Each phase needs its own spec, checkpoint, tests and review. The consolidated brief's 51 sections remain target requirements rather than silently disappearing from scope.
