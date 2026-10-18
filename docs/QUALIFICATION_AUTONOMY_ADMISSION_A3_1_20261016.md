# A3.1 gateway audit repair — local qualification

Date: October 16, 2026 (UTC; local log timestamps crossed into October 17).
Status: **Implemented and locally offline-qualified. Full autonomy is NOT ready.**

## Plain-English result

The gateway no longer ignores a required audit failure. An unavailable preflight stops routing/execution/command work. An unavailable final acknowledgement blocks a successful reply and conversation persistence. Browser chat returns a fixed failure response; Telegram can deliver a fixed failure notice without making its legacy poller rerun work merely because execution was not successful.

An audit append can commit and then raise. Tests preserve that uncertainty and prove there is no automatic append retry. This repair does not roll back completed provider calls, earlier execution memory, evidence or command mutations. It does not create durable autonomous admission, independent permission or recovery.

## Revision and approvals

- Frozen investigation/contract: `622e41b432df7a824e928c4c57543c3cd1bbf37f`.
- Direct offline implementation approval: recorded at `f7303dc031d156ce2174fa5a6c78f79659e77032`.
- Implementation and exact tested revision: `418ab8e93a3f2ddab2dcd3ab0ff60a4473057479`.
- Source Git tree: `5e24627025f00fe6a5e775e297f80c3fc1175ed1`.
- Tests Git tree: `713bfd75b939b913113d3b4cd1c424e87497022f`.
- Stable final raw source/test fingerprint: `a3ce25ccf664310b719a1823e739f9899226146012a227f794e4649f0e537c50`.
- Branch/worktree: `docs/autonomy-admission-a3-20261016`, `C:/Users/alphi/agency-worktrees/autonomy-admission-a3-20261016/`.

The owner separately approved one fixture-only scope amendment in `test_context_and_sender_cannot_mint_principal`, `tests/test_beta_principal_hop.py`: initialize/close its actual audit store, leaving its principal-forgery assertion unchanged. The original full-suite failure is retained. No other pre-existing test was modified and no beta denial was relaxed.

A later documentation-only commit may cite this qualification only with identical source/tests trees. The current assistant implemented and self-reviewed directly. No delegation/auxiliary model was used; this is not independent review.

## Changed behavior

- Added a fixed-message `ButlerAuditError`; required appends acknowledge the exact entry ID, not merely a truthy value. None/False/True/objects/foreign IDs fail. Exception chains are suppressed for this fixed error; gateway logging does not display storage bodies/tracebacks.
- Added sanitized required request, route and direct-execution preflights. Typed beta denial and explicit owner/thread authorization retain precedence. No new model request was added.
- Made routed final audit precede conversation/thread persistence. Existing optional conversation memory remains best effort; earlier orchestrator effects are separate stores.
- Audit/metered denial/uncertainty errors propagate before generic execution rendering or legacy routing fallback. Cancellation remains cancellation.
- Gateway audit records omit raw message/output/sender/task-title data. Command records use known codes or `unknown`, not arbitrary unknown slash-command tokens. Relevant gateway error logs use exception class only.
- Browser chat catches audit failure and returns the fixed HTTP 503 notice, without output/assessment. Its generic error handler also omits tracebacks/request locals for propagated metered failures while preserving its existing fixed HTTP 500 body. Host/origin/CSRF and isolated-only dispatch remain unchanged.
- Telegram normal chat/private thread commands deliver one fixed audit-failure notice per handling attempt and return terminal handling rejection after successful delivery. Legacy `process_message` returns that notice without demo/model fallback. Its string-only API is not an autonomous admission/result contract.
- Real nonbeta poller tests distinguish successful failure-notice delivery (offset advances; duplicate poll does not execute again) from failed delivery (offset stays pending). Restart/failed-send retries still need durable task/outbox recovery; exactly-once execution/delivery is not claimed.

Runtime ownership stayed within `butler/service.py`, `prototype/app.py`, and `telegram/handler.py`. No budget/audit schema, model adapter, UI source, orchestrator, tool registry, dependency or provider configuration was changed.

## Final gates on 418ab8e

The final full suite ran October 16, 2026, **21:57:14–22:02:17 UTC**:

- Python pytest: **1,870 passed, zero failures/errors/skips**, parsed from final JUnit.
- New A3.1 tests: **76 cases**, counted from that JUnit across the two new files.
- Ruff lint: passed.
- Ruff format: passed; 246 files already formatted.
- Strict mypy: passed over 151 source files.
- Node UI: **16 passed**; existing byte-bound status/UI safety tests retained.
- Five fresh cold imports passed: `agency.tools.driver`, `agency.llm.metered`, `agency.telegram.handler`, `telegram_bot`, `agency.agents.assessment`.
- One existing Starlette/httpx deprecation warning remains visible. Resource/thread/unraisable warnings were errors in pytest.
- All ten final receipts identify the exact tested commit and the same before/after source/test fingerprint. No source/test mutation occurred during final gates.

`research-evidence/autonomy-a3-1-qualification.json` records exact commands, start/end times, containment flags, final receipts, iteration outcomes and hashes of retained logs/JUnit/harness artifacts. Full raw evidence remains at `C:/Users/alphi/agency-evidence/autonomy-admission-a3-20261016/`.

## Acceptance mapping

- GA-01/02: actual service preflight denial across routed/isolated/direct route/direct execute/command entrypoints, with routing/model/recall/transcript/command/task-count assertions.
- GA-03/04/05: phase-specific failures before and after an actual committed append; no later work, returned success, conversation save or repeated command/append; direct execute guards and terminal audit preserved.
- GA-06: invalid acknowledgements rejected, valid exact IDs accepted through real file-backed store and reopened-reader checks.
- GA-07: actual in-flight cancellation across seven audit phases, including post-commit uncertainty; no success conversion or retry.
- GA-08: command preflight prevents mutation, post-mutation failure preserves the created state rather than pretending rollback.
- GA-09: actual prototype requests observe fixed non-2xx/no assessment; existing guard/isolation tests pass in the full suite.
- GA-10: actual Telegram chat/thread/legacy-message and nonbeta poller paths; one delivered notice, cursor advance/duplicate exclusion, failed-send cursor retained; existing beta regressions pass.
- GA-11: blocking audit/metered errors across routing/execution/isolated execution do not become fallback execution or successful replies. Propagated prototype metered errors retain a fixed non-success response without trace logging.
- GA-12: synthetic input/output/sender/error markers absent from repaired audit/error boundaries, including late failure, reopened records and arbitrary unknown command tokens. This is not a whole-application privacy audit.
- GA-13: existing successful routed/isolated/thread/command paths and all A1/A2 regression cases pass. Exact report/body bindings and honest not-performed status remain.
- GA-14: full local quality gates above pass; independent review and CI remain separate and unperformed.

## Failed attempts preserved

Behavioral RED/GREEN pairs cover exception propagation, invalid acknowledgements, preflight side effects, final-audit ordering, blocking typed errors, audit/log privacy, prototype response handling, Telegram handling, generic prototype error privacy and unknown command-token redaction. Control tests additionally exercise actual store commits, cancellation, reopened reads and poller behavior.

The first attempted final-order test append was blocked by stale-read protection; only the existing eleven cases ran and passed. It is **not** counted as the new behavior's RED. `a31-final-order-red-valid` contains the actual two failing ordering regressions.

The first nonbeta poller control spied on the beta-only handoff rather than the actual nonbeta `handle_update` call. Four control assertions failed; the spy was corrected, production was not changed for that mistake, and the original result remains.

The first full integration run completed with one uninitialized legacy audit-fixture failure and 1,868 passes. Its outer execute_code wrapper timed out after 300 seconds, but the underlying runner completed and wrote its genuine receipt/JUnit at 315 seconds. Those files were recovered rather than inventing an outcome or rerunning an overlapping process. The owner-approved fixture repair preceded the final passing suite. Formatting diagnostics and failed patch/harness attempts were not represented as feature regressions.

## Containment and remaining gaps

Tests used the discovered project Python 3.11.16 interpreter as a read-only toolchain, explicit isolated source/test paths, scratch CWD/data/temp paths, no checkout dotenv, removed provider environment secrets and explicit echo selection. Copied Python socket guards matched the preserved guard hash. The guard permits loopback for Windows asyncio and **is not an OS/native-child sandbox**. Node tests ran outside that Python guard. No real provider or Telegram transport was exercised.

Canonical main remained clean at `713a4e311b1e071fbd6c5cf103b995d17aa7e5a2`. No live bot stop/start, deployment, merge, push, beta enablement, CLI worker or paid fallback occurred.

Remaining A3 gaps include mutable effective provider/endpoint/credential/request-limit binding, platform-neutral authenticated admission, durable shared attempt accounting and direct research/SDK/bridge consumers. A4 independent peer authority, A5 journal/recovery/outbox/fencing, A6 an actual useful independently checked task, A7 independent review/CI/fault tests/authorized real-model demonstration and separate rollout are still open.

Required ordinary audit acknowledgement is not a durable grant or independent proof. Cross-store finalization remains non-atomic; a previous execution memory/evidence write or deterministic command mutation may survive a later gateway failure. Legacy non-gateway logs can still contain private owner/query/title data; this repair does not claim global privacy, automatic reconciliation, safe external retries or full autonomous operation.
