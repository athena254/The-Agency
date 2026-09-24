# Telegram-first L2 beta — bounded MVP specification

Status: proposed acceptance contract, **not** a claim that beta is ready. Base: merged `main` at `4cf28a7` (PR #8). Normative product direction: `SOURCE_CONSOLIDATED_BRIEF.md`, `SPEC.md` §§16–18, `ROADMAP.md`. Read-only gap review at `e885c5c` and source inspection drive the slices below. The separate dirty `C:/Users/alphi/theagency` checkout and its live bot are out of scope until a reviewed rollout.

## Plain-English goal and boundaries

A small group of invited people can privately message Remex on Telegram, receive honest and reliable replies, research public sources, and keep separate names/conversation history. An uninvited sender cannot reach the model, tools, memory, or agent-creation path. Operators can tell whether the bot is processing updates and can roll back without losing user settings. No GUI. Butler remains the internal service; one shared Telegram bot account remains global. **L2** means controlled testing against synthetic/authorized targets in a verified isolated environment; it does not permit high-impact, production-impacting, or destructive actions. If the isolation backend is unavailable, the L2 feature fails closed while ordinary chat remains usable; never call a process subprocess a hostile-code sandbox.

## Evidence-backed gap matrix

| Boundary | Existing evidence | Beta work |
|---|---|---|
| Telegram admission | `telegram/config.py:33` empty allowlist; `handler.py:48-60` checks chat ID but no invited-user policy | Explicit positive Telegram `from.id` allowlist; private chats only for beta, before any command/model/write. Empty/invalid configuration refuses to start beta mode. Keep non-beta developer fixtures configurable, but never silently fall through in beta. |
| Side effects | `handler.py:244-267,418-443` synthesizes approvals for agent creation | Hide/reject proposal creation and plain-English spawning in beta; no fake votes. Existing read-only status/agents/whoami/name commands may work for invited users. |
| Tool boundary | `orchestrator.py:173-195` registers all tools; `tools/registry.py:142-189` validates schema but not caller policy; `tools/base.py:15-45` has coarse risk only | Explicit beta allowlist and action classification at the tool-call boundary, not only in an LLM prompt. Deny unclassified tools and L3–L5. Disable sandbox_exec, bridges, arbitrary memory writes, unrestricted network fetches until separately verified. |
| Memory isolation | `tools/builtin/memory.py:35-93` accepts arbitrary agent_id/unscoped FTS; `orchestrator.py:186-195` context lacks trusted user scope | Either exclude memory tools entirely for first beta while Butler's owner-scoped history remains, or bind reads/writes to trusted Telegram conversation scope. No cross-user/group fallback; regression against real SQLite store. |
| Delivery | `telegram_bot.py:96-109` advances volatile offset before processing; no dedup | Advance only after a completed handler send; persist processed update IDs/offset or implement a bounded replay-safe design. No duplicate side effect on replay, including rename and disabled mutations. Surface sustained polling/send failures. |
| Reply integrity | `llm/adapter.py:60-108` defaults to live provider; `tools/driver.py:299-307` can return raw invalid model text as completed; `butler/service.py:301-320` returns output without failed status | Explicitly configure a working provider/model for runtime; provider/parse/tool failures return a clearly failed reply, never success-looking echo. CI echo is for offline tests only. Do not promise a live research answer without owner-side probe. |
| Operations | Dockerfile/Compose target different API/env than Telegram bot; audit defaults to in-memory (`orchestrator.py:113-118`) | Document one polling instance, runtime DB paths and backups, startup/restart/rollback, health/log thresholds. Do not publicly expose unauthenticated general API. Persistent audit for consequential actions before enabling any such action. |
| L2 isolation | `orchestrator.py:144-148` uses process backend; `security/sandbox/process.py` warns it is not hostile isolation; local Docker daemon unavailable in this workup | Dedicated opt-in Docker/VM isolated test gate with synthetic targets and resource/network/filesystem restrictions; deny when unavailable. Evidence tests on the actual deployment host before accepting L2 execution. Do not enable ordinary Telegram users to run shell commands in this beta without this gate. |

## Contract, scopes and failures

- Beta mode is opt-in via explicit configuration, not inferred from Telegram API connectivity. Parse stable positive Telegram **user IDs** separately from allowed private chat IDs; deny groups, channels, missing IDs, bool IDs, malformed updates, and not-allowlisted users before profile reads, commands, LLM calls, tools or audit of private content. Use a nonempty operator-configured invite list. No usernames or caller-provided `sender` as authority. Default should fail closed in beta; tests inject temporary IDs.
- `/start`, `/help`, `/whoami`, `/status`, `/agents`, `/name`, ordinary chat and bounded `/research` form the initial surface. `/propose_agent`, plain-English agent creation, unrestricted memory tools, code execution and external bridges are unavailable. Reply with a deterministic refusal for disallowed commands. Rate/budget caps for each invited identity and input length are required before external model use.
- Retain owner-scoped SQLite nickname and Butler memory. Group/private histories must not mix. Store runtime SQLite outside tracked repo artifacts; back up and restore without duplicating a Telegram poller. Keep a stable bot process revision visible to the operator; never print tokens or user conversations into diagnostic output.
- An update is successful only after its response has been sent/acknowledged by the Telegram adapter. Failed sends are retried with bounded backoff or left for safe replay; never silently advance past failures. Any action with external side effects must be idempotent by update ID. Avoid exactly-once claims without a transaction that can cover Telegram delivery (it cannot). Distinguish transient network errors from 409 competing pollers and sustained provider failures.
- Tool authorization is deterministic and happens **before** invoking tool.run. Classification depends on tool, arguments/target and trusted actor; hard-deny L3–L5 in beta, even if a model or forged caller requests them. For L2, require evidence of the actual sandbox, explicit scope, synthetic/approved targets, expiring grant and audit. A tool merely marked READ_ONLY can still fetch internal URLs: validate network targets and redirects or disable the tool.
- Beta must not claim full autonomous Forge, Agent Factory, durable approval workflow, public multi-user HTTP authentication, production sandbox isolation, or GUI. These stay documented gaps. Do not merge unreviewed dirty `main` files or old Dependabot PRs as if they were completed features.

## Build slices and disjoint worker ownership

1. **Spec checkpoint** (this document), then gate foundation: Telegram allowlist/private-chat admission and disabled creation commands (`telegram/config.py`, `telegram/handler.py`, admission tests). Preserve existing non-beta tests; require explicit beta flag in new tests. No worker edits `telegram_bot.py` or tool registry in this slice.
2. **Parallel, once contracts are fixed:**
   - Delivery worker: `src/telegram_bot.py` plus focused polling/dedup tests; no handler/config edits.
   - Tool boundary worker: `src/agency/tools/base.py`, `registry.py`, `builtin/memory.py` plus focused policy/memory tests; no orchestrator/bot edits. Define a minimal callable preflight contract, default-deny in beta without weakening legacy tests.
   - Provider integrity worker: `src/agency/tools/driver.py`, `src/agency/butler/service.py` plus focused failure-result tests; no bot/registry edits.
   - Operational documentation worker: beta operator guide and isolated test fixture, no production code. Confirm actual provider and DB paths, rollback/startup steps.
3. **Integration (single owner):** orchestrator and Telegram wiring for gated tools, scope propagation and deterministic failure output; reconcile all workers, run full suite, lint, mypy, wheel/smoke, review, commit, draft PR. No deployment before all beta gates pass.
4. **L2 controlled-test extension:** independent opt-in specification and worktree after safe conversational beta. Verify Docker/VM on deployment host with network/FS/resources/cleanup tests before enabling L2; without those, release remains read-only L0/L1 and must not be described as an L2 beta.

## Acceptance gates before inviting testers

- Offline contract suite: denied/malformed/group/non-invited updates make **zero** model/tool/profile writes; two invited users remain isolated across restart; authorized private command/chat/research paths return through a fake Telegram transport.
- Real storage/policy tests: no tool call can query another owner or group/private scope; direct registry calls and LLM-driven calls reject missing/revoked grants, L3–L5 actions and private-network/redirect fetches. No synthetic governance votes from untrusted Telegram input.
- Delivery tests: send failure, restart, replayed update, network timeout and competing-poller conflict have explicit outcomes without silent loss or unintended duplicate side effects. Polling offset state and DB artifacts survive restart.
- Provider tests: timeout, HTTP 429, malformed tool response, unknown provider and tool failure show unsuccessful status; successful research returns real citations under an explicitly authorized harmless live smoke (offline echo tests alone are insufficient).
- Release: full Python 3.11/3.12 CI matrix, Ruff, format, mypy, security tests pass at exact PR head; branch reviewed/merged; a clean deployment can restart from verified interpreter/env/DB and roll back; owner confirms `/status`, `/whoami`, `/name`, chat and `/research` replies from the **new revision**. No public API exposure. An operator sees health and repeated failures without exposing secrets.
- L2-specific: actual isolated backend demonstrably prevents host filesystem/secret/network access and cleans up on timeout; synthetic-target test and denial of L3–L5 verified. If not, gate stays off, and beta label explicitly says text/read-only only.

## Alternatives and rationale

- *Cline/OpenCode/Freebuff/Qoder/ZCode as build helpers*: model/CLI access is an implementation aid, not a runtime dependency. OpenCode free endpoints were rate-limited in this workup; Freebuff, Qoder and Cline require user sign-in before code work; ZCode is a desktop harness with account/model-plan setup. Use available free agent in isolated worktrees, never put credentials in briefs or commits, and verify every generated change independently.
- *Anonymous public HTTP*: cheaper to expose, but identity and thread authorization remain incomplete; exclude from beta.
- *Process sandbox*: easier than Docker but does not isolate hostile code on Windows; never accept it as L2 evidence.
- *Synthetic automatic votes*: expedient but not consent; disable until an authenticated explicit vote and durable idempotent spawn flow exist.
- *Strict exactly-once Telegram delivery*: Telegram send and local DB commit cannot share a transaction; use at-least-once processing with idempotency and make limits explicit.

## Approval and rollout

This spec is a review checkpoint. User requested a usable MVP with no GUI and free coding agents; no model access or live rollout is implied by a passing spec. Implement in small reviewed PRs, keeping the currently running bot alive until its replacement, token source, interpreter, DB paths and rollback are verified. A merged commit is not a deployed beta.
