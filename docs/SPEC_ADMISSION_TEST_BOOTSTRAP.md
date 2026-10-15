# Spawned admission-test bootstrap repair — implementation contract

Status: reviewed bounded test-harness repair for the requested batch. Base source `357e4d8`. No production admission change, beta enablement or deployment.

## Causal evidence and deliberate timing change

The unchanged original test module's fresh import took 19.687 seconds in the investigation, while the parent allowed 15 seconds for the first ready message. Timestamped evidence shows a late ready message was successfully transmitted after the parent had timed out; a same-platform Event/Queue control passed. This is startup/import latency, not proven broken IPC or a demonstrated production admission bypass.

The prior 15-second ready limit conflated interpreter/dependency bootstrap with readiness of an initialized registry. A minimal-import candidate still missed readiness and is rejected. Given that causal evidence, introduce an explicit, separately reported **60-second aggregate bootstrap budget**. This is an intentional test-harness timing-contract change, not a claim that the old deadline passed. The actual ready/observation waits remain 15 seconds; start/release gates remain checked 10-second waits; the old read/check synchronization barrier remains 5 seconds. No retries, no increased runtime race deadlines and no early false-ready messages.

## Frozen protocol

1. Spawn a lightweight importable test-support target, never the heavyweight `test_beta_tool_boundary` module. Its module top level imports only stdlib. Child may emit `bootstrap_started`, with PID/phase, which is explicitly NOT readiness.
2. Import the actual concrete BetaAuditLog, registry, policy, principal and minimal tool types inside bootstrap; initialize the real temporary audit connection and registry. Emit `bootstrap_complete` only after those operations. Parent must obtain both completions within ONE absolute 60-second bootstrap deadline, not a fresh budget per queue message.
3. Parent sets a distinct `arm` event only after both actual initializations. Worker waiting for arm is separately bounded by the bootstrap contract; a timeout fails and cannot enter the race.
4. Armed workers emit actual `ready`, then require `start.wait(10)` to return literal True before registry.call. Parent collects both ready messages within the unchanged readiness allowance, then sets start once.
5. The admitted fake tool emits `ran` and requires `release.wait(10)` to return True before returning success. Before release is set, parent must observe exactly one ran and one explicit denial `(False, beta audit unavailable)`. Preserve concrete request/principal/policy/audit boundary checks and real separate processes/connections.
6. Parent sets release exactly once, drains/asserts the admitted success result, checks both worker exit codes and verifies durable audit rows are exactly intent pending + successful outcome, with no unresolved pending. Unexpected/error phase messages fail immediately with sanitized diagnostics.
7. Every setup/failure path reaps owned children, including a partially failed process.start. Gate expiry must produce explicit failure, zero dispatch after expired start, and no false success after expired release. Restore any process-local class monkeypatch in finally for in-process regressions.

## Mutation and regression requirements

- Original failure is recorded; do not describe warm import success as an unchanged-deadline fix.
- Expired start: zero tool executions and zero durable intent/outcome rows.
- Expired release: no successful tool result; exact failed outcome/error is asserted and worker cannot report success.
- Secure real two-process race: one admitted/held, one denied while release is unset; success only after release and exact durable rows.
- A deliberately split read/check/append claim implementation in a test worker must produce the forbidden two-ran scenario and be rejected by the parent assertions. This is a mutation check in synthetic tests only; never alter production claim_intent.
- Bootstrap/readiness/worker errors retain PID/phase diagnostics and cannot be confused with a legitimate denied competitor. Preserve inherited source PYTHONPATH/networking guard and echo provider/model.

## Ownership and acceptance

Own `tests/test_beta_tool_boundary.py`, new `tests/worker_support/__init__.py`, new `tests/worker_support/admission.py`, and new `tests/test_admission_worker_protocol.py`. Top-level support imports remain stdlib-only; all actual Agency imports stay in explicit bootstrap. Use an importable namespace compatible with Windows spawn and the project's pytest path setup, not an installed unrelated tests package.

No production source changes, no new package dependency, no larger default test/suite deadlines, no dropped security assertions, suppression or success-by-retry. Run targeted RED/GREEN, protocol negatives/mutant, real race, full beta boundary/metering/admission tests, scoped Ruff and diff checks. All checks use scratch data, actual qualified Python and blocked external network. If the explicit bootstrap budget is still insufficient, report infrastructure failure rather than increase it again.

This change repairs how a test establishes its experiment. It does not establish a new production capability or show any live model/tool ingress safe to enable.
