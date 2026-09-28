> ARCHIVED DRAFT — preserved for completeness, not an approved implementation contract.
> Historical platform/status claims below may be superseded. See ../../COMET_LAB_VERIFIED_STATUS.md
> and ../../FREE_PROGRAM_BUILD_STATUS.md for current verified status.

# SPEC: Human-Only Lattice Gateway and Application Contract

Status: REVIEW / CONTRACT ONLY. No runtime, source, schema or test changes ship with this
document; no engine, agent or validator code is written here. Synthetic-only, non-actuating,
no production deployment. This is contract work, not proof of completion. The engine slice
(milestone delivery order step 1) must land before anything below is built.

## 1. CHECK map (provenance of this contract)

Read (3 files):
- `docs/SPEC_SINGLE_HOST_LATTICE_MILESTONE.md` — product boundary, delivery order 1-4, lab interface.
- `src/agency/butler/service.py:210-307` — `handle_message` boundary facts.
- `src/agency/orchestrator.py:433-560` — `execute_task` boundary facts.

EXPLICITLY NOT INSPECTED — no claim is made about any of these:
- `orchestrator.py` lines 1-399 and 561-end; lines 476-480 were elided by the read window.
- `butler/service.py` outside 210-307: `execute`, `route`, `_recall_history`, `_store_turn`,
  `_audit_append`, `handle_isolated_message`, config; also `butler/server.py`, `gateways/butler_only.py`.
- `src/agency/agents/*`, `src/agency/lab/*`, tools, memory, policy, registry, `telegram/handler.py`,
  task manager, executor, verifier, planner, KV store, any ABCI application, all other docs.
- Not read by rule: `.env`, `data/`, chat logs, raw transcripts, credentials, private docs.

## 2. Facts recorded from the inspected regions

Butler `handle_message(message, sender, context, *, memory_enabled=True, beta_principal=None) -> str`
- rejects `beta_principal`: `TypeError` on wrong type, `RuntimeError("beta execution disabled: policy,
  budget and audit gates missing")` before startup, routing or persistence;
- `sender` is a plain string label; identity can only arrive through trusted in-process admission;
- caller `context` is data, not identity: `beta_principal` and `memory_context` are stripped from it;
- thread read is authorized (`thread_store.get_thread(sender, thread_id)`) before recall/execute;
- `route(text, merged)` picks an agent, then `execute(...)` runs behind `asyncio.wait_for`;
- outcome is one of `completed|timeout|failed`; failure text is generic, so this path has no
  machine-readable status for the caller; Butler returns rendered text only.

Orchestrator `execute_task(task_id, context=None, *, beta_principal=None) -> ExecutionResult`
- same typed refusal for `beta_principal`; the docstring states this registry is legacy-open;
- `task.created_by` is an *agent* id, not a human identity; the human `sender` never reaches this layer;
- plan = `planner.plan(description)`; tool-capable domains run `tool_driver.run(...)` and record
  `verification="passed"` and `risk_category="none"` as literals;
- classic path executes planner subtasks, then `verifier.verify(criteria=[...])` yields a score used
  as confidence on a `Finding`;
- nothing inspected checks a committed decision, input/operation digest, dedup key, lease or
  validator signature before status change or execution.

## 3. Responsibility map: human gateway vs Lattice peers

| Concern | Current (inspected) | Target owner |
| --- | --- | --- |
| human admission | `sender` string + trusted in-process caller | Butler; holds a `HumanRef`, no key |
| presentation | `handle_message` -> `str`, audit prefixes | Butler |
| routing / agent choice | `butler.route` over seeded agents | replaceable peer/coordinator role, not Butler |
| planning | `_planner.plan` inside executor | peers, under committed deterministic policy |
| authorization | only the dormant beta gate | each validator, per committed policy state |
| task identity | `task.created_by` = agent id | `task_id` + committed `requester: HumanRef` |
| execution | executor / `tool_driver.run` | peer executor under committed decision + fence |
| evidence | `Finding` + confidence score | peers; model claims labelled advisory |
| memory | `thread_store` / per-sender recall | Butler-local convenience, never an authorization gate |
| validator keys | absent from both inspected paths | peers only; Butler must never hold one |

Butler has no goals, vote, planning authority, validator key or unilateral execution permission.
A supervisor may start/restart processes only; scheduling/coordination roles stay replaceable.

## 4. Exact schemas (new ABCI application layer, synthetic-only)

```
HumanRef      = { human_id: str, fixture: "synthetic", consent_ref: ConsentRef|null }
AffectedInput = { input_id: str, kind: "file"|"kv"|"external", digest: Bytes32|null,
                  availability: "present"|"missing", consent_ref: ConsentRef|null }
TaskRequest   = { request_id: str, requester: HumanRef, intent: str,
                  affected_inputs: [AffectedInput], required_roles: [Role],
                  budget: { wall_ms: int, llm_calls: int }, synthetic: true }
Proposal      = { proposal_id: str, author: PeerId, kind: "task_request",
                  body: TaskRequest, created_at: int, nonce: bytes }
Envelope      = { proposal_digest: Bytes32, signatures: [PeerSignature], quorum: QuorumRef }
Task          = { task_id: str, request_digest: Bytes32, requester: HumanRef,
                  state: TaskState, input_commitment: Bytes32, epoch: int }
Assignment    = { assignment_id: str, task_id: str, peer: PeerId, role: Role,
                  lease_epoch: int, expires_at: int, coordinator_sig: PeerSignature }
EvidenceRef   = { kind: str, digest: Bytes32, uri: str|null }
Claim         = { text: str, model: str, confidence: float }   # advisory, never a gate
Result        = { result_id: str, task_id: str, assignment_id: str, input_digest: Bytes32,
                  operation_digest: Bytes32, outcome: "completed"|"failed"|"rejected",
                  evidence: [EvidenceRef], model_claims: [Claim], produced_at: int,
                  peer_sig: PeerSignature }
```

Digests are SHA-256 over canonical CBOR of resolved bytes. All heights/times inside validation
come from the engine, never from wall-clock or environment reads. Deterministic store keys:
`task/<task_id>`, `assign/<task_id>`, `result/<result_id>`, `dedup/<human_id>:<request_id>`,
`fence/<assignment_id>`, `consent/<consent_ref>`.
