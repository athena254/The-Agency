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
  '.venv/Scripts/python.exe' -m agency.lab.comet_smoke \
  --binary C:/Users/PC/agency-tools/bin/cometbft.exe \
  --output C:/Users/PC/agency-tools/labs/NEW_UNIQUE_DIRECTORY \
  --whole-timeout 180
```

### Binary provenance (2026-11-21, machine of user `PC`)

Built from source with two-channel verification; the harness still does not download:

- Tag object `2228c7101a00994a14811e15cd8795c7e70713c2` (GPG-signed), commit
  `94d77f9f51a72e2b7d832798859f6222f08028f8` — both confirmed identical between the
  local shallow clone (`C:/Users/PC/agency-tools/cometbft-src`) and the official
  GitHub API ref (`repos/cometbft/cometbft/git/ref/tags/v0.38.26`, dereferenced).
- Built with Go 1.27.4 (`D:/tools/go`): `go build -o .../bin/cometbft.exe ./cmd/cometbft`.
- Artifact SHA-256:
  `d7e7d47eae03d6318c5dde83a96af411dbbacc8b0505b501efe0375ee393535c`
- `cometbft version` prints exactly `0.38.26` (harness exact-match gate satisfied).

## Boundary and remaining work

This original checkpoint was created on `feat/free-lattice-engine`. For its subsequent
merge/consolidation status, see `FREE_PROGRAM_BUILD_STATUS.md`; no production deployment
is implied. It does not implement Agency peer authorization,
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

## Slice C agency-net (2026-11-22)

`agency.lab.comet_agency_net` extends the engine lab to the Agency application on real
transport: four 0.38.26 validators, each paired with its own
`agency consensus-abci-host` child over a real TCP socket, transacting signed ballot
envelopes through real CometBFT RPC. Three consecutive full PASS runs on 2026-11-22
(final report `C:/Users/PC/agency-tools/labs/agency-net-20261122-015423/report.json`,
schema `agency.lab.comet_agency_net_report/v1`): quorum ballot convergence with an
independent app-hash replay oracle, node kill/restart with application replay
convergence, 1/3 persistent-peer partition with majority progress + isolated-node stall
+ healed convergence, rebroadcast replay refused at CheckTx with exactly-once
`executed_grant` records, revocation fail-closed at the ledger/actuator seam, and
conflicting ballots resolved deterministically (equivocation rejection, fail-closed
tally) with equal app hashes across all four peers. Full per-scenario numbers, deviations
and re-run instructions: `SLICE_C_LIVE_EVIDENCE.md`; machine-readable summary appended to
`research-evidence/comet-engine-verification.json`.

The live proof required production fixes to the previously landed socket host: varint
framing (CometBFT's delimited-wire format), decoding `Request.commit`/`check_tx`/`query`,
answering the mempool and query connections (CometBFT dials four and kills the app
connection on any `ResponseException`), per-connection serving with one dispatch lock,
chain-id binding via InitChain, and non-destructive `retain_height`. Each fix carries
hermetic tests; the engine (kvstore) harness and its report schema are unchanged.
