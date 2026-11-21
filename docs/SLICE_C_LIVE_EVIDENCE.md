# Slice C live evidence — Agency application over real CometBFT (2026-11-22)

Live fault proof for Slice C on real transport: four `cometbft` 0.38.26 validators on
loopback, each paired with its own `agency consensus-abci-host` child process over a
real TCP socket, driven through real CometBFT RPC (`/broadcast_tx_sync` with `0x`-hex
transaction bytes, `/tx`, `/commit`, `/block`, `/status`). No transport is mocked.

- Harness: `src/agency/lab/comet_agency_net.py` (imports and reuses
  `agency.lab.comet_smoke` helpers: version gate, testnet generation, node-id
  discovery, TOML rewrite/verify, bounded polling, process lifecycle, cleanup
  verification, sanitized report schema).
- Final evidence report: `C:/Users/PC/agency-tools/labs/agency-net-20261122-015423/report.json`
  (schema `agency.lab.comet_agency_net_report/v1`, status PASS, 86.1 s).
- Confirmation run one minute earlier also fully PASSed:
  `C:/Users/PC/agency-tools/labs/agency-net-20261122-014648/report.json`.
- Binary: `C:/Users/PC/agency-tools/bin/cometbft.exe`, `cometbft version` prints exactly
  `0.38.26`, SHA-256 `d7e7d47eae03d6318c5dde83a96af411dbbacc8b0505b501efe0375ee393535c`,
  built from source commit `94d77f9f51a72e2b7d832798859f6222f08028f8` (provenance in
  `COMET_LAB_VERIFIED_STATUS.md`).
- Membership: fresh per run — 4 generated Ed25519 peer identities written as an Agency
  genesis JSON; every host child loads the same file, so membership epoch/root are
  pinned identically across peers. Final run membership root:
  `a2b2cb2a7ff4dc1cde84102fdf8e53a85c31be38150f1e4dc5e376d9e7491260`.

## Production fixes required by the live proof

The C3 socket host as previously landed could not survive a real CometBFT node. The
live run exposed and fixed four defects (all in `src/agency/consensus/abci/` +
`agency/consensus/app.py`), each covered by hermetic tests:

1. **Wire framing**: CometBFT's socket transport frames each protobuf message with a
   base-128 varint length prefix (`abci/types/messages.go` → `protoio` delimited
   writer); the host used a fixed 4-byte big-endian prefix. Symptom: CometBFT
   connected all four ABCI connections (consensus, mempool, query, snapshot) but the
   handshake never completed. Fixed in `host.py`; hermetic socket tests now frame with
   varints.
2. **Commit request never decoded**: the codec had no `Request.commit` (field 11)
   branch, so every `CommitSync` received `ResponseException("unknown request kind")`,
   which CometBFT treats as fatal ("consensus connection terminated. Did the
   application crash?"). Found via a logging proxy host; fixed in `codec.py`.
3. **Unanswered CheckTx/Query**: CometBFT 0.38 dials four connections and kills the
   application connection on any `ResponseException`. Added `check_tx` (mempool) and
   `query` decoding plus dispatch: CheckTx runs `AgencyApplication.check_tx` (new
   deterministic, committed-state-only admission precheck — structure, pinned
   epoch/root, eligible voter, monotonic-seq replay; expiry stays at
   ProcessProposal/FinalizeBlock because it needs block BFT time), and Query returns
   the committed height and state root.
4. **Concurrent connections**: the host served one connection at a time, which deadlocks
   CometBFT's four-connection client. The host now serves each accepted connection on
   its own thread and serializes all request dispatch through one lock (the shared
   application never sees concurrent mutation).
5. **Destructive retain height**: `ResponseCommit` returned
   `retain_height=<current height>`, which makes CometBFT prune all earlier blocks —
   breaking post-restart handshake replay. Now returns 0 (keep history).

## Scenario results (final run)

| Scenario | Result | Key evidence |
| --- | --- | --- |
| converge | PASS | 3/4 approve ballots on `prop_54ca…` committed at height 3; block 4's header app hash `4f5f18d8…` identical on all 4 peers; independent in-process replay of blocks 1–3 reproduces the engine's app hash exactly |
| kill_restart | PASS | node3 (engine + host child) killed; 3 peers committed ballots at height 6; node3 restarted, replayed its stored blocks 1–6 through a fresh application, block-synced, and block 7's header app hash `bee19e3a…` is identical across all 4 peers; the oracle re-derived it from node3's own RPC |
| partition_heal | PASS | persistent-peer rewrite split node0 (empty peer list) from the {1,2,3} island; the island committed quorum ballots at height 13 without node0 (block 14 header hash `f541c3a6…` equal on the island); node0's height stayed frozen at 10 for the full 12 s observation window; after heal + restart all 4 peers agreed at height 14 (`f541c3a6…`) and the healed chain accepted a fresh ballot (height 23, `d3f3e2c1…`); no split-brain |
| replay_idempotent | PASS | rebroadcasting the exact committed ballot bytes to two different nodes was refused by CheckTx (`code=1`, "invalid ballot: rejected by deterministic precheck"); over the 8 s window the `/tx` index height for that ballot never moved and each node's PeerLog `executed_grant` counts stayed exactly one per proposal |
| revocation_fail_closed | PASS | grant issued then revoked through the `GrantLedger`; `verify_at_actuator` refused with `REVOKED`; the actuator never ran the executor (0 executions); a fresh control grant verified and executed (positive control); the used control grant afterwards also fails closed (`REPLAY` nonce barrier) |
| conflicting_proposals | PASS | voter0 signed approve (height 38, accepted) then a conflicting deny ballot (height 40, deterministically rejected with reason `equivocation`, payload digest `9cbf3d00…`, not in accepted); the oracle replayed blocks 1–40 through `LatticeAppState` and matched the engine's app hash `fbd5a5fe…` exactly on node1; tally after the conflict is deterministically (`open`, approve=2, deny=0) on all 4 peers — the equivocator's vote is retracted and no quorum can be reached |

App-hash agreement across all 4 independent peers was checked at heights 4, 6, 7, 14,
15, 23 and 41 (all equal, see `app_hash` entries in the report).

## Durable attribution

Each host child ran with `--peer-log` (SQLite, WAL, `synchronous=FULL`) and its own
`issuer.key`. After all processes were stopped, every node's log was reopened through
the production `PeerLog` class: chain verification healthy on all 4 nodes
(125–128 records each, durability FULL). Every executed proposal has **exactly one**
`executed_grant` record per node across the entire run — including node3, whose ledger
is in-memory and was restarted mid-run: the actuator's executed-grant dedup catches the
replayed grant by digest, so catch-up replay does not re-execute. Per-proposal record
noise (`result` records re-appended per block by the minimal CLI adapter seam) is
append-only and does not affect the exactly-once execution guarantee.

## Deviations (explicit, no silent weakening)

1. **Proposal txs are represented by their ballots.** The deterministic ABCI surface in
   this increment processes `ballot` records only: a `proposal`-kind envelope is
   rejected at ProcessProposal (block rejected, chain would stall) and recorded as
   `UNSUPPORTED_RECORD_KIND` at FinalizeBlock. "Submit a proposal" therefore means
   broadcasting the 3/4 approve ballots that carry the proposal to a quorum decision
   (D-01 arithmetic, `passed` at 3 approvals). Decisions are inside the app hash; the
   replay oracle re-derives them independently.
2. **Revocation runs in-process, not through a live host channel.** The running host
   exposes no control channel to revoke a grant inside its in-memory ledger, so the
   revocation phase exercises the real `grants.py`/`actuator.py` paths (issue → verify
   → revoke → verify → actuator) in-process against the same membership the live net
   used. Transport identity/authorization independence is covered by the live phases.
3. **Heights advance continuously; stall/replay evidence is per-transaction and
   per-record.** The app hash binds height and BFT time (per the determinism contract),
   so it changes on every block; CometBFT's `needProofBlock` then commits empty proof
   blocks at the round cadence even with `create_empty_blocks=false` (observed ~0.7 s
   cadence). "Isolated node stalls" therefore compares frozen height against a moving
   majority (still unambiguous), and the replay phase proves idempotency via the CheckTx
   rejection, the immutable `/tx` index height, and unchanged `executed_grant` counts
   instead of frozen heights.
4. **Conflicting proposals mapped to the app's equivocation primitive.** CometBFT
   itself never commits two blocks at the same height, so "two conflicting proposals at
   one height" is realized as two conflicting ballots (approve/deny) from the same voter
   for the same proposal across consecutive heights. Exactly one of the pair is accepted
   as a record, the other is deterministically rejected (`equivocation`), and the
   fail-closed tally (`open`, 2/4) is identical on every peer.

## What this does NOT prove

Not Byzantine fault tolerance, not packet-level network partition (the split is a
persistent-peer config rewrite on loopback), not independent-host resilience, not
production readiness, and revocation is not reachable through a live host control
channel. See `not_proved` in the report.

## How to re-run

```bash
AGENCY_LLM_PROVIDER=echo AGENCY_LLM_MODEL=echo PYTHONPATH=src \
  .venv/Scripts/python.exe -m agency.lab.comet_agency_net \
  --binary C:/Users/PC/agency-tools/bin/cometbft.exe \
  --output C:/Users/PC/agency-tools/labs/NEW_UNIQUE_DIRECTORY \
  --whole-timeout 900
```

Hermetic tests (no binary): `pytest tests/test_comet_agency_net.py`. The live scenario
also runs under pytest when `AGENCY_LIVE_SLICE_C=1` and `AGENCY_LIVE_COMETBFT` point at
the pinned binary.

## Verification summary

- Targeted suites (labs, consensus, ABCI, HTTP grant gate): **182 passed, 1 skipped**
  (the skipped test is the gated live run) with ruff, ruff format and mypy clean on all
  changed files.
- Three consecutive full PASS lab runs (2026-11-22 01:46, 01:48, 01:54 local); every
  owned process confirmed stopped in each report; independent process listing showed no
  remaining cometbft processes.
