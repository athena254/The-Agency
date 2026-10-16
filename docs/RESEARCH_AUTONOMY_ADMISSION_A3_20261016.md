# A3 admission / dispatch investigation — October 16, 2026

Status: **HISTORICAL preimplementation investigation** at the base below. Its source paths/line numbers, observations, baseline totals and proposed next checkpoint describe that revision, not current runtime. A3.1 is now implemented/locally qualified at `418ab8e`; see `QUALIFICATION_AUTONOMY_ADMISSION_A3_1_20261016.md`. G2/G3 and the broader A3 integration remain open.
Base `d9377606de25e7dadbcf85d51480026022068085`; source tree `3e5818e450be307b4d040f9c5252d2f30ba571a1`; tests tree `81902cb80f3dd26dbe04a6f816150f02f72338bf`.

See `SPEC_AUTONOMY_ADMISSION_A3_1.md` for the exact next implementation boundary and `AUTONOMY_READINESS_20261016.md` for all remaining milestone gates.

## 1. Method and completeness boundary

An AST inventory parsed all **151 source Python files** without parse errors and identified **332 candidate call sites** using explicit model/provider/network/process/execute method names plus model constructors and known dynamic callbacks. Every matched candidate was classified; the corrected map has zero unclassified candidates. SQL/decorator/constructor matches remain visible instead of being inflated into inference counts. The first classification left one registry callback unclassified; the corrected version is retained separately, not represented as the original result.

This is a reproducible syntactic inventory plus manual tracing of the named entry paths. It is **not** a sound whole-program call graph, proof against reflective/imported/monkeypatched code, audit of all dependencies, or guarantee that arbitrary callbacks/subprocesses cannot call a model. SDK/bridge/tool extension paths remain explicit exclusions until reviewed and integrated. There is no claim that all 332 calls are model requests.

The index in `research-evidence/autonomy-a3-dispatch-map.json` records each matched path/line/scope/callee/category and category totals. Dynamic relationships such as `execute_task`, `handle_message`, bound executor callables and the direct research consumer are additionally traced below; the method-name scan is not treated as exhaustive relationship discovery.

## 2. Source-backed entry-to-dispatch map

### Telegram and gateway

- `src/telegram_bot.py`: persistent polling cursor, internal trusted-polling handoff and legacy retry behavior are distinct from task completion. Reading a dictionary with a valid-looking user ID does not establish Telegram transport provenance.
- `src/agency/telegram/handler.py:163–285`: private beta polling marker, stable user checks, durable request reservation/capability and model-path refusal. Public dictionary entrypoints remain rejected. `/name` is deterministic and its write outcome is separately handled.
- `handler.py:365–466`: `/research` calls `orchestrator.submit_task` and `execute_task` directly, **not** Butler `execute` or its final audit. It must remain a separate A3 consumer; hardening Butler alone does not certify research.
- `handler.py:381–428,649–723`: normal/thread chat and legacy `process_message` reach Butler; no Butler path may fall back to the demo/model after audit or metering denial. Transport status must not authorize execution replay.
- `src/agency/butler/service.py:287–395`: typed beta denial remains before startup; owner/thread checks and context handling precede routing. Current routing can call a model before its route audit, and conversation persistence currently precedes final gateway audit.
- `service.py:397–483`: isolated gateway uses routing then tool-/memory-free `converse_isolated`; both gateway audit calls are presently best effort. A2 truthful metadata does not make audit acknowledgement mandatory.
- `service.py:522–670`: injected routing callable, configured Ollama, and direct OpenAI-compatible HTTP are separate model paths; broad exception fallback can start other work after a failed routing model. The HTTP helper's current hard-coded endpoint is not an approved future provider policy.
- `src/agency/butler/server.py`: adapter-built routing callable and business-route body exist, but protective middleware keeps sensitive business routes closed. Do not remove the middleware to demonstrate a positive path.
- `src/agency/prototype/app.py:344–392`: local host/origin/CSRF guards, isolated-only service call, true execution status and body-bound A2 metadata. Audit failure currently does not reach it as a blocking service error. Typed handling must avoid the generic traceback logger.

### Runtime and concrete metering

- `src/agency/orchestrator.py`: general/research use ToolDriver; classic execution uses AgentExecutor's captured callable; isolated execution uses its direct executor and excludes the tool loop. A1 recall and A2 report/finalization semantics remain, not a task-wide admission system.
- `src/agency/tools/driver.py:240,340`: normal turns and forced final turn dispatch through the supplied model. Per-loop counters and iteration ceilings are not durable aggregate attempt accounting; exceptional calls can be absent from the reported count.
- `src/agency/agents/executor.py`: may capture `model.generate` at construction. Replacing a model method later is not proof that all execution paths used the replacement. Use an actual recording leaf or preconstruction injection in tests.
- `src/agency/llm/metered.py:25–264`: a concrete Telegram-bound `MeteredBinding`/`MeteredModel` validates scope, reserves a call and claims durable intent before delegating to a concrete adapter. Private capability ownership dies with the budget-store instance; reopening a request does not mint another capability. Audit uncertainty stays blocking.
- The metered wrapper rechecks provider/model labels across budget/audit awaits, but does not pin effective endpoint, credential/account, max tokens, temperature or adapter timeout. No request-wide catalog/resource authorization is inferred from constructor equality. Current context stripping is useful but not an immutable dispatch configuration.
- `src/agency/llm/adapter.py`: six provider HTTP leaves, explicit echo, and a stream helper delegating to generate. Defaults can select a live provider, so offline runners explicitly select echo, strip provider environment and deny external Python sockets. Existing mutable adapter fields and context provider/model overrides must not become admitted-task authority.

### Alternate model and external-effect surfaces

- `src/agency/llm/providers/{openai,anthropic,ollama}.py`: independent SDK generate/stream implementations; `providers/base.py` health check invokes generate. A provider “health probe” may consume a real inference attempt. Echo is deterministic test plumbing. These are not automatically covered by MeteredModel.
- `src/agency/llm/router.py`: configured preset selection, adapter generate/stream. Direct consumers can bypass the request-bound beta seam.
- `src/agency/agents/demo/agent.py`: direct legacy adapter use. It cannot serve as an authorized autonomous fallback.
- `src/agency/agents/planner.py` and `agents/verifier.py`: injected strategy/model-judge callbacks are arbitrary code. Ordinary deterministic behavior does not certify unknown extensions as non-model calls. Model judges are not independent peer authority or a factual oracle.
- `src/agency/bridges/`: coordinator dispatch/health plus Claude/Hermes subprocesses, Codex HTTP and OpenClaw HTTP. Child-agent work/retries may multiply provider attempts and escape Python instrumentation. Exclude from the first autonomous class; no CLI/bridge was launched in this investigation.
- `src/agency/tools/registry.py:197–451`: arbitrary registered `tool.run` callback, strict concrete beta policy/audit seam, separate legacy-open behavior. A policy-bearing registry is not inherited by a caller that constructs a different registry. Native tools/network effects need their own resource/containment/replay contracts.
- `tools/builtin`, `security/sandbox`, addon Git/process helpers and the consensus lab have non-model side effects. Their exclusion from inference counting is **not** permission to expose them to the first fixture task.
- Passive orchestrator status/label reads differ from SDK inference-based health. API decorators, SQL execution, enum conversions and adapter construction are not counted as provider requests merely because their names matched the scan.

## 3. Synthetic offline reproductions

Receipts: `research-evidence/autonomy-a3-baseline-evidence.json`. Raw diagnostics ran at base d937760 on October 16, 2026, 19:48:14–19:48:15 UTC.

**G1 — audit failure still reported completed.** Real isolated Butler/orchestrator path, echo execution leaf and a routing recording callback. Replacing the service audit append with a synthetic exception yielded one routing callback, one execution, two failed audit appends and a `completed` ButlerReply with an assessment. This reproduces a gateway propagation defect; it is not independent QA or a provider outage simulation.

**G2 — endpoint/request-limit drift accepted.** Real concrete MeteredModel, BetaBudgetStore and BetaAuditLog; only the OpenAI provider leaf was replaced with a synthetic recorder. After binding, changing the inner base URL to `https://changed.invalid/v1` and max tokens to 9999 reached that leaf once and received an acknowledged outcome. Zero real HTTP was performed. This establishes a binding gap, not that a live request was redirected.

**G3 — driver counter is not a ledger.** A recording model raised on its first synthetic attempt. The driver returned `llm_error`, one actual callback attempt, but `llm_calls=0`. Treat that field as non-authoritative telemetry. Do not decide quotas or reconcile billing from it.

**P1 — closed/reopened capabilities still deny.** After one synthetic acknowledged call, closing the owning budget store denied another dispatch. Reopening the same DB and requesting the same ID yielded `duplicate=true`, `allowed=false`, state `RUNNING` and no replacement capability. The earlier one call was not rerun. This does not prove complete task recovery.

**P2 — forged beta dictionary still denied.** An allowlisted-looking private update sent through the public dictionary entrypoint yielded `rejected: trusted polling required`. Do not fabricate a Telegram principal to make local tasks reuse Telegram-only metering.

All inputs/markers were synthetic. Diagnostics and baseline ran with explicit echo, scratch CWD/data, isolated PYTHONPATH, stripped provider secrets/environment and a Python external-socket guard. A synthetic recording leaf is not a usable/free-provider probe. Python guards are not an OS sandbox, do not constrain arbitrary native children, and permit loopback for Windows asyncio plumbing. No live Agency provider, Telegram API, worker, push, merge, bot restart or deployment was used.

## 4. Fresh unchanged-source baseline

October 16, 2026, 19:54:49–19:57:58 UTC: full pytest returned **1,794 passed; zero failures/errors/skips** in parsed JUnit. ResourceWarning/thread/unraisable warnings were errors. One existing Starlette/httpx deprecation warning remained visible. Source/test fingerprint before/after was `ca1ebcf791c44a54c0720a6e702ee31bdff3d9c82a47e0158938dab49b6205d4`.

This is a fresh baseline of the A3 worktree's unchanged A2 source, not proof that the reproduced defects are fixed. Git source/tests trees match A2 qualification. The raw file fingerprint differs from the earlier A2 worktree's fingerprint because a separate checkout's raw line endings differ; raw working-file hashes are not compared as if they were Git trees. No src/tests change occurred during either new run. Historical A2 lint/mypy/UI results are not advertised as rerun here.

## 5. Primary research and design consequences

Python context variables are supported in asyncio.[1] They can carry request-local diagnostic scope in a future implementation, but this is not authorization, durable quota storage or a guarantee that child work is safely budgeted. Prefer explicit typed bindings at effect boundaries; do not introduce ambient authority to simplify callback wiring. **No ContextVar implementation is approved by this document.**

Telegram considers an update confirmed when polling uses a greater offset.[2] Therefore audit/execution failure reporting must be separated from delivery/polling status. A3.1 tests the existing poller and terminal failure notice; durable task/outbox semantics remain A5, not an implication of that offset rule.

AWS's idempotency guidance keys duplicate handling to the same caller and request identifier.[3] Apply that distinction to the future contract: equal-looking bodies or retries after a timeout are not new permission, and reused IDs with conflicting intent must be rejected. This is a design requirement, not a claim that generic external effects can be made exactly once.

SQLite documents WAL/FULL synchronization at transaction commit.[4] Reuse the existing concrete beta storage invariant where appropriate, but do not call an ordinary in-memory/NORMAL audit acknowledgement a durable autonomous intent. Independent storage and external effects still require an explicit journal/outbox/reconciliation contract.

Alternatives considered:
- Enable beta first, then add accounting: rejected; opens unmetered authority and hides failure propagation.
- Reuse Telegram metering by inventing local user/update identities: rejected; invalid provenance.
- Trust the driver's successful-call counter: rejected by G3; durable attempts must reserve before dispatch.
- Make every callback a model-shaped duck type: rejected at admitted boundaries; review concrete registrations and direct/alternate consumers.
- Treat a global request context or one shared database as peer authority: rejected; explicit local bindings/stores do not replace independently authenticated peer policy.
- Build an entire new agent framework: unnecessary. Reuse tested adapter/meter/audit/workflow pieces, adding missing contracts dependency-first.
- Repair everything in one maintenance patch: rejected. A3.1 has three runtime file owners and a bounded failure contract; immutable dispatch identity and neutral admission/shared budgets require their own frozen APIs/compatibility/resource decisions.

## 6. Next checkpoint and unresolved decisions

A3.1 proposes strict local gateway audit acknowledgement, preflight/outcome ordering and fixed consumer failure handling. It deliberately does not close G2/G3, direct research, platform-neutral admission, SDK/bridge integration or task-wide recovery. Implementation still requires owner approval of that new contract after commit. It is not blocked on Cline sign-in or a free CLI because direct assistant coding is authorized.

D-05 remains open before an actual runtime task/provider integration: fixture/output resources, limits, provider/endpoint/model/data disclosure/cost and retention. D-01–D-04 remain open before peer grants/actuation. No-delegation means independent review must remain unperformed until the owner arranges/authorizes an actual independent review; self-review cannot satisfy it. No paid fallback, fabricated peer approval or live enablement is chosen to make progress appear faster.

## Sources

[1] https://docs.python.org/3.11/library/contextvars.html
[2] https://core.telegram.org/bots/api
[3] https://aws.amazon.com/builders-library/making-retries-safe-with-idempotent-APIs
[4] https://www.sqlite.org/pragma.html
