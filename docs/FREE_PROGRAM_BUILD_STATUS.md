# Free-only program — consolidated status

This ledger supersedes the historical worker-allocation and platform-blocker notes.
The owner requested consolidation of all completed work. The main integration includes
verified work only; unfinished source has separate, explicit Git recovery checkpoints.
No runtime database, signing key, credential, private configuration or worker scratch
brief is part of this merge. Merge is not deployment; no bot restart is performed.

## Completed work included

- Research/specification program `18b9717`, approval boundary `842459f`, and
  single-host decentralized milestone `07b2c44`.
- Engine contract `b0971ee` and verified CometBFT loopback lab `9da1922`.
  Four peers, one-peer failure progress, restart catch-up, quorum-loss stop,
  fresh post-restoration commit, fail-closed reports and cleanup are exercised.
- Audit lifecycle specification `9c7a686` and reviewed implementation `1f996e7`.
  Same-instance initialization/closure is serialized; failed persistence does not
  become a successful close acknowledgement. This does not claim append-vs-close
  atomicity or repair every audit cancellation edge case.
- Completed metering specification `b8ad163` and recovery specification `a2ebda1`.
  These are contracts, not claims of completed runtime features.
- Earlier BFT and human-gateway drafts are preserved under
  `archive/free-program-drafts/` as historical, unapproved drafts. Engine/platform
  statements in those snapshots may be obsolete. They are not executable instructions.

## Preserved but deliberately NOT merged as runtime features

- `feat/free-metering` — WIP checkpoint `6f6ee44`. Focused check:
  **6 failed, 28 passed**, including metering/driver integration expectations.
- `feat/free-recovery` — WIP checkpoint `474e8a4`. Focused check:
  **1 failed, 36 passed**; executor does not yet accept the required replay-safe
  configuration. Safe resumption is not operational.

These branches retain the candidate source and tests. They must be fixed, reviewed
and fully tested before their source joins main. Their earlier specification commits
are included; their later WIP commits are not. Never blindly merge those branch tips.

## Architectural boundary

The verified deliverable is an **engine-only, synthetic, single-machine lab**.
It does not yet implement Agency task authorization, affected-consumer participation,
distributed planning/scheduling or the full human-request-to-peer-execution path.
Butler remains the human gateway, not an agent, voter, planner or execution authority.
Same-host peer tests cannot prove independent-host/admin fault resilience or Byzantine
attack handling. Stopping peers is not a network-partition test.

## Free-model provenance

Cline free Muse workers supplied early research/specifications; free DeepSeek V4.1
Flash supplied the later harness work, corrections, regression tests and bounded reviews.
Reported worker costs were zero. Coordinator integration corrections and actual tool
verification are recorded in the lab evidence. No paid coding-worker fallback was used.
OpenCode and Freebuff were attempted but never credited with verified implementation.

## Verification and preservation

Combined verification passed: **1205 Python tests**, **14 UI tests**, repo-wide Ruff
lint/format and mypy. The real four-peer engine scenario passed again from the combined
checkout, and process inspection confirmed no remaining test peers.

See `research-evidence/free-program-consolidation.json` for exact integrated test results,
source fingerprints, engine report, commit coverage, excluded WIP commits and backup
location. The full suite is run only in an isolated integration checkout, never against
the canonical checkout's live data. A consistent SQLite backup and source snapshots
were made before consolidation; live database files remain outside the staged changes.

After all gates pass, main is fast-forwarded to this integration history. No GitHub
push, release, deployment or live-provider enablement is implied by that local merge.
