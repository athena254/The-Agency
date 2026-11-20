# Owner Registry / ToolDriver handoff — October 19, 2026

## Delivered bounded seam

Base f605811f657e3d3c58a466a177b3740de7ba5d2a, branch feat/owner-tools-20261019.
Only these four owned files change:

- src/agency/tools/registry.py
- src/agency/tools/driver.py
- tests/test_owner_tools.py (new)
- docs/HANDOFF_OWNER_TOOLS_20261019.md (new)

No service/API, metered-model, builtin, policy, budget or store source was modified. The parent's independent timeout repair is not part of this branch. This is a tested local seam, not a daily-driver readiness, provider qualification, independent security review or rollout claim.

## Contract for the dependent service

`ToolRegistry(..., owner_policy=OwnerToolPolicy())` and `build_default_registry(tools, owner_policy=..., audit=audit)` accept the concrete additive owner policy. Mixed beta/owner policies and nonconcrete/subclass owner policies are rejected. The owner factory preserves its supplied audit. Existing beta/legacy factory behavior and result/telemetry contracts remain covered by the focused regressions.

Supply the same concrete BetaAuditLog to the registry, MeteredModel and ToolContext; supply the exact issued OwnerExecutionGrant to ToolContext and OwnerMeteredBinding. ToolContext.task_id must equal grant.lease.task_id. Use a neutral agent label. Do not supply beta_principal, memory_store, sandbox_manager or lattice. Register only the three approved safe builtins (calculator, utc_time, web_search); this change does not register tools automatically.

The owner driver rejects open registries, fake/subclass registries/models/policies, copied or absent grants, mismatched audit/task and mixed/private handles before even a final-only model call. It detects an owner-bound model even when context omits its owner grant. Both regular and forced-final model calls supply only owner_grant, matching task_id and strict; the concrete model enforces the accepted context-key set. The beta binding union is explicitly narrowed before beta-principal access.

The registry uses the existing strict intent/outcome pipeline. A durable tool-attempt reservation with limit=grant.max_tool_calls precedes dispatch. It snapshots grant/policy/audit identity and grant restrictions, validates live durable task state after persistence awaits, and checks again inside the scheduled tool coroutine immediately before tool.run. Post-effect binding changes are typed uncertainty rather than success.

Owner audit entries use neutral agent=owner-tools and task=owner-pilot, a random call_id and bounded tool/phase/status/duration fields. They omit owner/subject/task identifiers, capabilities, caller arguments, query, output and free-form errors. Unknown tool names are redacted. Denial records remain best effort and never authorize effects.

Owner quota/live-binding blockers propagate as PilotError; audit failures use PilotError(audit), and tool cancellation/timeout/unhandled effect errors use PilotError(uncertainty, uncertain=True). Unacknowledged effect outcomes leave the durable intent pending; owner audit failures latch the registry closed. MeteredDenied/MeteredError and PilotError are not converted into retry observations or model-written completed fallbacks. Known unsuccessful owner tool results also terminate the driver instead of prompting a fabricated success. The service must preserve these typed failures and perform its own task finalization/uncertainty handling.

## Actual qualification

Evidence: C:/Users/alphi/agency-evidence/limited-autonomy-20261019/owner-tools/.
Interpreter: C:/Users/alphi/theagency/.venv/Scripts/python.exe (Python 3.11.16, Windows).

- **48 owner tests passed**, owner-final-corrected.txt.
- **355 focused tests passed**, qualified-focused.txt. This includes the 48 owner tests, not 355 plus 48. Files: test_owner_tools.py, test_tools_base.py, test_tools_driver.py, test_beta_tool_boundary.py, test_safe_local_tools.py, test_owner_metering.py, test_owner_policy.py, test_owner_budget.py.
- Root Ruff lint passed, qualified-ruff-root.txt.
- Root Ruff format check passed: 264 files already formatted, qualified-format-root.txt.
- Strict affected mypy passed for both win32 and Linux targets: 2 source files, --strict --follow-imports=silent. These are target-platform checks on Windows, not native Linux execution.
- Separate source-bound cold imports of registry and driver passed; neither imported agency.admission.service.
- git diff --check passed before committing.

The owner tests use real SQLite OwnerPilotStore/BetaBudgetStore, OwnerBudgetAdapter, live grants, concrete BetaAuditLog, LLMAdapter, MeteredModel, ToolRegistry and ToolDriver. Provider/search responses are synthetic at the actual HTTPX MockTransport leaf only; adversarial wrappers and persistence/effect faults are explicit denial/fault probes. Tests cover direct/factory limits, effective search defaults, schema and unknown denial, neutral redacted audit, after-reservation/intent/outcome revocation and cancellation/binding changes, exact-type/final-only bypass denial, normal/forced-final contexts, model quota at forced final, audit acknowledgements/failures and tool cancellation/timeout/exception/known-failure behavior.

The external qualification runner sets PYTHON_DOTENV_DISABLED=1 and explicit echo defaults before Agency imports, strips credentials, binds absolute own source/tests, uses scratch CWD/data/temp, rejects non-scratch database/.env access and blocks external Python socket/DNS traffic. No real provider was contacted. This Python guard is not an OS/subprocess sandbox.

## Retained failed/incomplete evidence

Vertical RED/GREEN receipts are retained. red-01 demonstrates missing owner constructor/factory behavior; green-01-corrected has 2 passes. red-02 has 18 recheck failures before implementation; green-02-last-checkpoint has 20 passes. red-03-driver-real has missing owner-context failures; green-03-driver has 23 passes. red-04-final-binding has 10 bypass failures; green-04-final-binding has 36 passes. red-05-typed has 8 failure-propagation failures; green-05-typed has 45 passes.

Earlier infrastructure timeouts stalled in Python/pytest importlib before collection; they are not product-deadlock evidence or green runs. The frontend also temporarily rejected file stat/read/write and terminal calls, requiring direct Python filesystem/subprocess fallback within the owned paths. An approximate patch damaged a test line; it was restored before obtaining behavioral RED. A calculator-output assertion and a missing test json import were corrected as test authoring errors, not product defects.

The first combined run recorded 1 failed / 351 passed because the external qualification runner lacked a __main__ guard and Windows multiprocessing re-entered pytest instead of completing child bootstrap. The runner guard was corrected outside the checkout, without extending any product/worker deadlines. The corrected combined runs passed 352 tests and then the final 355-test selection. All raw failed/incomplete receipts remain separate from the final qualified receipts.

## Remaining gates / limitations

Parent integration and service/API wiring remain separate work. No full suite, native Linux/other-Python matrix, live provider, daemon, deployment, push or merge was attempted. Provider funding/consent remains unresolved; no paid/private fallback, beta opening, governance expansion or privileged context access was introduced. This local Python boundary is not a sandbox against arbitrary trusted-process monkeypatching or an OS administrator. Parent must review/cherry-pick this explicit checkpoint and retain task-level uncertainty handling before starting dependent live qualification.
