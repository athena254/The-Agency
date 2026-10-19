# F1 owner foundation handoff — October 19, 2026

Base: `573b484c60a25f9050e2eb7384ebf5410fa6deea`. Ownership is limited to the five new `agency/admission` modules, three new owner foundation test files, and this document. No legacy source, provider configuration, canonical runtime database, daemon, API, or permission policy was changed. Parent integrates runtime next; this foundation alone enables no ingress or provider execution.

## Public contract

Public dataclasses/wire models are in `agency.admission.models`, including **all** pilot-request columns on frozen `AdmissionRecord`, plus `replayed: bool = False`. Epoch DB times are floats; counters/sequence are ints; `input_json` and nullable `result_json` are canonical JSON strings. Request capabilities are process-local, random, omitted from repr, and absent from wire models. No `BetaPrincipal` is constructed.

```python
OwnerPrincipal(owner_id: str, transport: Literal['local', 'telegram'], subject_id: str)
RequestLease(principal: OwnerPrincipal, request_id: str, task_id: str,
             sequence: int, quota_subject_id: int, capability: str, deadline_at: float)
OwnerExecutionGrant(lease: RequestLease, budget: OwnerBudgetAdapter,
                    allowed_tools: frozenset[str] = frozenset({'calculator', 'utc_time', 'web_search'}),
                    max_tool_calls: int = 4, max_model_calls: int = 8)
OwnerMeteredBinding(grant: OwnerExecutionGrant, provider: str, model: str, provider_config_id: str)
PilotError(code: str, status_code: int = 503, uncertain: bool = False)

OwnerAuthenticator(*, owner_id: str, subject_id: str, bearer_secret: SecretStr,
                   telegram_user_id: int | None = None,
                   telegram_polling_marker: object | None = None)
def authenticate_local(self, authorization: str | None) -> OwnerPrincipal
def authenticate_telegram(self, *, user_id: int, chat_id: int, chat_type: str,
                          update_id: int, polling_marker: object) -> OwnerPrincipal

OwnerPilotStore(db_path: str)
async def initialize(self) -> None
async def close(self) -> None
async def ensure_workspace(self, principal: OwnerPrincipal) -> str
async def admit(self, principal: OwnerPrincipal, request_id: str, *,
                operation: str, payload: dict[str, Any]) -> AdmissionRecord
async def get(self, principal: OwnerPrincipal, task_id: str) -> AdmissionRecord
async def get_request(self, principal: OwnerPrincipal, request_id: str) -> AdmissionRecord
async def list_tasks(self, principal: OwnerPrincipal, *, limit: int = 50) -> list[AdmissionRecord]
async def transition(self, sequence: int, *, expected: str, state: str, phase: str,
                     error_code: str | None = None) -> None
async def stage_result(self, sequence: int, result: dict[str, Any]) -> None
async def commit_turn(self, sequence: int, *, text: str,
                      assessment: ExecutionAssessment | None) -> None
async def request_cancel(self, principal: OwnerPrincipal, task_id: str,
                         request_id: str) -> AdmissionRecord
async def recover_interrupted(self) -> list[AdmissionRecord]
async def reserve_attempt(self, sequence: int, *, kind: Literal['model', 'tool'], limit: int) -> bool
async def create_thread(self, principal: OwnerPrincipal, request_id: str, title: str,
                        parent_thread_id: str | None = None) -> tuple[RequestReceipt, ThreadView]
async def list_threads(self, principal: OwnerPrincipal) -> list[ThreadView]
async def list_messages(self, principal: OwnerPrincipal, thread_id: str) -> list[MessageView]

OwnerBudgetAdapter(backend: BetaBudgetStore, store: OwnerPilotStore, *, namespace: str = 'owner-pilot:v1')
async def reserve_request(self, principal: OwnerPrincipal, request_id: str, task_id: str) -> RequestLease
async def mark_running(self, lease: RequestLease) -> None
async def reserve_model_call(self, lease: RequestLease) -> bool
async def reserve_tool_call(self, lease: RequestLease, tool: str) -> bool
async def finish_request(self, lease: RequestLease, *, success: bool) -> None
def owns(self, lease: RequestLease) -> bool
def revoke(self, lease: RequestLease) -> None
async def close(self) -> None
```

Wire classes exactly: `PilotUsage`, `PilotLimits`, `PilotCapabilities`, `ThreadView`, `MessageView`, `RequestReceipt`, `OwnerTaskRequest`, `OwnerTaskView`, `OwnerError`. They forbid extras and coercion. Full inspected signatures/field manifests are retained in external evidence `foundation/public-signatures.json`.

## Integration sequence and invariants

1. Initialize the concrete quota backend and owner store with explicit isolated paths. The store reuses the existing `ThreadStore` schema, takes the existing `RecoverableLock` for its lifetime, verifies WAL and FULL on every connection, and closes per-operation connections. `initialize()` is idempotent while open; a closed instance is not reopened. A second live instance refuses startup without changing requests.
2. Under that lifetime lock, call `recover_interrupted()` before scheduling new work. It marks every interrupted ADMITTED/RUNNING turn UNCERTAIN, retaining private staging and stable IDs; it never resumes. `initialize()` deliberately does not run recovery implicitly against its own live tasks.
3. `create_thread()` implements operation `new_thread`; `request_cancel()` implements operation `cancel`; direct `admit()` accepts operation `turn` with **only** `{thread_id, message}`. Owner/request keys span all operations. Replays preserve identifiers/content, compare both canonical bytes and digest, and changed operation/body conflicts. No idempotency retention/pruning follows the backend's 48-hour quota pruning.
4. Only the original submission path should call `reserve_request()`; replay handlers return the existing task. One fresh in-process admission has one atomic, irrevocable private claim. Duplicate admission reads cannot sabotage that original claim, concurrent reservations mint exactly one lease, and duplicate reservations/restarted stores cannot mint another. There is no cross-store transaction claim. Failed/cancelled reservation work is not refunded or reclaimed.
5. `budget.mark_running(lease)` moves BOTH quota reservation and pilot row to RUNNING. Do not first transition the pilot row separately. Model attempts increment durably in the pilot store **before** backend authorization; backend denial retains the conservative pilot count. All three permitted tool names use durable attempts before effects. Hard caps are eight model attempts/four tool attempts; runtime must enforce any lower grant/configuration ceilings too.
6. Stage EXACTLY `{'task_id': admitted_task_id, 'response': exact_text, 'assessment': assessment.model_dump(mode='json') or None}`. Staging leaves RUNNING and publishes no messages. Task ID, response bytes, assessment task/output binding, canonical result bytes and digest must all match at `commit_turn`. The response limit is 65,536 UTF-8 bytes, including valid full-limit Unicode/JSON-escaped content; a bounded private 512-KiB canonical envelope permits escaping/assessment overhead. Input JSON is bounded to 32 KiB.
7. After required gateway result audit acknowledgement and successful `budget.finish_request(...)`, call `store.commit_turn(...)`. Budget finish revokes the lease but does NOT declare task success. Commit atomically writes COMPLETED, updates the thread and inserts exactly one user/assistant pair using IDs allocated at admission. SQL/message/event failure rolls the transaction back. Terminal success cannot be overwritten or cancelled.
8. Cancellation commands have their OWN row with `task_id=NULL`, `resource_id=target_task_id`. `request_cancel` returns the TARGET turn with replay status from the command, while `get_request(cancel_key)` returns the command row. Undispatched ADMITTED turns become CANCELLED. Running turns retain RUNNING plus a durable cancel flag until service interruption/finalization; they immediately lose capability liveness and cannot reserve or publish. Service must drain its actual owned execution task and conservatively transition interrupted dispatched work to UNCERTAIN. This foundation does not acknowledge/clear pending audit intent.
9. Budget/store awaited workers are shielded and drained on cancellation; locks remain held until work actually finishes. Leases reject clones, subclasses, mismatched fields, expiry, revocation, cancellation, terminal state and store close. `budget.close()` revokes immediately, drains its operations and closes its injected quota backend; it does not close the owner store. Close any separate runtime/context `ThreadStore` owned by the integration itself.

Store transition states: ADMITTED -> RUNNING/FAILED/DENIED/CANCELLED/UNCERTAIN; RUNNING -> FAILED/UNCERTAIN. COMPLETED is available only through atomic publication. Lifecycle phases accepted: `admitted`, `reserved`, `running`, `dispatch`, `request`, `result`, `staged`, `committed`, `finished`, `failed`, `denied`, `cancelled`, `uncertain`, `recovery`. Fixed error codes: `unauthorized`, `forbidden`, `not_found`, `conflict`, `too_large`, `media`, `invalid_request`, `quota`, `disabled`, `audit`, `store`, `uncertainty`. Unknown exception strings/payloads are not persisted as lifecycle events. The reserved nullable `provider_config_id` column is exposed but remains NULL in this foundation; provider snapshot/binding is runtime ownership.

## Qualification and evidence

Final scoped qualification: **32 tests passed** on Windows Python 3.11.16 with warnings treated as errors; owned-module statement coverage **91%**. Ruff lint passed; all eight owned Python files passed formatter check; strict mypy passed for both win32 and Linux targets (five owned source files each). Five separate cold-import probes passed under the explicitly disabled-dotenv containment. JUnit: `foundation/final-focused.xml`; logs: `final-focused.log`, `final-lint.log`, `final-format.log`, `final-mypy-win.log`, `final-mypy-linux.log`, `cold-imports-corrected.log`.

All results are retained in external directory `C:/Users/alphi/agency-evidence/limited-autonomy-20261019/foundation/`. Raw RED/GREEN, initial failures, corrected harness probes, lint/typecheck and cold-import logs are preserved; no failing evidence was overwritten as a pass.

- Behavioral TDD slices cover authentication, closed wire models, exclusive stores/atomic thread replay, request admission/columns, exact staged publication/SQL rollback, cancellation/recovery/attempt ceilings, neutral quota reservations, conservative attempts, restricted grants, cancellation/revocation races, replay/restart claims, owner rechecks, full-limit output byte binding and duplicate-reservation concurrency.
- Real SQLite and concrete `BetaBudgetStore`; no model/network fake or provider call. Crash evidence uses a real owned worker process and kernel lock release, not a fabricated recovery response. Native execution is Windows Python 3.11.16. Strict mypy is separately targeted at win32 and Linux on only the five owned modules (`--follow-imports=silent`); this is not Linux runtime execution or a full integration suite.
- Scratch CWD/data/tmp and explicit echo/absolute worker PYTHONPATH are used. Final qualification additionally sets **`PYTHON_DOTENV_DISABLED=1` before any Agency imports**, strips credential environment variables, and cold probes reject all network connects, `.env` opens and runtime-database opens.
- Initial cold-import audit found the pre-existing `agency.agents` initializer imports the legacy LLM adapter, whose eager `load_dotenv()` searches ancestor configuration. The audit blocked the attempted open. Earlier preliminary tests selected echo and stripped inherited credentials but did not yet disable this upstream dotenv search; do not treat those preliminary runs as sealed configuration-IO evidence. The final contained run supersedes them. No secret contents are in the evidence. No out-of-scope production workaround or environment mutation was added; runtime integration should address/contain this existing import side effect.
- The first temporary-directory probe had a missing basetemp parent. A crash-test attempt initially killed only the Windows venv redirector, leaving the actual worker live until cleanup; it correctly refused the replacement lock. The corrected test records/kills the actual worker PID, drains its launcher/pipes, and has scoped exceptional tree cleanup. Both original failures remain recorded.

## Deviations and remaining gates

No public name, constructor, method signature, wire field or staging/cancellation contract was substituted. Refinements above expose the bounded internal phases/error vocabulary, one-claim semantics and backend ownership so runtime does not guess. No added platform, policy permission, beta opening, network fetch, provider default/fallback or production stub was introduced.

Parent still owns source integration, independent review, runtime/API/UI route tests, full-suite qualification, effective provider freezing/audit integration and any authorized live rollout. No push, merge, daemon or production-readiness claim is part of F1.
