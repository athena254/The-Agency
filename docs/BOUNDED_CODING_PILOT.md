# Bounded coding pilot

## What this slice does

An explicitly commissioned, already approved agent can handle one fixed job through the owner API and Remex: change the trusted fixture `m.py` to the literal assignment `x = 2`. The native planner and AgencyCodingAgent/Forge perform the edit in an isolated Git worktree. The independent static literal-assignment checker records a bound receipt. The source fixture is unchanged.

This is a narrow integration milestone, not general autonomous coding, peer-governance completion, a hostile-code sandbox or permission to merge/deploy. `tests_run=false` and `release_authorized=false` remain explicit in the receipt. A completed task and a passing objective check are different facts.

## Explicit user entry

When an operator has enabled this fixed profile in a separate approved pilot:

```
/code Set x to integer 2
```

The owner/private-chat restriction remains in place. Ordinary messages, pasted metadata and conversational mentions of coding do not enable the coding path. No web coding selector is added by this slice; Remex is the selected owner-facing entry.

The bridge persists the exact typed request before submission and saves the returned task ID immediately. Known work is observed by GET only. An ambiguous acknowledgement can resend only the identical saved request. Saved work takes precedence over changed command text on redelivery. Telegram reply delivery is still at-least-once; this does not promise exactly-once message delivery.

The reply displays the stored objective verdict only after strict receipt validation and task/request/thread/profile binding. A missing or malformed receipt is unavailable, not successful. The model's answer text is not substituted for a checker verdict.

## HTTP contract

- `POST /api/pilot/coding-tasks`: exactly `request_id`, `thread_id`, `profile_id`, `task`. The only profile is `literal-assignment-v1`. Fresh admission returns 202; exact saved replay returns 200. A changed payload with the same request ID conflicts.
- `GET /api/pilot/tasks/{task_id}/coding-receipt`: read the saved scoped receipt, without rerunning the checker. Noncompleted private evidence remains concealed.
- Existing owner authentication, loopback/Host/origin/CSRF/body guards apply. The request cannot select a repository, command, provider, actor, permissions or checker.

## Trusted operator composition

The existing launcher supports `await agency.prototype.pilot_launcher.serve(settings, coding=executor)` from the executor's creating event loop. `executor` must be the exact `NativeCodingExecutor`, with a pinned `NativeLiteralProfile`, dedicated existing factory/kernel/runtime registries, an approved active identity, pre-existing owner/workspace/thread and fixed trusted fixture/base/checker/criterion. The configured owner and all six nonsecret provider dispatch facts must match. These objects are trusted operator inputs, never HTTP/Telegram fields.

No activation shortcut or automatic identity creation/recreation is provided. The default CLI still calls `serve(settings)` with coding disabled. A factory database marked ACTIVE after a process restart does not recreate an ephemeral runtime identity; new work remains unavailable until separately commissioned. Saved task/receipt replay remains available with coding disabled.

Transferred native work must really settle before its stores and loop close. Cancellation fences publication but is not a hard host/process kill. Shutdown may wait indefinitely; do not close the stores early or clear uncertainty to force a clean exit.

## Executed qualification

`tests/test_factory_coding_socket.py` exercises the real Uvicorn TCP listener and actual owner API, launcher, native Forge, checker and durable stores. It drives Remex with a synthetic Telegram sink and a clearly synthetic model HTTP response; neither a real provider nor Telegram is called. It checks output bytes, original preservation, one model attempt, saved transcript/receipt and reopen/replay with coding disabled, then rejects conflicting and fresh-disabled requests.

This evidence is synthetic end-to-end integration acceptance, not a live-model/live-bot acceptance or installation. Keep live services and historical uncertain work untouched until an explicit rollout with a verified provider, approved identity, backups and rollback is authorized.
