# Telegram beta operator runbook (pre-deployment)

**Status:** review checklist only. Draft PR #9 is not a deployment. The bot observed in the dirty `C:/Users/alphi/theagency` checkout must not be stopped merely because remote `main` changed. No secrets, tokens, chat contents or runtime databases should be committed or pasted into logs.

## Before deciding to release

1. Read `docs/BETA_GAPS.md`. Any open admission, tool, identity, delivery, provider, or rollback blocker means **do not invite testers**. Docker isolation and L2 execution are separately blocked until verified on the host. A text-only L0/L1 trial must be labeled as such.
2. Inspect the exact PR head and wait for all seven GitHub checks after its last push. Review code paths for webhook, polling, direct tool calls and memory. Merge only reviewed commits. Record merge SHA separately from the running process revision.
3. Build a clean, pinned deployment checkout and `.venv` with Python 3.11/3.12 and `pip install -e '.[dev]'` for validation (production install can omit dev extras). Run the offline echo suite, Ruff, mypy and a wheel/smoke test. Never point the bot at the CI echo provider for real replies.
4. Verify one supported real LLM provider/model is configured through `AGENCY_LLM_PROVIDER`/`AGENCY_LLM_MODEL`; check any required key is present **without printing it**. An unknown/default live provider or expired quota is a release blocker. Perform an authorized harmless live smoke separately and record whether it returned an actual answer.
5. Configure `AGENCY_BETA_MODE=1` and a nonempty comma-separated `TELEGRAM_ALLOWED_USER_IDS` of stable numeric user IDs. An empty/malformed invite list must refuse startup. Keep the shared Telegram account's username unchanged. Configure `REMEX_PROFILE_DB_PATH` and `TELEGRAM_POLL_DB_PATH` outside tracked source; neither may be `:memory:` in beta. Set per-user budget/rate limits when integration lands.
6. Polling is the only beta transport in this release proposal; keep webhook mode disabled until a Butler-backed handler passes its own admission, lifecycle, and reply tests (B-12). Enumerate independent Python poller *trees*, including `runpy`/launcher children, rather than counting PIDs. Record each real working directory, code revision or runpy source, Python interpreter and active HTTPS connection without exposing args containing credentials. The deployment checkout previously observed at `42e6ad9` had a venv; the current running child observed under `theagency` had port-443 connectivity, but its executable source revision was **not proven** by that observation.
7. Back up the live memory, nickname, Lattice and poll-state SQLite files using SQLite's online backup API or a fully stopped process; never copy active `.db` alone without WAL state. Verify a restore into a separate temporary directory and file ownership. Do not commit DB/WAL/SHM. Keep the old binary/checkout and start command ready for rollback.
8. Only after replacement interpreter, token source, paths, backup and rollback are verified: stop the old **independent** poller tree, wait for exit, then start exactly one replacement with the same token and runtime data paths. Never run two pollers to compare versions; Telegram 409 Conflict indicates competition, not success. Do not call `getUpdates` as a diagnostic while a poller runs.
9. Verify startup milestones (`butler.started`, command registration, polling started), process liveness and repeated-error threshold. Read-only Telegram API `getMe`, `getWebhookInfo`, `getMyCommands` confirms connectivity/menu only. Ask an allowlisted owner to send `/status`, `/whoami`, `/name`, ordinary chat, and `/research`; capture their actual replies and model/tool status. Test an uninvited sender with consent and confirm no model/tool/profile access. No end-to-end reply is verified until the human reports it.
10. On failed health/reply or memory corruption, stop the new tree and restart the saved old revision with original paths. Restore DB only if the failure changed persistent data and the restore was tested; preserve logs for diagnosis. A rollback is not equivalent to deleting the new branch or rewriting merged Git history.

## Release record template

- PR head SHA / merge SHA / deployed source SHA:
- CI run URL and seven results:
- Runtime Python version and interpreter path (no token):
- Single poller PID tree, source revision and startup timestamp:
- DB path inventory, backup and restore check:
- `AGENCY_BETA_MODE` / allowed ID **count** only; never publish IDs:
- Provider/model names, credential **present?** boolean, harmless live smoke:
- Human Telegram replies to `/status`, `/whoami`, `/name`, chat, `/research`:
- Denied-user probe outcome, repeated errors, rollback handle:
- L2 isolation backend/synthetic-target proof, or explicit **DISABLED**:

This checklist must be updated to match the integrated code; commands and environment names are not substitutes for a real read-back at deployment time.
