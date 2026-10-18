# Lattice repairs — handoff, October 16, 2026

Status: **completed repair checkpoint, locally qualified; not production or peer-authority approval.**

## Start here

- Repository: `athena254/The-Agency`.
- Publication branch: `fix/lattice-foundation-20261015`.
- Last tested code revision: `ba9ec76896cd70df290f0fe70fe7c9ac0a7528ee`.
- The handoff commit adds documentation/evidence only. Its `src` and `tests` trees must equal the tested revision, not a new untested implementation.
- Canonical local `main` remains `713a4e311b1e071fbd6c5cf103b995d17aa7e5a2`. Do not switch or overwrite a live checkout as part of handoff.
- Remote `main` observed before publication: `3417c79bf2056ab414f49b3c0281b5c723377493`. It is an ancestor of the repair branch, not a diverging newer implementation. There were 45 unpublished ancestor commits before this documentation checkpoint, including 18 repair-slice commits; publishing the branch preserves the earlier completed local baseline too.
- Work is published as a branch, **not merged into main, released or deployed**. No tag or deployment workflow is requested.

## Plain-English result

Shared Lattice users now hold explicit lifetime leases: stopping one consumer does not silently close another consumer's shared backend. Initialization publishes only ready resources; managed operations reject the wrong event loop; same-instance start/stop is serialized and cleanup drains before a replacement lifetime is installed. Webhook and owned-store cleanup handles failure/cancellation, and callback approval accepts only literal `True`.

These are local lifetime and correctness repairs. Leases are not user permissions, peer identities, execution grants or decentralized consensus.

## Target architecture — do not change the meaning

Human ↔ Butler ↔ the full Agency backend. Butler carries human requests, context, confirmations and results; it is not a planner, validator, voter or unilateral boss. Lattice is the shared hive-memory, messaging, coordination and orchestration layer among participating, independently identified peers. Shared memory does not mean unrestricted visibility. Keep consequential actions and beta model/tool execution denied until the documented authority and durable-audit gates are satisfied.

## Completed repair commits

| Integrated commit | Completed scope |
| --- | --- |
| `2dfedf1`, `a4b9e02` | Bounded repair contract and consistent Lattice/Butler/selected peer-policy documentation |
| `270aa22` | Strict literal-boolean callback verdicts; not authenticated peer approval |
| `20603c6` | Collection-warning fix preserving intended test cases |
| `87fc288` | Webhook lifespan, startup rollback and owned handler cleanup |
| `22fe24f` | Owned service-store cleanup and failed-start rollback |
| `6e14d07` | Recovery diagnostic/interpreter ownership fixes; historical stability remains open |
| `ed597e7`, `357e4d8` | Qualification follow-up contract and hermetic bridge tests |
| `06da362` | Frozen Lattice lease and admission bootstrap contracts |
| `f193034`, `9296b9b`, `165aad5` | Test-only admission bootstrap, checked race gates, exhaustive cleanup and non-throwing diagnostics |
| `bfabc19`, `0522172` | Process-local leases, ready-only publication, manager installation/operation-loop enforcement |
| `34feed1`, `d181b9e` | Orchestrator/API consumers migrated together; concurrent start/stop/restart serialization |
| `ba9ec76` | Public-constructor governance test fixture; existing three test bodies/assertions unchanged |

All repair worker worktrees were clean at inventory. Their committed changes were ancestors or patch-equivalent to this integration branch; no unique committed repair patch was left out. Do not push every historical/experimental branch or reinterpret earlier rejected patches as separately approved work.

Independent review anchors: foundation `ba7cc1c`; admission `d6c78fb`; consumer lifecycle `c201b37` (review `deleg_ec3610c2`, actual 50 lifecycle tests); fixture `deleg_42444012` (static/AST review only). Parent runtime checks are separate evidence, not a claim that the static fixture reviewer ran tests.

## Actual qualification

Full offline local run at `ba9ec76896cd70df290f0fe70fe7c9ac0a7528ee`:

- 1,664 Python tests passed; zero failures/errors/skips; one Starlette TestClient/httpx deprecation warning.
- 14 UI tests passed.
- Repo-wide Ruff lint and formatter check passed.
- Strict mypy passed on 150 source files.
- Completed full-run wall time: 205.14 seconds, no timeout, clean checkout and unchanged revision/source/test trees.
- Python 3.11, explicit `AGENCY_LLM_PROVIDER=echo` and `AGENCY_LLM_MODEL=echo`; scratch working/data/temp directories, absolute source/test paths, Python DNS/socket egress guard.
- ResourceWarning, PytestUnhandledThreadExceptionWarning and PytestUnraisableExceptionWarning were errors. Timed faulthandler dumps were disabled because that diagnostic crashed natively on this Windows lane; test assertions were not suppressed.

Machine-readable record: `research-evidence/lattice-repairs-qualification-ba9ec768.json`. Raw logs/JUnit and the exact executed local harness are retained in the private handoff archive; they are not replaced by invented output. Python monkeypatch-based egress denial is not OS-level containment of arbitrary child programs.

The full suite is **not** being rerun merely to add these documents. Source/test-tree equality is the boundary linking this handoff commit to the tested code revision. No current CI matrix, fresh supported-launcher `--check`, live-provider, canary, release or deployment pass is claimed. The older launcher pin/check belongs to the earlier baseline; read and qualify `scripts/run_telegram_bot.py` before any future authorized rollout.

### Preserve failures rather than erase them

- Full `6e14d07` timed out without a completed JUnit summary; missing totals are unknown, not zero failures.
- Parent admission at `959eb33` timed out after 228 dots. Later passing selections do not establish the cause or eliminate historical flakiness.
- Parent `c201b37` startup timed out; a diagnostic native crash is excluded as passing evidence. Closing owner-authorized Brave/VLC preceded a successful parent run; causation was not proved.
- Full `d181b9e` completed with three legacy fixture failures. The fixture-only repair reproduced RED, preserved original test bodies and passed focused checks; full `ba9ec76` then passed. Keep both records.

## Remaining work — not silently completed

1. **W-01:** other store-internal initialization publication/rollback/failure/cancellation acceptance, beyond the accepted Lattice lifetime slice.
2. **W-03:** qualify a dev-only httpx2 dependency/lockfile change against the actual installed Starlette/FastAPI/HTTPX versions. No dependency install or change has been made for this issue.
3. **W-05 / qualification stability:** investigate retained historic timeout/recovery incidents; one later green run is not proof of a causal fix. Bound event-gated lifecycle test waits without weakening assertions.
4. **R-01 context:** classic routed execution omits already-assembled recalled context. Parent re-read confirms tool prompts consume it, classic execution uses the unaugmented system prompt (`orchestrator.py`, tool branch around 580 and classic branch around 615). Repair prompt propagation only after a bounded contract; preserve isolated chat's no-memory/no-tools behavior and do not treat recalled text as authority.
5. **R-02 specialist/DAG dispatch:** real assigned specialists/dependency outputs, not repeated whole-task prompts. Freeze assignment/grant interfaces first.
6. **R-03 truthful verification:** tool records hardcode `verification=passed` and `risk_category=none` without those checks (`orchestrator.py` around 596). Classic heuristic verification is not independent QA authority. Change labels truthfully under a reviewed contract; do not invent a QA/grant policy.
7. **P1/R-04:** trusted principal-bound memory scope/provenance, query/error redaction, all-provider metering and durable fail-closed audit. Keep beta memory/model/tools denied.
8. **R-05:** durable task/result acknowledgement and lifecycle contracts; closing owned stores is not complete durable task semantics.
9. **R-06:** literal callback comparison is repaired; authenticated actor/evaluation publication is still a separate contract.
10. Independently authorized harmless Human–Butler–Lattice–peer task remains undemonstrated. Owner-approved fault/validator/membership/consensus and independently verifiable human-input contracts are prerequisites. No unilateral Butler/orchestrator workaround.
11. CI, clean-clone/cross-platform behavior, supported launcher, isolation/egress, backup/restore, rollout/rollback and independent adversarial gates remain open as documented in `PRODUCTION_READINESS_PLAN.md`.

Two subsequent read-only R-01/R-03 maps completed at the tested revision (`deleg_63db1f58`); they changed no code and ran no runtime tests. Available local traces are archived. Their additional proposals are not approved specifications or implemented repairs. Parent source counter-checks above are narrower than accepting every delegated conclusion.

## Free CLI worker plan and current blockers

Standing direction: use eligible **free CLI models** for bulk bounded code, tests and documentation; coordinator owns contracts, review, integration and independent checks. No automatic paid/subscription fallback. Earlier GPT replacement approval was scoped to its repair batch, not a new standing implementation policy.

Observed readiness checks (not claims about future availability):

- OpenCode installed/native version 1.19.0. Official npm metadata advertised 1.19.1 at the last research check; not installed. Muse Spark 1.3 Contributor Free and Big Pickle synthetic probes both returned HTTP 403/FreeTierError even through the official executable. No model answer or coding readiness was established. Do not spoof identity/headers, proxy restricted free endpoints or loop indefinitely.
- Cline installed package 3.0.65, documented headless JSON support. Current catalog at research time listed free Nemotron 3.5 Super VL, MiMo V2.6 Flash, Muse Spark 1.3 Contributor and Solar Mini 4. Exact `cline-free/mimo-v2.6-flash` probe exited with `requires re-authentication`. Owner sign-in via `cline auth cline` is required; never request passwords/tokens in chat. Pin exact provider/model and check free pricing/finish reason/real artifacts before expanding the batch.
- Freebuff installed package 0.0.195. Published terms updated October 13, 2026 restrict automated operation without written permission and require human initiation/presence. It was not launched as an automated worker. Manual owner-operated output can still be handed to the coordinator. The posted privacy policy examined had a future effective date of November 12, 2026; do not present all of that future policy as already effective.
- Other free CLI research is incomplete. Qwen's official docs say its OAuth free tier ended April 15, 2026. Gemini's official site reports migration of unpaid/Google One users to Antigravity CLI on June 18, 2026. Do not repeat old quota claims or install these as supposedly working free lanes without current eligibility/auth/automation checks.
- There are no active subagents at the last parent status check and no successfully launched new free implementation batch. Readiness probes produced no bulk code. The prior interrupted turn did not complete the free-worker setup.

Use one isolated worktree/exclusive file list per writer, dependency-first contracts, enforceable no-progress/deadline bounds and incremental artifacts. Worktrees/ignore files are **not** OS security sandboxes. Exclude credentials, private runtime databases and personal transcripts; sanitize worker environments and do not send them to contributor/free tiers. Verify training/privacy terms before sending sensitive inputs. Begin with a small actual file-edit/test task after authentication, not a large batch on the strength of a catalog entry.

## Safe continuation order

1. Fetch the publication branch into a clean isolated checkout; do not reset the canonical/live main checkout.
2. Read this handoff, `CURRENT_STATE.md`, the frozen repair specs, `KNOWN_ISSUES.md`, `LATE_REVIEW_DISPOSITION.md` and readiness/peer protocol specs. Distinguish implemented behavior from proposed contracts.
3. Confirm source/test-tree IDs against the qualification JSON. Preserve raw failed/passing evidence and do not overwrite old logs.
4. Unblock Cline login or diagnose the exact OpenCode restriction/version via official mechanisms. No private credential reads, paid fallback, model-access bypass or arbitrary new installs.
5. Freeze/review/commit the next bounded contract and obtain required approval before implementation; R-01 and R-03 share `orchestrator.py`, so serialize ownership of that file even if documentation/test research is parallel.
6. Test independently and integrate only reviewed outputs. Full local/CI evidence must match the changed revision. Push does not authorize main merge or deployment.
