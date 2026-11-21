# Owner service handoff — October 20, 2026

## Delivered scope

Branch `feat/owner-service-20261020`, base `b0f2f1ef25dafa1ccbcde6445188640b8c8cb017`.
Only the five authorized files are included in the candidate commit:

- `src/agency/admission/service.py` (new)
- `src/agency/butler/service.py` (additive owner helper)
- `src/agency/orchestrator.py` (additive owner helper)
- `tests/test_owner_service.py` (new)
- this handoff (new)

The public constructor and async service methods match the frozen pilot/service specifications: `start`, `stop`, `submit`, `get_task`, `list_tasks`, `cancel`, `capabilities`, `create_thread`, `list_threads`, `list_messages`. The explicit helpers are `ButlerService.handle_owner_message(message, thread_id, *, grant, model, registry)` and `AgencyOrchestrator.execute_owner_request(message, *, grant, model, registry, memory_context='')`.

## Execution and failure behavior

- Uses the concrete delivered stores, quota adapter, durable audit, Butler, orchestrator, MeteredModel, owner policy, Registry and ToolDriver. Registers only CalculatorTool, UtcTimeTool and WebSearchTool. No second assistant or legacy fallback.
- Requires an already injected ThreadStore at the same resolved protected database path. The configured owner and optional workspace are checked before resource access. Workspace selection is bound to the actual durable owner workspace.
- Captures a private validated provider dispatch snapshot during construction and snapshots the request/principal before admission awaits. Execution uses the durable admitted payload. Provider configuration IDs are random, not credential-derived; no provenance SQL was added.
- Serializes admission/lifecycle operations, retains and drains cancelled admission/cancel/stop waiters, and tracks one execution. Replays do not remint a lease or rerun; changed bodies conflict. Stop synchronously revokes the active lease before waiting for the admission lock, then cancels and drains the actual child.
- Preserves typed MeteredDenied/MeteredError/PilotError through both helpers. Failure disposition always inspects pending audit, including a real `forbidden` error after tool intent. Unknown/pending outcomes remain private and block subsequent execution; inspection/disposition failures latch dispatch closed without disabling available read-only history.
- Checks durable uncertainty independently of history pagination and checks pending audit at admission, before dispatch and before publication. Startup recovers interrupted rows without resuming them or clearing pending intents.
- Does not start or stop the injected Butler/orchestrator, seed agents, acquire Lattice, route/plan through legacy execution, mirror memory, or close caller-owned ThreadStore/unrelated stores. ThreadContextEngine uses `local_only=True`, serialized explicitly untrusted committed conversation only.
- Expected task identity and request body are read independently from admission rather than taken from an assessment or unchecked lease ID. Uses the existing task/body-bound `build_assessment` / `unchecked_subtask('tool_loop', path='tool', ...)`: wire verification is `not_performed`, check reason `tool_path_unchecked`, independently_verified=false.
- Publication ordering is stage -> required sanitized gateway audit acknowledgement -> quota finish while the pilot row is RUNNING -> atomic transcript/result commit. Pending audit is rechecked before commit. Cancellation after an already committed publication reads the durable COMPLETED state; it never attempts rollback.
- Preserves the original created_at+120s task deadline, validates provider timeout <=45s, enforces <=8 model/<=4 tool attempts, and uses lowered actual BudgetLimits. No retries, timeout increases, paid fallback or environment authority.

## Actual verification

External evidence root: `C:/Users/alphi/agency-evidence/limited-autonomy-20261019/owner-service/`.
Final source/test fingerprint: `c9b492f8ea9a3f3879bc221b9a3e3291c976e32e0dad058d69939f4fc0614e87`.

- `red01`: **one failing test inside its body**, asserting the missing owner service, before any production edit.
- `green01b`: **one passing real calculator tracer**: two HTTPX MockTransport provider responses, actual durable COMPLETED, exact request/output/unchecked assessment, one transcript pair. The preceding `green01` failed on the existing assessment schema's fixed `tool_loop` unit ID; that evidence is preserved, not relabeled as a pass.
- Subsequent observed vertical RED/GREEN receipts cover single-execution/replay, unknown provider outcome, durable recovery/readiness, actual cancellation/drain, cancelled admission snapshots, shutdown ownership, gateway acknowledgement failure, original admitted deadline, lowered quota, audit-inspection outage, cancelled cancel waiter, startup rollback, pending gateway audit, post-admission uncertainty, workspace binding, shutdown inspection failure and stop-before-lock dispatch revocation. Supporting tests additionally cover final-only thread isolation, foreign owner/thread denial, forbidden-after-intent uncertainty, publication order, quota/commit faults, and post-commit cancellation immutability.
- `green19`: **24 service tests passed**, zero failures/errors/skips, in 16.25s (before final formatting).
- `focused02`: **354 focused tests passed**, zero failures/errors/skips, **one inherited Starlette/httpx TestClient deprecation warning**, in 88.06s. Includes all 24 final service tests and affected Butler/orchestrator/thread/context, owner tools/runtime/metering, model metering, legacy Driver and assessment/gateway/context consumers. This is not a full-suite result.
- `lint04`: root-discovered Ruff check of `src/` and `tests/` passed.
- `fmt03`: root-discovered Ruff format check passed, 267 files already formatted.
- `mypy05` and `mypy06`: strict affected-source mypy clean for `win32` and `linux`, three source files each. Linux-target type checking ran on Windows, not a Linux runtime test.
- `import2-service`, `import2-orchestrator`, `import2-butler`, `import2-driver`: separate fresh-interpreter cold imports all passed.
- Final qualification receipts have identical before/after source/test fingerprints. The receipts record the base HEAD because qualification preceded the candidate commit; the external commit seal binds the committed owned files to these exact tested working bytes.

Qualification uses the project interpreter read-only, absolute own source/tests, credential-stripped environment, explicit echo defaults and PYTHON_DOTENV_DISABLED before imports, scratch CWD/data/temp, external Python socket/DNS denial and non-scratch SQLite/.env-open guards. Only provider HTTP leaves and explicit persistence faults are replaced. No live provider, canonical private configuration/data, install, daemon, push, merge or deployment was used. This is Python-level containment, not an OS sandbox.

An initial explicit `ruff --config <absolute pyproject>` invocation changed Ruff's source-root inference to scratch and produced unrelated first-party import-sort diagnostics. Effective settings were compared against true root-discovered settings; the runner was corrected to normal source-root discovery, leaving shared files untouched. Failed lint/type checks and intentionally fingerprint-changing formatter receipts remain preserved.

## Limits and parent gates

- Ready/provider_ready describe the initialized service and explicit supported snapshot, **not successful live inference**. Parent review, integration, API/launcher/browser and authorized live-provider qualification remain separate gates. Runtime provider selection remains unresolved here; no private fallback was introduced.
- Service-owned store/budget objects are not reopenable after stop or failed startup. Repeated start while ready and repeated stop are safe; restarting a closed service fails closed. Construct fresh store/budget/audit/service objects against the same protected files for restart/recovery; keep caller ownership of Butler/ThreadStore explicit.
- Recovery was exercised through concrete interrupted durable rows and reopening protected stores. No new abrupt OS-process-kill test or OS-sandbox guarantee is claimed for this slice.
- Audit, quota and pilot stores are separate durability domains, not a cross-store transaction. A quota-finished but uncommitted staged output remains private/UNCERTAIN; there is no automatic retry, resume, reconciliation or uncertainty clearance.
- The capability/read-only boundary trusts concrete in-process OwnerPrincipal supplied by a separately configured authenticator. No authenticator/API transport is manufactured by this service.
