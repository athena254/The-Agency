# The-Agency — Fully Working v1 — Execution Plan

**One-line definition (agreed with user):** single-host full v1 — 4 independent CometBFT 0.38.26 peers on this machine; human → Butler → grants → actuators → durable results → consumer input. **The single-host sim topology runs the whole pipeline; 'full' means the product surfaces are real, not mocked.**

Status notes (verified on this box): glrl-3 absent, no /opt/workspace, no `tokengears/`, no Docker, single sim machine ("nothing else just this machine"). No independent hosts. WIP exists on this branch; must land first.

---

## 1. What "fully working v1" means — concrete deliverables

| # | Deliverable | Evidence | Status |
|---|---|---|---|
| D1 | Four live peers: real `cometbft start --proxy_app AgencyApp` × 4, independent keys, distinct node dirs | lab harness `src/agency/lab/comet_smoke.py:36,535,592,602` | This box, all new sims |
| D2 | End-to-end: human → Butler → granted by quorum → actuator executes → durable result visible to human (Bucket + Studio) | `src/agency/consensus/grants.py:3-10`, `src/agency/consensus/app_state.py:118-134` | Missing today |
| D3 | Quorum proof: 4/4 `/status` height ≥ 1; `tx4`; `/commit` app-hash equality on 3+ | `src/agency/lab/comet_smoke.py:839,592` | Lab-ready |
| D4 | Restart catch-up: restart node3 → real commit + independent `abci_query`; fresh commit after restoring 2,3 (restart_catchup + restore phases) | `src/agency/lab/comet_smoke.py` | Lab-ready |
| D5 | Quorum loss: kill 2,3 → 20s stability: `tx_found=false`, `height_unchanged=true`, no commit (quorum_loss phase) | `src/agency/lab/comet_smoke.py` | Lab-ready |
| D6 | Actuator-side grant enforcement: invalid/replayed/stale/foreign grant → rejected at execution point | NEW: `grants.py` has no consumers today; tests to be written | Missing |
| D7 | Beta: `lite`/`lite-beta` on both dev (this box) and remote; live-model smoke passes (Gemini free, owner-pinned mercury-3 as fallback) | Beta tasks | Partially: TTY cryptography → do D7 after Wave 1 |
| D8 | L268 (Beta) HTTP /v1 on 18 port return health, v1 surface set complete; L268 bills 90 calls, 60d, QPS 60 (user) | Server v2/day-ops — Phase H | Missing |
| D9 | FeatureSet (define-ids 1-3): visual agency row, web routes, etc. | **Not in v1 — Out of v1 by definition.** | N/A |
| D10 | Risk mitigations committed: sun-byte/qps (user), telemetry, no blind retry on UNCERTAIN | mitigations as defined, committed during waves | Missing |

**NOT in v1 (out of scope by definition, written here to be honest):** glrl-3 enterprise node; MCC v2+ (MCC-485); SSEB; consumer proof graphs; glrl-3 product naming (MUNCH names are a later wave); independent production hosts.

---

## 2. Baseline inventory (verified today)

**Canonical repo** (`C:\Users\PC\The-Agency`):
- Branch `feat/self-improve-forge` @ `5c798e9`; **11 commits ahead of `origin/main`; no push, no PR, no CI on this branch** (`ci.yml` triggers on master/main/develop + PRs only).
- **Uncommitted WIP**: `src/agency/forge/test_runner.py` (+ new `tests/test_forge_test_runner.py`) — never validated (Wave 0).
- **Other uncommitted**: `.zcode\worktrees\5ab0\The-Agency` @ `15c0554` (branch `fix/peer-ballot-equivocation`): new **uncommitted ABCI app + codec + 3 test files** — green (ruff/mypy/pytest at 06:08). Test bug vs codec: `worktree/tests/test_abci_codec.py:10,96-100` expects `Header` in `abci/codec.py`, which lacks it (`worktree/abci/codec.py:187-194`).
- `agency-slice-c` worktree: uncommitted `tests/test_llm_google.py` downgrade (117 deletions) — decisions: revert (suggested) or branch it (PR 2).

**This machine (dev box):**
- No glrl-3 anywhere, no `/opt/workspace`, no `tokengears/`, no Docker.
- Closed: `/v1/*` → 503 except health (`src/agency/api/authority.py:13-18`); **6/14 GUI panels locked** (`gui/src/routes/Locked.tsx:4-35`, launch spec in `docs/CURRENT_STATE.md:41`).
- Stale: `docs/CURRENT_STATE.md:7` (wrong commit), `:89` (capability "proposed only" — it's a library), `:104-112` (wrong branch); `ROADMAP.md` missing all new items.
- Known issues: KI-01 (Windows harness, OPEN), KI-03, KI-05, KI-06, KI-07.

**User answers recap (from "scoping questions" #1):** full v1 = single-host 4-wide MCC; MCC-485; dev+remote all sims; same box for everything; on this machine; glrl-3 at Wave 1 (gate); hyper all needed? (docker needed for glrl-3? — we say "if glrl-3 exists, install; else, no docker"); >90 calls on remote (user); eager to start: "I want this whole plan to work where this thing is being used, really bad."

**Approach decision (capture):** since remote is "another dev box running an identical sim": Wave 1 = dev-box lab (this machine); Wave 1.5+ = remote sim provisioning (same scripts, repeatable). All everything is reproducible with the same scripts, so the plan says: "One machine = fully working v1. Remote = same scripts + no extra code; each sim host is a v1 instance."

---

## 3. Execution waves (Order is real; gates: green → next wave)

### Wave 0 — Canonical recovery: land ABCI + green branch (1–2 weeks)

**Deliverable:** `feat/self-improve-forge` is clean and green on CI.

Steps:
1. Land ABCI from `.zcode\worktrees\5ab0\The-Agency`: git apply uncommitted files into canonical; **fix test bug**: add `Header` struct + `FinalizeBlock.header` to `worktree/abci/codec.py` (per `worktree/tests/test_abci_codec.py:10,96-100`). Commit.
2. Land WIP: `test_runner.py` + `tests/test_forge_test_runner.py`; validate it works (`pytest tests/test_forge_test_runner.py`).
3. Revert or branch: `agency-slice-c` test downgrade → revert (suggested: restore from `HEAD:tests/test_llm_google.py`).
4. Hotfix backlog (fix-c fixes): KI-01: shorten the two multi-thousand-char param IDs (`KNOWN_ISSUES.md:9-12`); KI-03: check/migrate Starlette TestClient as needed; KI-05: patch gui bearer send to prototype reads; KI-06: note Telegram wiring as "not now" boundary.
5. Baseline update: `CURRENT_STATE.md` → Nov 19 reality; `ROADMAP.md`: add ABCI/Codec items; `KNOWN_ISSUES.md`: document KI-07 as "slated for Wave 1".
6. CI: add `feat/self-improve-forge` to triggers (`.github/workflows/ci.yml:3-8`).

**Exit gate:** CI green on this branch; `.pytest_cache/v/cache/lastfailed` clean or explain-stale; `loop.db` — delete + gitignore, or commit with note.

### Wave 0.5 — Engine bring-up (1–2 weeks)

**Deliverables:** engine on 4 real processes; this box = 4 AAA dirs.

Steps:
1. **glrl-3 gate** (user: glrl-3 at Wave 1): verification on this box (`glrl-3` in PATH? if not: document fallback — engine-lab-on-this-box).
2. `C:\opt\workspace\toasthead\nhnamespaces` — create dirs (worktree "of": nhnamespaces). 4-peer AAA: `glrl-3` nodes 0..3, distinct homes (`C:\opt\workspace\toasthead\nhnamespaces\`), ports distinct (2049+i), distinct keys (`testnet`).
3. **Config edit template** (real, per lab script): `cometbft 0.38.26`; per-node `config.toml`: `[p2p] persistent_peers = "ID0@tcp://127.0.0.1:P0,...,ID3@..."`, `seeds=""`, `pex=false`, `addr_book_strict=false`, `allow_duplicate_ip=true`; `[rpc] unsafe=false`, `prometheus=false`, `pprof_laddr=""`.
5. Lab PASS: `version`, `testnet`, `start4`, `tx4`, `abci_query(v4)` on all 4, `/commit` app-hash equal at H4+1, `stop1_commit3` (tx `probe=v5` commits > H4 at height > H4), `restart_catchup`, `quorum_loss` (kill 2,3 → 20s: no `/tx`, heights unchanged), `restore`.

### Wave 1 — Engine binds to v1: 2 real actions demo (2–4 weeks)

**Deliverable:** v1 demo: 2 real actions (engine-granted → executed → durable), all proofs green.

Steps:
1. **Engine adapter** (1 new module): real engine events → grants → `AgentExecutor`/`LatticeAppState.apply_block` + durable write via `LatticeBackend` (`lattice/backends/sqlite.py`).
2. Real action path (two): on request → chain → executed → shown to human (dashboard/history).
3. Proofs: valid grant executes (valid-inst); replay → reject (`Replay` at `authority.py`); foreign/stale grant → reject at actuator (D6 — new `grants.py` consumer + tests).
4. Gates: full pytest + ruff + mypy + lab PASS (from D3–D6) + e2e demo transcript.
5. **Second machine provisioning** (if glrl-3 available): same scripts on remote sim.

### Wave 2 — glrl-3 + beta (1–2 weeks, when glrl-3 exists)

1. Beta `lite`/`lite-beta` on this box + remote sim.
2. Risk mitigations (as decided: sun-byte/qps, telemetry, no blind retry on UNCERTAIN).
3. Tier docs: tier 1 = this box; tier 2 = remote sim.

### Wave 3+ — SSEB, bigger (deferred, documented for when it comes)

### Costs/timeline
- Wave 0: ~1–2 weeks
- Wave 0.5: ~1–2 weeks
- Wave 1: ~2–4 weeks (deepest)
- Wave 2: ~1–2 weeks

---

## 4. Risk register (real, honest)

| Risk | Mitigant | Today |
|---|---|---|
| glrl-3 unverified | Gate 0.5: `glrl-3` version check; if missing → dev-box 4-peer lab (same proofs). If present → install per its spec. | ABSENT on this box (need user to confirm). |
| no independent hosts | All work is reproducible on this box; remote sim = same. | Documented in §2. |
| Version drift (cometbft, gemini) | pin: `cometbft 0.38.26` (lab script), `llm/config.py` provider pins (D-05). | Real pins verified. |
| Terminal regressions | lab asserts app-hash equality; flaky timeouts are fail-fast. | Lab script + its asserts. |

---

## 5. Loose-end decisions (to tie now)

1. **branch naming of ABCI work**: land into `feat/self-improve-forge` (canonical, keep branch of record; don't create new branch). Suggested PR: "feat: land ABCI app + codec, fix codec tests" — **PR 1**.
2. **`loop.db`**: delete + `.gitignore`, or commit — decide: plan says `.gitignore` it (runtime DB) — hmm, user said "commit it or delete it": plan: **gitignore + move off** (it is `data/` now? — land it in `data/`).
3. **slice-c downgrade**: revert via `git checkout HEAD -- tests/test_llm_google.py` in worktree — **PR 2**.
4. **glrl-3 on this box**: user asserts present; we verified absent. → decision **A**: run dev-box lab this box (4-peer engine lab), and when glrl-3 exists: bring it to same v1 spec (gates unchanged). **Decision B (user to confirm)**: for Wave 2, plan for: remote dev-sim + "bigger v1": add SSEB level (second tier) as described in §1 D9.
5. **Windows box + POSIX 4-way AAA**: per lab config: `pex=false`, `allow_duplicate_ip=true`, `persistent_peers` set — lab-ready.

---

## 6. Execution order (high level, user can cut waves)

```
Wave 0: land ABCI + green CI (canonical)
     │  → exit: CI green, main branch of record
Wave 0.5: engine bring-up (dev = 4 peers, glrl-3 gate)
     │  → exit: D3, D4, D5 PASS on this box
Wave 1: v1 demo (2 real actions, valid/replay/foreign proofs)
     │  → exit: v1 usable, D2, D6 green, lab PASS
Wave 2: glrl-3 + beta (lite/lite-beta) + mitigations
     │  → exit: beta smoke green (only if glrl-3 exists)
```

**Total: ~5–7 weeks of focused effort, with checkpoints: "fully working v1?" — yes/no + any holes found → iterate.**

Next step: start with Wave 0 — "land ABCI + green CI".
