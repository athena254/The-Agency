# Remex personalization — Telegram MVP

## Decision and scope

The Agency's internal Butler service, agent IDs, governance voter IDs, and API remain named Butler. **Remex** is the human-facing default name. A Telegram user can choose their own *presentation nickname*; it is not a new bot, agent, global Telegram profile name, or `@username` change. One global Telegram bot serves every user. Scope: the Telegram gateway only; no anonymous HTTP preferences or Agent Factory changes.

Current gap: `src/agency/telegram/handler.py` hard-codes Butler in `/whoami` and `/start`, while `src/agency/orchestrator.py` grounds ordinary chat as Butler. Incoming Telegram messages already derive a stable `telegram:<from.id>` sender. The HTTP API deliberately ignores caller-supplied identity/context for durable memory. Preserve these boundaries.

## Alternatives considered

- Telegram `setMyName`: globally configured or language-specific, **not per user** (Telegram Bot API). Not usable for private nicknames.
- Reuse SMS conversational memory: retrieval is semantic/best-effort and cannot guarantee a setting is available. Reject.
- One row per Telegram user in a separate SQLite preference store: deterministic reads, simple reset, and persistent across restarts. Use this; do not place preferences in the tracked Lattice runtime DB.

## Behavior

- Default identity is `Remex`. `/start`, `/help`, and `/whoami` use the requesting user's saved name (or Remex), including a short note that Telegram's visible bot account is shared. Keep service-health facts deterministic and unchanged.
- `/name` shows current name and syntax. `/name <nickname>` sets it; `/name reset` removes the preference. The literal `reset` is reserved (case-insensitive). Commands are matched as commands, not arbitrary prefix strings (`/nameother` must not mutate a setting). Optionally accept `/name@BotUsername` in groups only if supported by explicit bot targeting; initial MVP is **private chats only**, where `chat.id == from.id` and `chat.type == "private"`. Reject group rename requests clearly; group replies use Remex rather than another person's preference.
- Validate after trimming surrounding whitespace: 1–32 ASCII characters, first character a letter, remaining letters/digits/spaces/hyphens. Reject newlines, Markdown control characters, emoji, quotes, slashes, control/bidi chars and overlong input before writing. Case and internal spaces are preserved. No nickname becomes an identity/permission token, prompt instruction, filesystem path, or Telegram profile update.
- On normal private chat, pass the resolved, validated presentation name through *trusted Telegram-only context* to the orchestrator. Ordinary tool-capable and classic LLM paths should describe the same assistant name without changing the stable internal Butler identity. Public anonymous HTTP requests must remain Remex/default and cannot set user preferences through payload context. Do not transform/forge the agent's actual output or disguise failures as successful replies.
- If storage fails, do not report a successful rename; surface a short user-facing failure. Existing rows from corrupt/older stores must not be rendered unchecked.

## Storage and API

`src/agency/telegram/profile_store.py`: `ProfileStore(db_path: str)` using standard-library SQLite, with `get_name(user_id: int) -> str | None`, `set_name(user_id: int, name: str) -> None`, `reset_name(user_id: int) -> None`, `close() -> None`. Primary key: stable positive Telegram `from.id`; `name TEXT NOT NULL`. All SQL parameterized; validation is centralized. A row exists only for an explicit nickname; reset deletes it. Ensure parent directory exists; initialize table on first open. The runtime supplies `REMEX_PROFILE_DB_PATH` (default `data/remex_profiles.db`), with tests using isolated temporary databases. Ignore database, WAL, and SHM files in git. Store must survive handler and process reconstruction.

Telegram handler accepts an optional injected ProfileStore for tests. The production bot constructs a file-backed store, registers `/name` in `TelegramBot.COMMANDS`, and closes it at shutdown. Existing stable ID check and chat allowlist run **before** profile writes. Do not introduce any public HTTP profile endpoint until authentication exists.

## Files and tests

- `docs/SPEC_REMEX_PERSONALIZATION.md` — contract and gap matrix (this document).
- `src/agency/telegram/profile_store.py` — SQLite persistence and validation.
- `src/agency/telegram/handler.py` — exact command dispatch, per-user deterministic text, group safety, trusted chat context.
- `src/telegram_bot.py` — persistent profile-store wiring and Telegram menu.
- `src/agency/orchestrator.py` — contextual display name in both LLM paths, default Remex; no change to agent routing/governance.
- `.gitignore` — local database artifacts.
- `tests/test_remex_personalization.py` — command behavior, invalid inputs, two-user isolation, restart persistence, reset, group rejection, no-ID rejection, store failure, LLM identity context without live provider/network.

## Acceptance / rollout

Spec commit before code. Follow RED→GREEN focused regressions, then `ruff check src/ tests/`, `ruff format --check src/ tests/`, `python -m mypy src/`, full pytest on Python 3.11. Commit implementation on an isolated feature branch; push a **draft** PR without merging. Deploy only after reviewed approval/merge. Live Telegram end-to-end replies (`/status`, `/whoami`, `/name ...`) need owner confirmation; API connectivity alone is not proof.

Reference: [Telegram Bot API](https://core.telegram.org/bots/api) (`setMyName` is global/language-scoped, not user-scoped).
