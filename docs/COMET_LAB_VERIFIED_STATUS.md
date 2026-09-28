# CometBFT engine lab — verified checkpoint

## What works

`agency.lab.comet_smoke` is an isolated, stdlib-only local test harness. It creates
four CometBFT 0.38.26 processes with distinct generated node homes, identities and
persistent state. It is not imported by production and cannot authorize Agency actions.

Two actual full scenario runs passed (`review-run-02`, `review-run-03`):
- commit a synthetic key/value transaction with all four peers;
- terminate one peer and commit a new transaction with the remaining three;
- restart that peer, check matching committed state and the expected query value;
- stop two peers, submit a transaction, and observe no commit/height progress for
  the complete 20-second window while both remaining RPC services stay responsive;
- restore peers, commit a fresh independent-key transaction above the stopped
  baseline, and verify query values and post-transaction app hashes on all peers;
- terminate and wait every remaining child, close logs, and record confirmed cleanup.

The Windows process termination in these runs is abrupt termination, not a Byzantine
attack or a simulated network partition. Shared-machine/admin failure remains out of scope.

## Verification

- Baseline b0971ee in a separate checkout: **1146 tests passed**, 22 warnings.
- Candidate entire suite: **1197 tests passed**, 19 warnings (146.52 seconds).
- Focused lab coverage: **51 tests**. Regression tests exposed RPC encoding,
  incomplete PASS validation, late-deadline success, unbounded port retry and failed
  cleanup before fixes. The initial red run also revealed a test-fixture omission;
  that fixture was corrected rather than credited as a product defect.
- Ruff check and formatting passed on the four changed Python files; mypy passed on
  both lab source files. Static scan found no eval/exec calls or shell=True.
- Actual 6-second deadline run returned exit 1, wrote FAIL with later phases skipped,
  and confirmed all four peers stopped.
- Reusing a nonempty output directory returned exit 2 and preserved the original
  report byte-for-byte (SHA-256 compared).
- Final process inspection found no CometBFT processes remaining.
- Independent free-model runtime and validation subset reviews found no blocking
  findings. Their suggestions and missing-context qualifications are retained in
  `research-evidence/comet-engine-independent-reviews.json`; this is not certification.
- Full reports and source/test SHA-256 fingerprints are in
  `research-evidence/comet-engine-verification.json`.

## Provenance and review fixes

Cline free DeepSeek V4.1 Flash generated implementation changes and regression tests.
Reported model costs for those runs were zero. Coordinator review rejected incomplete
proposals, applied verified literal patches, corrected fixture/API/schema integration
mistakes, ran the real checks and collected evidence. Earlier broad workers timed out;
their completion was never treated as delivery. No paid coding-worker fallback used.

Initial real run failed on the wrong transaction-hash RPC encoding. Fixes include
0x-prefixed RPC hashes, precise error.data absence classification, interrupt cleanup,
failed-termination accounting, bounded retries, deadline rechecks, strict PASS phase
validation, exact version matching, and fresh-transaction restoration proof.

## Run again (Windows Git Bash)

Use a **new** output path every time; never erase/reinitialize existing signing state.
The binary is supplied by the caller; the harness does not download it.

```bash
AGENCY_LLM_PROVIDER=echo AGENCY_LLM_MODEL=echo PYTHONPATH=src \
  'C:/Users/alphi/agency-mvp/.venv/Scripts/python.exe' -m agency.lab.comet_smoke \
  --binary C:/Users/alphi/AppData/Local/Temp/agency-bft-tools/bin/cometbft.exe \
  --output C:/Users/alphi/AppData/Local/Temp/agency-peer-labs/NEW_UNIQUE_DIRECTORY \
  --whole-timeout 180
```

## Boundary and remaining work

This checkpoint is **engine-only**, in `feat/free-lattice-engine`. It is not merged
into the main Agency or deployed. It does not implement Agency peer authorization,
affected-consumer participation, distributed task scheduling, or real-action execution.
Butler remains specified as the non-agent human gateway, without voting authority.

Next architectural slice is an Agency application/task protocol on top of peer
coordination, followed by a harmless end-to-end task and coordinating-peer failure
proof. A consensus engine passing its lab must not be mistaken for that milestone.

Known nonblocking follow-ups: some deterministic application failures are reported
as bounded polling timeouts with the original diagnostic; stronger best-effort report
handling could preserve diagnostics if report validation or cleanup itself crashes.
The local output directory contains generated signing material and node logs; keep it
private. This harness is not a sandbox against a hostile same-host administrator.
