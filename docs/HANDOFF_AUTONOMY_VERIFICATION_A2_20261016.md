# A2 checkpoint and authentication blocker — October 16, 2026

## Actual status

Research and specification are completed and committed at `b5de5136141c5df03a54d95f30d611eb693082eb`. The owner approved **free-only coding workers and offline verification**, then authorized an attempt to handle Cline sign-in. **A2 runtime implementation has not begun.** Do not treat the specification, baseline passes, login attempt or approval as shipped A2 behavior.

Branch/worktree: `docs/autonomy-verification-a2-20261016` at `C:/Users/alphi/agency-worktrees/autonomy-verification-a2-20261016/`.
Approved contract: `docs/SPEC_AUTONOMY_VERIFICATION_A2.md` as introduced at b5de513.
Research: `docs/RESEARCH_AUTONOMY_VERIFICATION_A2_20261016.md`.

Last implemented baseline remains A1, `c958b874e24938e2a3713963d4d5aa73f21a97ba`:
- src tree `13ba5624a85ed91591a306e84eec337af09556db`.
- tests tree `2cd9d492728377e5197cf9de89e157b940075657`.

Canonical main remains clean and untouched at `713a4e311b1e071fbd6c5cf103b995d17aa7e5a2`. No push, merge, beta enablement, Agency provider call, bot stop/start or deployment occurred. External build-CLI smoke/auth requests are not Agency runtime provider calls.

## Completed milestones

M1: recovered and hash-verified all 20 original evidence files; reran the harmless diagnostic in fresh isolated stores; reproduced dropped/false QA labels, missing accuracy, unearned evidence verification and the injected empty-plan failure. Original evidence untouched.

M2: two independent read-only source audits mapped production consumers and verification/risk/evidence boundaries; reconciled findings into the specification and execution plan. Owner approved free-only implementation after the documentation commit.

Fresh **pre-implementation A1 baseline**, not A2 acceptance:
- 1,706 Python tests passed; zero failures/errors/skips in JUnit.
- One existing Starlette/httpx deprecation warning; not silently suppressed.
- Ruff lint and format checks passed; strict mypy passed over the unchanged source.
- 14 Node UI tests passed.
- Tests used explicit echo provider, isolated source/data/CWD and a Python loopback-only network guard. The guard is not OS/native-child isolation; Node checks were outside that Python guard.
- `docs/research-evidence/autonomy-a2-preimplementation-gates.json` contains exact commands, times and source/test fingerprints.

## M3 blocked: actual free-lane results

OpenCode 1.19.0, official installed native CLI:
- `opencode/muse-spark-1.3-contributor-free`: HTTP 403 FreeTierError.
- `opencode/glm-5.3-flash-free` control: same HTTP 403.
- Neither completed the harmless one-line prompt. No identity spoofing/proxy or service-restriction bypass attempted; no further free-endpoint cycling.

The existing global Cline launcher points to a missing installation. A bounded local npm install of the broad wrapper was stopped without progress; an official pinned native-only package (`@cline/cli-windows-x64@3.0.72`) installed successfully in 11 seconds under Hermes scratch. Global packages were not repaired or replaced. Local executable:
`C:/Users/alphi/AppData/Local/hermes/cache/scratch/agency-a2-cline-native-20261016/node_modules/@cline/cli-windows-x64/bin/cline.exe`.
This is a scratch tool installation, not a durable product dependency; rediscover/reinstall the verified official package if scratch cleanup removes it.

Current Cline free-catalog models probed:
- `cline-free/muse-spark-1.3-contributor`: requires re-authentication.
- `cline-free/mimo-v2.6-flash`: requires re-authentication.
- Neither returned a completed model response. No paid/subscription fallback used.

Owner-authorized authentication attempt:
1. Installed CLI help showed noninteractive provider quick setup requires an API key; none was supplied, read out or guessed.
2. Interactive `auth` selected **Sign in with Cline**, not ClinePass or ChatGPT.
3. Device authorization started; browser automation reached the official Cline sign-in page after one navigation timeout/recovery.
4. `browser_vault list` found no saved login. Its secure save prompt was unavailable in this session (`prompt_unavailable`). No password/code was typed through browser or desktop automation.
5. Device authorization timed out; the owned auth process was terminated. No successful sign-in is claimed.

Do not persist device codes, OAuth session URLs, tokens or passwords in the handoff. Sanitized facts: `docs/research-evidence/autonomy-a2-implementation-blocker.json`. Full code-free smoke/diagnostic logs live at `C:/Users/alphi/agency-evidence/autonomy-verification-a2-20261016/resume-20261016/`; do not publish login process output.

## Safe continuation

The remaining prerequisite is user-completed Cline browser sign-in or a securely provisioned saved login. The assistant cannot invent account credentials, bypass authentication or substitute a paid worker. The CLI authentication entrypoint is the above native executable with `auth` and no positional provider/API-key flags. Never ask for a password, token or code in chat.

After authentication is genuinely completed:
- Recheck approval/scope, Git cleanliness, exact base ancestry and source/test trees; do not touch main/live runtime.
- Rediscover the current official free catalog and terms. Contributor/free tiers may have data-training caveats; exclude credentials/private runtime content and do not assume all free models have the same retention policy.
- Repeat a harmless bounded no-tool smoke using exact free provider/model, compaction disabled and no fallback. Require actual completed output and inspect reported model/cost, not a catalog listing or running process.
- Only then create isolated per-worker branches/briefs with nonoverlapping ownership. Follow the approved schema/signatures and vertical RED→GREEN plan; one writer for shared orchestrator files.
- Preserve red/green evidence, independently review changes, qualify the exact code revision, and give implementation/qualification milestone reports. Do not reuse this pre-implementation test total for changed code.
- M4 (A2 independent review/final qualification) remains unstarted. CI, live-provider validation, peer authority, durable lifecycle and rollout remain separate gates.

No coding workers remain running and no automatic retry job was scheduled.
