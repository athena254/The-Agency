> ARCHIVED DRAFT — preserved for completeness, not an approved implementation contract.
> Historical platform/status claims below may be superseded. See ../../COMET_LAB_VERIFIED_STATUS.md
> and ../../FREE_PROGRAM_BUILD_STATUS.md for current verified status.

# BFT lab spec V1 — DESIGN ONLY, non-actuating (Wave B lab gate)

Status: RESEARCH/DESIGN ONLY. No engine selected/installed, no services
started, no source/tests touched. Owner owns ONLY this file
(`docs/SPEC_BFT_LAB_V1.md`). Program:
`docs/SPEC_FREE_ONLY_DECENTRALIZED_PROGRAM.md` (RESEARCH/PROPOSED, no
BFT engine selection/actuation approved). License per `pyproject.toml`:
MIT, `theagency` 0.1.0, Python `>=3.11`.
Host worktree: branch `research/free-bft-lab` @ base `18b9717`;
`git status --short` clean except untracked `.clineignore`,
`WORKER_BRIEF.md`. Target `docs/SPEC_BFT_LAB_V1.md` did not exist
(`Get-ChildItem docs` 2026-09-28 listing). P-1/P-2/P-3 confirmed:
echo-provider only, no installs/live models/push, exact outputs only.

## 1. Non-goals (hard blocks)

- No custom Python consensus (repeats `governance.py` single-lock /
cast-quorum failure modes per `docs/RESEARCH_FREE_PROGRAM_BFT.md` §1).
- No Raft/CFT-majority, single-peer/Butler/Telegram/user-string, KV-store
or signed-vote-count claims as BFT. Engine commit ≠ app authorization.
- No real actuator/import path. Lab is hermetic synthetic state machine
only; §12 actuation stays disabled until §10/§20 gates close.

## 2. Lab topology (proposed, not provisioned)

- 4 isolated peers: separate processes, keys, stores, loopback endpoints
only (`127.0.0.1`, fixed ports), genesis-digest pinned. Equal weight,
tolerates f=1 (PROPOSED, owner has not approved n/f).
- Layers: (a) maintained BFT engine sidecar = ordering + finality proof
(real Propose/Prevote/Precommit rounds, >2/3 commit, lock/unlock,
gossip catch-up); (b) deterministic Agency checker per protocol §11.2
(signatures, epoch/root, affected coverage, policy version, expiry,
conflicts, fencing/idempotency); (c) synthetic-resource fixture showing
one valid authorized transition + durable idempotency, plus deny paths.
- Success = positive progress AND refusals with converged evidence. An
always-refuse harness is explicitly insufficient (program §8 review).

## 3. Required fault/negative tests (hermetic, no live net/model)

Positive synthetic authorized transition persists; then: invalid schema /
kind-swap / forged / tampered / noncanonical / duplicate / stale-epoch /
unknown-root / expired all rejected; equivocating voter + conflicting
certs same idempotency scope execute ≤once; single-ballot pseudo-quorum
rejected; missing affected consumer blocks; kill-9 proposer mid-round
then liveness via remaining 3/4; minority partition (1+3 or 2+2) both
sides refuse conflicting finals; heal converges without duplicates;
kill-9 mid-grant-persist reconciles without re-execution; audit-append
failure blocks success claim. Mocks alone insufficient for transport
auth; needs real-transport review before any authority claim.

## 4. Command slots — ALL UNVERIFIED (do not run as written)

No reproducible startup/stop/fault/partition/convergence command is
verified from official release/docs in this pass (fetches returned only
release-tag list `v0.38.26/v0.39.4/v0.40.0` and generic docs shell, no
exact `testnet`/`compose`/install stanza captured). Pinned engine
version, license text, binary/docker/Go download, and Windows support
are therefore recorded UNVERIFIED. Slots for a future verified pass:
`lab-init` (keys/genesis), `lab-up` (4 peers), `lab-stop`, `lab-fault`
(kill proposer), `lab-partition`/`lab-heal`, `lab-converge` (evidence
export). Each must cite exact official doc lines before use.

## 5. Verified platform blockers (exact outputs, 2026-09-28)

- Docker CLI present (`29.7.2`, `windows/amd64`, `docker.exe` under
`C:\Program Files\Docker\Docker\resources\bin`) but daemon DOWN:
`docker version` → `failed to connect to the docker API at
npipe:////./pipe/dockerDesktopLinuxEngine ... The system cannot find
the file specified`. Linux-engine pipe absent → container path blocked.
- `Go` NOT on PATH, `cometbft` NOT on PATH
(`Get-Command go, cometbft` returns nothing). Native binary path blocked.
- Interpreter available: `C:/Users/alphi/agency-mvp/.venv/Scripts/
python.exe` → `Python 3.11.16`. Python harness authoring possible;
engine execution is not.
- Viable setup recommendation: start Docker Desktop Linux engine OR
provision Linux host/VM with Go toolchain; then re-verify pinned
CometBFT version/license/docs commands from official release page
before writing executable lab steps. Same-host 4-process runs prove
protocol logic only; multi-host with real keys/transport/skew/stores
required before any authority claim. No deployment from green lab alone.

## Coordinator verification update

Owner approved the non-actuating lab at program checkpoint 18b9717; production actuation remains forbidden. The executable lab is still unbuilt/unverified.

Direct official tagged-source retrieval succeeded for candidate v0.38.26:
- https://raw.githubusercontent.com/cometbft/cometbft/v0.38.26/docs/guides/install.md documents `go install github.com/cometbft/cometbft/cmd/cometbft@<version>` and `cometbft version`.
- https://raw.githubusercontent.com/cometbft/cometbft/v0.38.26/LICENSE is Apache-2.0.
- https://raw.githubusercontent.com/cometbft/cometbft/v0.38.26/go.mod declares Go 1.22.11.
- Release API lists v0.38.26, v0.39.4 and v0.40.0 without platform binary assets. Do not invent a download URL.
- WSL is not installed. Docker startup alone may therefore not solve the missing Linux backend.

A portable official Windows Go archive is being downloaded to a task-local temporary directory with SHA-256 validation against official metadata. This avoids global PATH changes, OS setup and reboot. Native CometBFT build/run compatibility remains unverified until exercised; this is an alternative attempt, not an assertion of supported production deployment. No code generation or substitute consensus implementation is authorized to hide a build failure.
