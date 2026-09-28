# Coordinator review: Comet engine lab candidate

Scope: isolated engine-only harness, not Agency authorization or production.

## Initial verification
- Focused unit suite: 26 passed (earlier independent run).
- Ruff lint/format passed; mypy passed on both lab source files.
- Actual CLI run review-run-01: version, testnet, start4 passed; tx4 failed with RPC -32602 Invalid params; later phases skipped. All CometBFT processes were stopped, verified by process inspection.
- Report: C:/Users/alphi/AppData/Local/Temp/agency-peer-labs/review-run-01/report.json.

## Blocking findings in initial source
1. RpcClient.tx sends bare hexadecimal /tx hash; pinned upstream URI parser requires 0x for hex bytes. RpcClient.call drops error.data, losing specific tx-not-found diagnostics.
2. run_lab has no try/finally around phase loop; KeyboardInterrupt/SystemExit bypass cleanup. Cleanup failures can be ignored and PASS retained.
3. allocate_ports retries OSError forever, outside whole deadline.
4. poll_until may accept an attempt after its deadline and hides permanent RPC errors behind retry timeout.
5. validate_report accepts incomplete/empty/malformed phase sequences in PASS reports.
6. restore compares old committed heights without requiring a fresh post-restoration commit. Historical convergence is not restored consensus liveness.
7. Version check accepts pinned token anywhere in output, rather than exact version string.

All findings were addressed by the corrective checkpoint described in
`COMET_LAB_VERIFIED_STATUS.md`. The free-model regression suite and real loopback runs
were independently exercised; initial failing reports remain in the evidence bundle.
No merge/deploy approval is implied by this engine-only checkpoint. The initial
independent free review attempt timed out exploring upstream and supplied no final
verdict; subsequent bounded runtime and validation subset reviews completed and their
actual verdicts are preserved in `research-evidence/comet-engine-independent-reviews.json`.

Upstream evidence checked locally: pinned CometBFT rpc/jsonrpc/server/http_uri_handler.go handles 0x-prefixed hexadecimal bytes; rpc/core/tx.go returns tx-not-found diagnostic. No private node keys inspected.
