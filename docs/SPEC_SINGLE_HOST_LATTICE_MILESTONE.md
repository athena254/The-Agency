# Single-host decentralized Lattice — approved target and first slice

Owner requested parallel free OpenCode, Freebuff and Cline work toward this target after approving recovery/metering and the non-actuating BFT lab. This document narrows that existing approval, not production deployment or real-resource authority.

## Product boundary

Butler is NOT an agent, neither smart nor dumb: it is the human gateway to the Lattice. It passes authenticated human requests/input and presents results; it has no independent goals, vote, validator key, planning authority or unilateral execution permission. Agent reasoning belongs to Lattice peers. A supervisor can launch/restart processes, not authorize tasks. Scheduling/coordination roles are replaceable. Missing required human/consumer input blocks execution; no fabricated approval.

Run genuinely separate peers with separate identities, keys and stores on one host. Explicit peer interfaces must remain portable to separate hosts. One host/admin remains a shared compromise/availability domain; single-host testing does not prove independent-host Byzantine resilience.

## Delivery order (do not skip foundation)

1. ENGINE LAB: actual four-process CometBFT consensus on loopback, separate node/validator keys and stores. Synthetic persistent KV fixture only. Submit/query transaction, kill a validator, make progress with remaining validators, restart/catch-up, stop another validator so inadequate quorum cannot commit a new transaction, restore and verify convergence. This demonstrates engine operation, NOT Agency policy authorization or distributed orchestration. No real tools/actuators, private data, production nodes, bot restart, or OS-wide networking changes.
2. APPLICATION CONTRACT: deterministic task/proposal/affected-input/assignment/result state machine; exact schemas and grants specified and reviewed. Real engine orders transitions, each peer validates policy. No standalone central authorization checker that can be bypassed by sending directly to consensus.
3. HUMAN GATEWAY AND TASK PATH: human request -> Butler -> peers -> independently authorized synthetic/read-only task -> durable result -> Butler. Gateway holds no validator key. Killing a coordinator does not lose acknowledged tasks or repeat unknown effects.
4. FAULT PROOF: duplicates/replay, invalid signatures, missing consumer, dishonest peer, crash at durable boundaries, stale assignment, recovery and bounded overload. No full-decentralization claim until authority paths and fault assumptions are verified.

## First-slice interfaces and scope

New Python package `src/agency/lab/` only; no imports into production entrypoints yet. It is a deliberate opt-in laboratory CLI, not unused production code.

`python -m agency.lab.comet_smoke --binary <absolute executable> --output <new dedicated lab directory>` performs the bounded engine scenario and writes a sanitized JSON report. Re-running must refuse a nonempty output directory (never erase/reinitialize signing state). Children must always be stopped in finally, including failure/cancellation. No listening outside loopback; no unsafe RPC, public seeds, telemetry or real user data. Unique bounded ports and deadlines, no fire-and-forget children. Binary version must equal the specified version before startup. Preserve report and node logs (logs contain no prompts or secrets); never include validator private keys in reports/commits. Explicitly distinguish crash/quorum evidence from an unperformed network-partition or Byzantine test.

Executable verified locally: `C:/Users/alphi/AppData/Local/Temp/agency-bft-tools/bin/cometbft.exe`, version `0.38.26`. Official tagged source Apache-2.0; compiled with temporary official Go 1.27.1, archive SHA-256 verified against official metadata. CLI `testnet --help` and `start --help` checked. Go install state remains task-local; no global PATH or OS changes.

`testnet --v 4 --o <dir>` creates independent peer homes, then configure loopback-only unique RPC/P2P addresses, explicit peer IDs, disable PEX/seeds/unsafe RPC and same-IP restrictions as needed for the lab. Use `persistent_kvstore` for restart durability. Inspect actual generated config and binary help rather than guessing TOML keys. Foundation worker freezes exact implementation contract/tests in its branch before coding. No custom consensus or approval-count replacement.

## Parallel work ownership

- Engine worker: `src/agency/lab/__init__.py`, `src/agency/lab/comet_smoke.py`, `tests/test_comet_lab.py`, this slice's detailed lab spec only. No production edits.
- Recovery worker: finish/review existing isolated recovery slice only, following its committed contract. No lab or metering edits.
- Gateway review worker: read-only map of current Butler/orchestrator boundaries and new application contract documentation only. No agent or validator code before foundation and interface review.

All workers: exact verified free model, no paid fallback; separate work area; no `git stash` commands (stash is shared across worktrees), reset/clean/rebase/merge/cherry-pick/push, package installs, secret/private-runtime reads, or edits outside owned paths. Coordinator integrates reviewed explicit commits. Full tests use echo provider. A running CLI is not proof of working model access or completed code.
