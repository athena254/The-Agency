# Free-only program — implementation ledger

Base specification checkpoint: `18b9717`.

## Owner approval

Owner response to the checkpoint: **Approve recovery/metering work and the non-actuating BFT lab**. Implementation may proceed within that boundary. Production deployment, beta enablement, real-resource actuation, publishing and independent host/key provisioning remain unapproved.

## Worker allocation

All current workers explicitly pin Cline `cline-free/muse-spark-1.3-contributor`. Fresh no-tool smoke returned `FREE_BUILD_READY`, completed, provider-reported totalCost=0 and zero pricing fields. A direct fetch of the public recommended-models endpoint listed this ID in `free`. The web extractor returned a differing catalog; use the direct fetch and actual CLI receipt, not the inconsistent extracted copy. No paid fallback.

- `feat/free-audit-lifecycle`: bounded audit lifecycle specification then TDD fix.
- `feat/free-recovery`: component contract first, then reviewed implementation.
- `feat/free-metering`: dispatch-metering contract first, then reviewed implementation.
- `research/free-bft-lab`: supported-engine/platform feasibility and non-actuating laboratory contract.

Each worktree starts from 18b9717. Workers receive explicit file scopes, offline test configuration and no credentials/runtime databases. Worktrees and ignore files do not constitute a hostile-code sandbox.

## Environment blockers

Docker CLI exists but its Linux engine named pipe is absent. `go` and `cometbft` are not on PATH. `wsl.exe --list --quiet` reports WSL is not installed. The official CometBFT latest-release API currently returns v0.40.0 with no binary assets. These findings are not proof that no supported installation path exists; the worker must verify alternatives. No OS installation, reboot, fake consensus engine or claim of successful BFT execution is permitted to conceal the blocker.

## Integrated result

Pending worker outputs and independent verification. No implementation is counted merely because a process is running or a worker reports completion.
