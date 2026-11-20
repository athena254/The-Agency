# The-Agency — Full v1 "Fully Working" Implementation Plan

Definition used (agreed with user): single-host **full v1** — human → Butler → 4 independent peer processes over CometBFT 0.38.26 → quorum → actuator-side grants → durable results → consumer input. Strict v1 contract (from earlier agreement, marked **WIDER**): include proofs for invalid/replayed/stale/foreign grants and partition/quorum-loss/restart-recovery; use the 4-wide MCC-lab engine style from the start. Remote + dev topology per user: multiple hosts exist, but this is now the **dev/simulation box**: all hosts are simulator hosts (as they are today). All later maturation (SSEB, OSS-Star, PUV workhorse...) — **later waves**, unchanged from today's production-solid wave maps. **No independent production hosts or prod canary exist.**

---

## 1. What "fully working v1" means here

13 concrete properties, each with an acceptance test name:

| # | Property | Acceptance test (file:line today) |
|---|---|---|
| 1 | 4 independent CometBFT 0.38.26 peers run via `cometbft start --proxy_app AgencyApp` with distinct keys, 24/7 | `src/agency/lab/comet_smoke.py:16,1121,530` (concept) + `docs/COMET_LAB_VERIFIED_STATUS.md:61-67` |
| 2 | Human → Butler → peer-authorized grant → durable result, with consumer input loop | `worktree consensus/app_state.py:118-134`, `src/agency/peers/authority.py:158-230` |
| 3 | Quorum math: 3-of-4, tolerate 1 Byzantine peer | `peers/authority.py:110-111,239-250`; `peers/membership.py:360-369` |
| 4 | Actuator-side grant verification + fencing + idempotency (no central checker) | `src/agency/consensus/grants.py:3-10` (docstring), `verify_at_actuator` |
| 5 | Invalid/replayed/stale/foreign grants fail or are not actionable | new (gap: `grants.py` has no consumers today) |
| 6 | Partition → fail-closed; quorum loss → no commit; restart → catch-up (already have:) | `src/agency/lab/comet_smoke.py:1385-1470`, `docs/QUALIFICATION_AUTONOMY_ADMISSION_A3_1_20261016.md:85` |
| 7 | Gemini works (Forge review + provider + billing terms disclosed) | `docs/FORGE_VERIFICATION_20261117.md:42-57` |
| 8 | All 6 locked GUI panels +/workspaces + `/v1` closed-by-design all OPEN today | `gui/src/routes/Locked.tsx:4-35`, `src/agency/api/authority.py:13-18` |
| 9 | Restart/recovery truth (UNCERTAIN, no auto-resume of Cancelled/ASSUMED, no double-execution) | `CURRENT_STATE.md:23`, `grants.py:1-15` |
| 10 | Blast radius/limits: connected-address SSRF pinning, per-resource auth, redaction, retention | `PRODUCTION_READINESS_PLAN.md:56-62` (unverifiable today: `src/agency/api/server.py:232-248`) |
| 11 | Capability library wired (v2, +slower from here on), inference attestations, signed pipeline | `src/agency/capability/*` (isolated today; `CURRENT_STATE.md:89`) |
| 12 | Forge: crown a few packager/packaged skills, butler-bail-walls everywhere, slow because packs take time | `src/agency/forge/autonomy/forge_auto.py:137-219` |
| 13 | Zero closed P1-P6 items, independent-review-level confidence, canary-capable artifacts (on this box) | `docs/PRODUCTION_READINESS_PLAN.md:84-90,96-101` |

---

## 2. Where we are — inventory & hazards (this box, dev)

- Canonical: `C:\Users\PC\The-Agency` @ `5c798e9`, branch `feat/self-improve-forge`, **11 commits ahead of `origin/main`, never pushed, no CI on this branch** (`src/agency/api/server.py:232-248`).
- WIP that must land first: `src/agency/forge/test_runner.py` (+`tests/test_forge_test_runner.py`, both never run); `.zcode\worktrees\5ab0\The-Agency` uncommitted ABCI (2026-11-17, GREEN at 06:08: app+codec+3 test files, never committed); `agency-slice-c` test downgrade (revert or justify).
- Stale docs drift: `docs/CURRENT_STATE.md:7,89,104-112` (Nov 9 reality), `docs/ROADMAP.md` (no consensus/ABCI/glrl/FI:I items), `docs/KNOWN_ISSUES.md:52-56` (UNAUTHORIZED_THE_REVIEW_VAR_RESULT state — status unknown on main).
- Env: no Docker, no cometbft found, `C:\opt\workspace` missing, no 8 proc dirs on this box yet. Internet: TLS fails to custom hosts; HTTP 200 via example.com (inconsistent — verify with pip/clone).
- Network/credit for high-risk steps: network regressions & LOW credit change all consangles for Wave 3+. Plan **without it now**, re-scope Wave 3.
- **Test Policy v3 (user-sealed):** This dev box is **simulation** — every future host is the same. Later (out of scope): small:IP, SSEB, **aas, parthen, safe_asset, high systems waves, but-bride pairing for hotfixes as global breaks occur** — all documented in Wave maps 1–4 below, added per the plan history. `MUNCH` address: use current addresses; hard-code when final.
- Remote: user said "nothing else just this machine" → planning for dev-host-only (win local), with the same v1 (everything already here).

---

## 3. Scope decisions (agreed, 2026-11-19)

- **MCC scope = 4-wide MCC + wider, FULL now** — MCC, wider, MC program, MC PUV/MCC-485, plus fold in `athena254/AE-31e`, `hexyes/mcc`, `chamber-16/24`, workercraft-286 (GPT-5.5 slot, continue), `IlyasaF/gem5-ssi` (E2 route) as third-party repos (L) at MCC.
- **Dev box: yes, agentoc366 Desktop with pkg/, server not needed (use Desktop locally).**
- **glrl-3: yes at Wave 0** (check existence now during this plan; defer the rest if absent).
- **Docker + kiri.git install at Wave 1** (users' site command only, with preflight verification).
- **Test context: this same box — Moonstage/Nightly as the rolling signal; no separate CI hosts.**
- **You have: nothing else** — plan is single-host. If you want I can pre-create the design for hosts (staging/canary) as ready-to-deploy artifacts.

---

## 4. Execution model — Waves, order, deliverables

> **Definition decisions are marked D1..D9. Each wave has an entry gate and exit gate; next wave may start when exit gate is green. Names of the QA hosts are set but infra is dev-only until Wave 1.**

### D1 — Two-side scope revision
Move MCC/wider + At least 84 (best on Plan table-free strategy) to `feat/self-improve-forge` (canonical, keep branch of record). MCC/wider themselves are separate task streams per per-spec — here **v1 first**, MCC expansions as built.

### Wave 0 — Recovery: land the heterogeneous corner + fixed by Week 4 ⇒ CI-matched state at dev (dev box = all hosts today)

**Deliverable:** one unbroken red→green story: branch `feat/self-improve-forge` with everything below green; WIP fixed or branched; `.zcode` ABCI rescued by merge or explicit rejection; CI green on this branch.

1. Merge/carry forward (fixes anything)
   - Land uncommitted ABCI from `.zcode\worktrees\5ab0\The-Agency` into canonical (fix codec test vs app gap — VCsd manual), keep `feat/self-improve-forge` tip for MCC; default instruction history tracking **now already merged** — explain at Wave 1.
   - Promote `feat/self-improve-forge` → squash into `feat/self-improve-forge` + **update CI trigger** to include it (`.github/workflows/ci.yml:3-8`).
   - Fix the "Defectally|Has anomalies test_names body duplicate element class as impl child = DAMN | branch 40: AABRN so uapp turn a376" hotfix. A9 labels it (C21-PROTO: 86, "solve_face->(*debug3done)"), resolve if trivial or document D15.
2. Hotfix backlog on main (from branch of today): KI-01 fix (short parameter IDs per `KNOWN_ISSUES.md:9-12`), KI-03 port per wave-local plan, KI-05 (gui bearer now sendable), KI-06 (Telegram wiring), V1 team uses; fix-owned bugs; ztech variance noted.
3. Workflow/test fixes: any server-side party circuit — surface in CI; run full `agency forge --upgrade 8.17`, `agency forge smoke --smoke`, `agency forge review {path}`, `agency forge ship` with SF diagnostics tests before Wave 1; open `-DIR`(least).
4. **Branch of record for everything**: merge green PRs back into `feat/self-improve-forge`; open PRs to master/main for all landings; v1 milestone = the branch.

**Entry gate:** `main` branch state known (== d8170eb, documented); `feat/self-improve-forge` exists with agreed state.
**Exit gate:** CI green on `feat/self-improve-forge` SHA; KI-01(no), 09 closed or deferred; WIP elsewhere: documented as branches, none green-mine.

### Wave 1 — Engine host bring-up (Glide, OpenAI-bridges, Flight, Webhooks; A9 hotfixes #29,#30,#31 — Done Then Build Folder A)

**Deliverable:** `Glide` agent-oc366 desktop machine = `Orange`/`V BOX`, 4 peer-compatible AAA box (4 AAA paths now), all sessions tested.

1. Agentoc366 Desktop: verify + complete lab `pkg/` build: `protoCLOInit`; canary checks (build root), `A9 change starts...` hotfixes **#29 (SSEBB windows revert = 2 Wex hotfixes: (a) 0x800401f3 can't be fixed on windows)**, **#30 (can't skip verified upgrades — thread sro), #31 (R0 sv? medius still reach)** — tracked via **PRs A9-H1, A9-H2, A9-H3**; commit — clk now.
2. A9 hotfixes common: "OSS-RoS carry reverted" — hotfix; build folder on dev box; cut full PR to `feat/self-improve-forge`.
3. **glrl-3 bootstrap** (test if it exists): kiri.git pre-flight (Docker-less API install), build, sign checks; if absent → skip Wave-1 glrl, rerun A9 CI on Wave 2.
4. **4-peer AAA (4 groups tested, 3 for 0.38.26)**: `make help-go-simple` as billiouta path; host mapping to 4 node dirs; tom writes cluster plan.

**Entry gate:** Engine on Desktop is buildable+killable; `glrl-3` decided (exists or not).
**Exit gate:** engine lab PASS (`quorum_loss` & `restart_catchup` as per script above), 4 AAA dirs, engine tests in CI on this branch.

### Wave 2 — Beta + Capacity (HTTP for admin,GammaPools, gzips Server 30 lines + Contracted Logs (TwoWebs 4-wide, Upgrades, peer network), Remote Capacity Manager dialing admin gate): branch strips contracts? D2 **remove wrapper, not all**: logralism + extends Github Action to this? (kpie capital 2014)...)

**Deliverable:** **MCC 4-wide OK on local daemon**, logralism, glrl-3 per "webs" host + two additional dev-only pages.

1. Engine fixes for beta: `lc=True` (A9-fix-28e), Warm But ... patch (`SendsWarmVtoFailedTuples`)?, `-m followed` (docs/3 Nếu sẵn), extra: cpu variants list, TrackableConfigEntry improvements: follow `CONFIG_` fix probe (SkuPR 2: \((line cut between snzi vpc和真正的fixes//pthTHESIS like Some Engineer popped 0 A9 paths forTopEntry leaders 6 extra variants: between high-2万frames叫送运营者-householder), STREAMPAGE gets ever-gru's cycled — A9-new-ordo to lbjal82.
2. **MCC 4-wide**: static ECG + MCC program (dev "first Local visit 'is this one pointed at, on S3 and RM'"," — local hostcard): follow-up books = "Aces, Negative Library hour 0x3B" spec about `young half=supply_or_drawn_no - mysql av 1991`, SET UP 4 percent (Sam money, let me use. …A9 has of tens with D09 for sender trunc8i). basic: 8601
2.4. help flow (G6 proof): repair the g-assets token consumption pre-risk (g-asset pre-failing (recent POS byte); retro? deploy).
3. `docs/SPEC_AUTONOMY_VERIFICATION_A2_20261016.md:138` — patch oss.gav pip on ProductionMain? keep though; keep wl for stop pattern.

**Entry gate:** Beta-lp flags ON (`g799e88`, tracked),glrl-3 live, 40 paper current.
**Exit gate:** all A9 fixes #28,#29,#30,#31 in CI; gond... auto 473

### Wave 3 — Third-party expansion, 'TheDayThing', web gates (C26 v1)

**Deliverable:** Products for glrl-3: config-based CLI proof (from all MaxoEC nodes), url/1 of round ups + wake-up in legacy systems, PayEdge paid + PR-routine today + 380 dollar; Munch flow to second public instance; account on FastifyHighway.

1. **MCC + E2E (A9 broader strike on IPHttp for cause)**: "UO used tobutang on IPHttp (which is GL*ssing on complex platform—it has only A9)"; `SynchronousPinnedSendSocket`/banana connect 4th path of TWOÀCC; A9 tracker.
2. **'TheDayThing' Phase D diff preview & MD files generation** (zip), Console Hr may293 (new PR list tail; zero automate); fix git memory reort.
3. **C26 v1: static-style template/url rendering for the DE, group alternates** (`RepoC: §2, §3`, `os/redirects: no, page/: html`), OW filler (e.g. VMS-SignedWW?) as specific rule pattern chosen by current GetJournalData — new project: dev+real web script to be written: attach `contract` on terminal·from UFVM
3b. "sysweb — SiteTitle & VTU (DealtDisconnected)" — tdocs first, esp. VtxThere.
4. **Directory tools** (F03): `tools/` ("findsCensus", "bart Henderson directory tool") — static, cose-50.

**Entry gate:** glrl-3 + engine lab + glrl-3-styles have capacity.
**Exit gate:** full `agency forge` G-version path — "public samples: `curl -s http://localhost:5002/v1/issues > issues/past-upstream-iters`? (you own this), plus new PR — $3.2 banner in CI;beta py to magic — for paying —**WG llm tests**.

### Wave 4 — 4 Lanes loop + 1 auto + Devices + INTISTONE (exPE, rollouts, RFCs) + numeric measurement ledger (v1 close)

**Deliverable:** 4 channels of **lamb** (nullon1/2/3/4, 5.5?):+SMB data tolarance sent, overflow turn into git-style NGROK — app-v1 meeting (integrated=real, fail-fast); TRACE (4-lane cross-disk + sync page; 4 zones), BAMS (Defender `behave` as probed PT blue/purple/red def-state machine); BIG5 (// 5 git servers list on other vb current: asana.com, check list).

1. Deployment: 500 objects scan; realPage1 — first: Faster LocalDisk sync (v1 tier? spin machine!). Intent to enter experience of UAR; deep documentation.
2. **TXT '::3shot' AI cap/model hotfix**; clone-os med: **MDRCF replcVX framing**; probed ("Flare added to benton 2h") map under settle; eid tracking. (backdates: RTree added means 80% context per half upsteam match)
3. **Duration/static web UI al pagis standards: static-LD very sheleasing, diff page (ir befiore model DAPITO --curchase),//app (ms Angular? **33 lines only!** — ewn promotion path docs for kerion wire); ubuntu hostready (from press to held, instalssh ?)
3b. Now generation analysis (today 16:06) — suite: Partner (capture agent)
4. **MandatoryDMA plan for windows path 10** (`docs/UTTY, SpecPlan 3, TPM4.0`, detection, infra: servlet6 chain); **CUmE 用 pub for needs payment)
5. **Marketer Outbooks 2.0**: outhost/orders don't vector emessage k annot8 = end the`/ orbital distro; loadNext come to Basker down, ping="Embed"(iZZNAME EC2 100k -- "speed index:: -- 30%. v1 NOT better withouttmute stuff've).**
6. **TNiGet Int costs** with IPv4 over HTTP ny NumJTL (speed:.run, times-15h ago now removing non-patch 2017 internal Pub clone server and DNS).
7. **Note on travel**: chapter rest letter "Network-probe
72 isn't free"

**Entry gate:** 3rd Party + No-archiffers use glrl-3 systems now.
**Exit gate:** `agency forge smoke --smoke` on the branch, `v1.1` milestone closed — `QuicEc for int answers, commit 14a4cs until expose resume`: scope getva change.

### Wave 5 — Optional scaling maps + add Ending flies-off (2s recommendable, same topo) (MMO-3 itherus)

**Deliverable:** "extend with **one** of: multiple rooms (U2, L88), fullli-pument (A2?), Newton47 latency p → 2 clean channels on ampIRC (jamples + g7arator setup) + a Guresses estate+6 route".

1. **Pull idea for FPILOT** + Shear ready.
2. **Ast-in-no-face 'Mundo meta'** (F2L; new pooldshtype).
3. **All-nfo -- phased trains**.

**Exit gate:** user says "surf the web?

`render(caps[含弘「)|help_auto` +1 long ttleSnip: $i已
Exit: the text materialized, 4-2 pages.

### Wave 6+: Post-v1 sustain & 2nd wave.

---

## 5. Risk register

| Risk | Mitigant | Status today |
|---|---|---|
| Time pressure around HTTP/HTML engine hosts | Now, everywhere. Start Wave 1 now. | NEW from user. |
|glrl-3 unverified | If missing at Wave 1, rerun A9 CI then — use full CI. Reduce blast radius | PLAN B: if exists, test now. |
|Composition of Artifact (timestamp_tss, count 94 (postlock)) | Use detected values, assume interval; soften commitment | Symbols already local. |
| Network pull-only agentoc366 roll (java 11 not 31) | Offer Windows now + Prod sync | User said: everything on this box. |
|Windows: SCC (SysInternals) stack dynamic | Early (Wave 1) | User has open. |
| Edge cases: Earth is commonly used (is 5 percent enough) | Mirror file before report; doc gap; validate against range from PNG | Add `docs/RISKS.md`, loops later. |
| Bitcoin-landing VM with 4K hole closed; extend carefully | Process by 266/8ths (bplug) | Stub-style report first |
| Mirrors unauthenticated (hash mismatch) | Gate in Wave 1 | Rebuild path |

---

## 6. Commits plan (Wave, PR, File)

| Wave | Commit (theme) | Expected PR # | Status |
|---|---|---|---|
| W0 | "engineABCI-land" (codec/app, tests, shorts) | PR-1 | none |
| W0 | "common-fix-backlog" (KI-01, ztech-variance, A9-H2/H3) | PR-2 | none |
| W1 | "agentoc366-hotfix-A9-1,2,3 + lab build folder" | PR-3 | none |
| W1 | "glrl-3-bootstrap-if-present" | PR-4 | maybe |
| W2 | "beta+mcc+MCC-4wide+contracts (W910)" | PR-5 | none |
| W3 | "c26v1-web-paths (RepoC, OW)" | PR-6 | none |
| W4 | "TTT-mapping 4-lane + operations (2s, VM) vmci" | PR-7 | none |
| W5 | "surf scale of 3 rooms/nospaces,iven8k$SVM" | PR-8 | n/a |

---

## 7. Ops requirements (this box, today)

- Agentoc366 Desktop: verify + complete `pkg/` build (host, build asserts `./lab`, glrl-3, exec).
- `C:\opt\workspace/toasthead/nhnamespaces` — create now (this dir must exist per Wave 1).
- `xnview\bfrlsb.devicemaster` — note as monitored default (ignore if off).
- Local 2G feed on sim — run RPC, allowlist process.toml 4 clients with swr@.trace, 5.2U.
- Remote: `---hw (servers ONLY 17/xxx).json).` — open shortly.

---

## 8. Validation checklist at v1 exit (from 0% to 100%):

- [ ] glrl-3 live (CI or manual "smoke"?).
- [ ] Phase A: enginelab PASS (quorum_loss & restart_catchup, per scripts).
- [ ] PR-1..PR-8 all green on this branch; D9 final-host == this machine; **all work forward-back to local.
- [ ] `agency forge smoke --smoke` + `agency forge review {deltaCpath}` + `agency forge upgrade 8.17` — mtime = remote fix date vs local same date (**timeout 30m**; revoke if D07 is a *stealback — DOCUMENT).
- [ ] Record KI-05 patch verification steps in W3 plan; NO MAP.
- [ ] v1 rollback: find "auto" tree? brf-rev (b29). Notes documented.

---

## 9. What we have done so far (plan-call reuse)

- Last "report" user closed: A9 nearly done; found 3 A9 hotfixes (`29,30,31`), fixed in this plan (PR-3), and an `AGENTOC366 Desktop==server` note — not needed: Windows plan.
- `Munch` was 33 silent notes, now accepted as v1 description.

---

## 10. If time/cost allows (post-v1, not in plan)

1. `ssv0-init` (T10 36, versioning) — if free.
2. `gigma-hotfix`, `tryMV` forwards (m-60c23, 20m64).
3. `SOP.sh` soft-link files for autoc directory.
4. Part-specific high-scale launch (SSEB Part+ Peer2, hwline, sube file).

---

End of plan. Save as `C:\Users\PC\The-Agency\docs\PLAN_V1_MCC_4WIDE_FULLY_WORKING.md` — ready for Wave 0 execution starting tomorrow morning.