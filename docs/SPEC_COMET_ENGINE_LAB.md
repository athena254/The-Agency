# CometBFT Engine Lab — exact implementation contract (frozen)

Scope: engine-only. Four separate `cometbft.exe` processes on loopback,
separate node/validator keys and stores, synthetic `persistent_kvstore`
fixture only. This demonstrates engine operation, NOT Agency policy
authorization or distributed orchestration. Labels: engine-only. This is NOT
a network-partition test and NOT a Byzantine test (process-stop crash and
quorum evidence only).

## CLI contract

`python -m agency.lab.comet_smoke --binary <ABS_EXE> --output <NEW_LAB_DIR>`

- `--binary`: required, must be an absolute path to an existing file. Refuse
  relative paths and missing files with exit code 2.
- `--output`: required. If the path exists and is non-empty, refuse with exit
  code 2 (never erase or reinitialize signing state). If missing, create it
  (including parents). If present and empty, reuse it.
- `--whole-timeout`: optional float, default `420.0` seconds. Whole-run
  deadline enforced by every wait loop alongside its stage timeout.
- Startup gate: run `<binary> version` (30 s timeout). Stdout must contain
  exactly `0.38.26` (`EXPECTED_VERSION`). Any mismatch, execution failure,
  or timeout is fatal (exit 1) before any node state is created.
- Dependencies: stdlib only (`argparse`, `json`, `shutil`, `socket`,
  `subprocess`, `time`, `tomllib`, `urllib.parse`, `urllib.request`). No
  third-party imports. No production imports. No listener outside loopback.

## Fixed limits

- `VALIDATOR_COUNT = 4`, `EXPECTED_VERSION = "0.38.26"`.
- `TESTNET_TIMEOUT_S = 120.0`, `VERSION_TIMEOUT_S = 30.0`.
- `STAGE_TIMEOUT_S = 90.0` per wait stage, `TX_POLL_TIMEOUT_S = 30.0`,
  `POLL_INTERVAL_S = 0.5`, RPC per-call timeout 5 s.
- Ports: per node one loopback P2P port and one loopback RPC port (8 total),
  allocated from OS-ephemeral free ports bound on `127.0.0.1` at startup,
  all distinct. No fixed ports. Built-in `persistent_kvstore` needs no ABCI
  socket ports. Part 2 covers runtime, report, and test contracts.

## Testnet + config contract

1. Generate: `<binary> testnet --v 4 --populate-persistent-peers=false --o <output>`
   (`TESTNET_TIMEOUT_S`). `--v` defaults to 4 and `--populate-persistent-peers`
   defaults to `true`, so both are passed explicitly. Produces `<output>/node0..node3`
   with independent keys and stores. Generated defaults already observed on the
   pinned build: `[p2p] addr_book_strict = false`, `[p2p] allow_duplicate_ip = true`,
   `[p2p] seeds = ""`, `[instrumentation] prometheus = false`,
   `[rpc] pprof_laddr = ""`; the rewrite below still sets the lab-critical values
   explicitly and the post-edit parse asserts them.
2. Node IDs: `<binary> show-node-id --home <nodeDir>` per node (30 s each).
3. TOML rewrite (`<nodeDir>/config/config.toml`), line-based, tracking the
   current `[section]`. Each rewrite targets a unique `(section, key)` pair
   and must match exactly one line; zero or multiple matches is fatal. No
   broad string replacement. Exact edits:
   - `([rpc], laddr)` -> `"tcp://127.0.0.1:<rpcPort>"`
   - `([p2p], laddr)` -> `"tcp://127.0.0.1:<p2pPort>"`
   - `([p2p], persistent_peers)` -> `"ID0@127.0.0.1:<p2p0>,..."` minus self
   - `([p2p], seeds)` -> `""`
   - `([p2p], pex)` -> `false`
   - `([rpc], unsafe)` -> `false` (assert present)
## Part 2 — runtime, scenario, report and test contract (frozen)

### Deadline discipline

`Deadline(whole_timeout)` exposes `remaining()` (`>= 0.0`), `expired()` and
`bound(cap_s)` = `min(cap_s, remaining())`, raising `LabTimeout` when the result is
`<= 0`. Every wait loop is bounded by its stage timeout **and** the whole-run deadline.
Every subprocess call passes `timeout=deadline.bound(...)`; every HTTP call passes
`timeout=min(RPC_TIMEOUT_S, deadline.bound(RPC_TIMEOUT_S))`. No phase may report
success after an unchecked timeout or an unhandled RPC error: the first failing phase
stops all further node-state mutation, later phases are recorded `skipped`, and the run
still performs full cleanup and exits nonzero.

### Scenario phases (fixed order, all recorded in `report["phases"]`)

1. `version` — `<binary> version` (`VERSION_TIMEOUT_S`); stdout must contain
   `EXPECTED_VERSION`; runs before any node state exists.
2. `testnet` — generate 4 homes, `show-node-id --home <nodeDir>` per node, TOML edits,
   then re-parse each `config.toml` with `tomllib` and assert the edits landed.
3. `start4` — start 4 processes, poll `/status` until all 4 report
   `result.sync_info.latest_block_height >= 1`.
4. `tx4` — broadcast one probe transaction with `broadcast_tx_sync` (JSON-RPC POST, `tx`
   base64 per Go `[]byte` JSON semantics) and require `check_tx.code == 0`. Poll
   `/tx?hash=<HEX>` until the tx result is found with `tx_result.code == 0` and a block
   height `H4`; require `abci_query` for the probe key to return a non-empty
   `result.response.value` whose base64 decodes to the expected value; then require the
   same-height app hash (`/commit?height=H4`, `result.signed_header.header.app_hash`) to
   be equal on all 4 nodes.
5. `stop1_commit3` — terminate node3 and wait for process exit, submit a second
   transaction, require it to commit at height `H5 > H4` with the 3 running nodes,
   require the new value to be readable by query, and require same-height app hash
   equality on those 3 nodes.
6. `restart_catchup` — restart node3, poll until its height `>= H5` **and** its app hash
   at `H5` equals the other nodes' (rejoined and converged, not merely listening).
7. `quorum_loss` — terminate node2 and node3 (2 of 4 running). Submit a third
   transaction: `check_tx.code` must be `0` (accepted into mempool), then observe for
   `QUORUM_LOSS_OBSERVE_S` (20 s) that no running node's height increases and `/tx` for
   that hash stays absent. The 2 running nodes must stay responsive (`/status` ok) during
   the window — unchanged height with dead nodes is not evidence. The window is a
   bounded observation, never "no news means pass".
8. `restore` — restart node2 and node3, poll until all 4 reach the same height with
   equal same-height app hash. Whether the quorum-loss transaction later commits is
   recorded as `quorum_loss.late_commit_observed` (informational only).
### Report contract

`<output>/report.json` (`REPORT_NAME`), written best-effort on failure as well as
success; if the report cannot be written the CLI still exits nonzero and says so.

```json
{
  "schema": "agency.lab.comet_engine_report/v1",
  "label": "engine-only",
  "not_proved": ["byzantine-fault-tolerance", "network-partition",
                 "independent-host-resilience", "agency-policy-authorization",
                 "production-readiness"],
  "status": "PASS",
  "failures": [],
  "limits": {"validator_count": 4, "stage_timeout_s": 90.0, "tx_poll_timeout_s": 30.0,
             "version_timeout_s": 30.0, "testnet_timeout_s": 120.0,
             "rpc_timeout_s": 5.0, "poll_interval_s": 0.5,
             "quorum_loss_observe_s": 20.0, "whole_timeout_s": 420.0},
  "binary": {"path": "...", "version": "0.38.26", "expected_version": "0.38.26",
             "matches": true},
  "output_dir": "...", "started_utc": "...", "finished_utc": "...", "duration_s": 0.0,
  "engine_only": true,
  "nodes": [{"index": 0, "node_id": "...", "home": "...",
             "p2p_laddr": "tcp://127.0.0.1:...", "rpc_laddr": "tcp://127.0.0.1:..."}],
  "phases": [{"name": "start4", "status": "passed", "detail": "...", "duration_s": 0.0}],
  "heights": {"start4": {"node0": 1}, "tx4_commit": 3, "tx3_commit": 4,
              "restore_common": 5},
  "app_hash": [{"phase": "tx4", "height": 3, "equal": true,
                "values": {"node0": "ABCD...", "node1": "ABCD..."}}],
  "txs": {"tx4": {"hash": "...", "check_tx_code": 0, "tx_result_code": 0, "height": 3}},
  "quorum_loss": {"submitted": true, "check_tx_code": 0, "observe_s": 20.0,
                  "height_unchanged": true, "tx_found": false,
                  "observed_heights": {"node0": 4, "node1": 4},
                  "late_commit_observed": false},
  "cleanup": {"terminated": ["node0"], "exit_codes": {"node0": 0},
              "log_files": ["node0.log"]}
}
```

`phases[*].status` is one of `passed`, `failed`, `skipped`. Sanitization: the report
never includes private key material, `node_key.json` / `priv_validator_key.json`
contents, or key file paths; node IDs from `show-node-id` are public.
`assert_sanitized()` rejects report text containing the forbidden markers
`priv_validator_key.json`, `node_key.json` or `PRIVATE KEY`. `validate_report()`
refuses `status == "PASS"` when any failure or non-passed phase is present, and refuses
`status == "FAIL"` with no recorded failure.

### Exit codes

`0` = all phases passed and report written; `1` = incomplete/error/timeout (report still
written, failure listed); `2` = usage error (missing/relative/nonexistent `--binary`,
existing non-empty `--output`, non-positive `--whole-timeout`).

### Test contract (`tests/test_comet_lab.py`, subprocess-free)

Unit tests never launch `cometbft` and never leave loopback. Required cases:

- binary argument: relative path refused, missing file refused, directory refused.
- output directory: existing non-empty refused with its sentinel file preserved;
  missing created including parents; existing empty reused.
- version gate: `0.38.26` accepted; `0.38.25`, empty and unrelated stdout refused.
- TOML edits: applied inside the correct section; an identically named key in another
  section untouched; zero matches fatal; duplicate matches fatal; result still parses
  with `tomllib`; unrelated lines byte-identical.
- `persistent_peers`: excludes self, contains the other 3 IDs at their own P2P ports.
- port allocation: 8 values, all distinct, all loopback.
- deadline: `remaining()` reaches 0, `bound()` raises `LabTimeout` after expiry and
  clamps to the remaining time before it.
- report: `PASS` only when every phase passed and no failure recorded; `FAIL` otherwise;
  `validate_report()` rejects contradictions; JSON round-trips; sanitized text free of
  forbidden markers; `not_proved` labels present.
- cleanup on injected failure: a scenario failure terminates and waits every started
  child, closes every log handle, and yields nonzero exit plus a `FAIL` report.

   - `([p2p], allow_duplicate_ip)` -> `true` (lab-only same-IP support)
   - `([instrumentation], prometheus)` -> `false`
   - `([rpc], pprof_laddr)` -> `""`
4. All nodes start with `--proxy_app persistent_kvstore` so state survives
   restart via the `data/` directory in each node home.

