"""Telegram bot entry point."""

from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, ClassVar

import httpx
import structlog

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Load .env before importing config
from dotenv import load_dotenv

load_dotenv()

from agency.agents.demo.agent import DemoAgent
from agency.butler.config import ButlerConfig
from agency.butler.service import ButlerService
from agency.orchestrator import AgencyOrchestrator
from agency.telegram.config import TelegramConfig
from agency.telegram.handler import TelegramHandler
from agency.telegram.profile_store import ProfileStore

logger = structlog.get_logger(__name__)

_POLL_STATE_TABLE = "telegram_poll_state"
_POLL_STATE_KEY = "offset"
# Terminal handler outcomes: safe to advance past. Anything else ("error",
# exception, unknown) stays pending for retry.
_ADVANCING_STATUSES = frozenset({"ok", "ignored", "rejected"})
# Hidden from the Telegram command menu while beta admission disables creation.
_BETA_HIDDEN_COMMANDS = frozenset({"propose_agent", "proposals"})
_MAX_SEEN_IDS = 1000
_MAX_BACKOFF_SECONDS = 30.0


def _extract_update_id(update: Any) -> int | None:
    """Return a valid Telegram update ID, or None for malformed updates."""
    if not isinstance(update, dict):
        return None
    update_id = update.get("update_id")
    if isinstance(update_id, bool) or not isinstance(update_id, int):
        return None
    if update_id <= 0:
        return None
    return update_id


def _is_poll_conflict(error: Exception) -> bool:
    """Detect a 409 competing-poller conflict without leaking tokens."""
    if isinstance(error, httpx.HTTPStatusError):
        response = error.response
        if response is not None and response.status_code == 409:
            return True
    message = str(error).lower()
    return "409" in message and "conflict" in message


class TelegramBot:
    """Telegram bot that connects to the Agency."""

    # The command menu registered with Telegram (setMyCommands).
    COMMANDS: ClassVar[list[dict[str, str]]] = [
        {"command": "start", "description": "Show welcome and command list"},
        {"command": "status", "description": "Live system health"},
        {"command": "agents", "description": "List registered agents"},
        {"command": "research", "description": "Research a topic: /research <topic>"},
        {"command": "propose_agent", "description": "Propose a new agent via governance"},
        {"command": "proposals", "description": "List open governance proposals"},
        {"command": "whoami", "description": "What this bot is"},
        {"command": "name", "description": "Set your private name for this assistant"},
    ]

    def __init__(
        self,
        token: str,
        webhook_url: str | None = None,
        *,
        config: TelegramConfig | None = None,
        poll_db_path: str | None = None,
    ) -> None:
        self._config = (
            config
            if config is not None
            else TelegramConfig(bot_token=token, webhook_url=webhook_url)
        )
        self._orchestrator = AgencyOrchestrator()
        self._demo = DemoAgent()
        self._butler = ButlerService(config=ButlerConfig(), orchestrator=self._orchestrator)
        self._handler = TelegramHandler(
            self._config,
            butler=self._butler,
            profile_store=ProfileStore(
                os.environ.get("REMEX_PROFILE_DB_PATH", "data/remex_profiles.db")
            ),
        )
        self._running = False
        self._offset: int | None = None
        self._seen: set[int] = set()
        self._poll_db_path: str | None = None
        self._poll_conn: sqlite3.Connection | None = None
        self._consecutive_failures = 0
        if self._config.beta_mode:
            resolved = (
                poll_db_path
                if poll_db_path is not None
                else os.environ.get("TELEGRAM_POLL_DB_PATH", "")
            )
            resolved = resolved.strip()
            if not resolved:
                raise ValueError(
                    "beta mode requires a nonempty TELEGRAM_POLL_DB_PATH "
                    "(explicit user-managed absolute path preferred)"
                )
            self._poll_db_path = resolved
            self._open_poll_db()

    def commands_for_mode(self) -> list[dict[str, str]]:
        """Command menu for the current mode; beta hides disabled creation entries."""
        if self._config.beta_mode:
            return [c for c in self.COMMANDS if c["command"] not in _BETA_HIDDEN_COMMANDS]
        return list(self.COMMANDS)

    def _open_poll_db(self) -> None:
        """Open the poll-state DB and resume the persisted offset. Safe to retry."""
        assert self._poll_db_path
        try:
            parent = os.path.dirname(os.path.abspath(self._poll_db_path))
            if parent:
                os.makedirs(parent, exist_ok=True)
            conn = sqlite3.connect(self._poll_db_path)
            try:
                conn.execute(
                    f"CREATE TABLE IF NOT EXISTS {_POLL_STATE_TABLE}"
                    "(key TEXT PRIMARY KEY, next_offset INTEGER NOT NULL)"
                )
                conn.commit()
                row = conn.execute(
                    f"SELECT next_offset FROM {_POLL_STATE_TABLE} WHERE key = ?",
                    (_POLL_STATE_KEY,),
                ).fetchone()
                if row is not None and int(row[0]) > 0:
                    self._offset = int(row[0])
                self._poll_conn = conn
            except Exception:
                conn.close()
                raise
        except Exception as exc:
            self._poll_conn = None
            raise RuntimeError("telegram poll state unavailable; refusing beta start") from exc

    def _persist_offset(self, next_offset: int) -> None:
        """Persist next_offset durably in beta; raise on failure (fail-closed).

        At-least-once only: a local commit can never atomically include the
        Telegram send, so a crash between send and commit may redeliver
        (a duplicate reply is unavoidable); handler commands stay idempotent.
        """
        if self._poll_conn is None:
            return
        try:
            self._poll_conn.execute(
                f"INSERT OR REPLACE INTO {_POLL_STATE_TABLE}(key, next_offset) VALUES (?, ?)",
                (_POLL_STATE_KEY, next_offset),
            )
            self._poll_conn.commit()
        except sqlite3.Error as exc:
            logger.error("telegram_poll_persist_failed")
            raise RuntimeError("telegram poll state persist failed") from exc

    async def start(self) -> None:
        """Start the bot."""
        logger.info("telegram_bot_starting")
        await self._orchestrator.start()
        await self._butler.start()
        self._running = True

        # Register the command menu so Telegram's autocomplete matches
        # what the handler actually supports (beta hides disabled creation).
        commands = self.commands_for_mode()
        try:
            await self._handler._adapter.set_my_commands(commands)
            logger.info("telegram_commands_registered", count=len(commands))
        except Exception as exc:  # noqa: BLE001 — menu is cosmetic, never fatal
            logger.warning("telegram_commands_register_failed", error=str(exc))

        if self._config.webhook_url:
            await self._handler._adapter.set_webhook(
                self._config.webhook_url,
                secret_token=self._config.webhook_secret,
            )
            logger.info("telegram_webhook_set", url=self._config.webhook_url)
        else:
            logger.info("telegram_polling_started")
            await self._poll()

    async def stop(self) -> None:
        """Stop the bot."""
        logger.info("telegram_bot_stopping")
        self._running = False
        try:
            if self._poll_conn is not None:
                self._poll_conn.close()
        except Exception:  # noqa: BLE001 — shutdown must stay safe.
            logger.warning("telegram_poll_close_failed")
        finally:
            self._poll_conn = None
        await self._handler.close()
        await self._butler.stop()
        await self._orchestrator.stop()
        logger.info("telegram_bot_stopped")

    def _backoff_delay(self) -> float:
        return min(2.0 ** min(self._consecutive_failures, 5), _MAX_BACKOFF_SECONDS)

    async def _poll_batch(self) -> str:
        """Fetch and handle one batch. Returns 'ok', 'transient', or 'conflict'."""
        try:
            updates = await self._handler._adapter.get_updates(
                offset=self._offset,
                timeout=30,
            )
        except Exception as exc:  # noqa: BLE001 — classify transport failures.
            if _is_poll_conflict(exc):
                # 409 competing poller: stop instead of retrying invisibly.
                # Never include tokens or update bodies in the escalation.
                logger.error(
                    "telegram_poll_conflict",
                    error="polling conflict (409): another poller holds this bot; stopping",
                )
                self._running = False
                return "conflict"
            self._consecutive_failures += 1
            delay = self._backoff_delay()
            logger.warning(
                "telegram_poll_transient",
                error=str(exc),
                backoff=delay,
                attempt=self._consecutive_failures,
            )
            await asyncio.sleep(delay)
            return "transient"
        self._consecutive_failures = 0
        for update in updates:
            update_id = _extract_update_id(update)
            if update_id is None:
                logger.warning("telegram_poll_malformed_update")
                continue
            if update_id in self._seen or (self._offset is not None and update_id < self._offset):
                logger.info("telegram_poll_duplicate_skipped", update_id=update_id)
                continue
            try:
                result = await self._handler.handle_update(update)
            except Exception as exc:  # noqa: BLE001 — failed send stays pending.
                logger.error("telegram_poll_handler_error", error=str(exc), update_id=update_id)
                break
            status = result.get("status") if isinstance(result, dict) else None
            if status in _ADVANCING_STATUSES:
                next_offset = update_id + 1
                if self._offset is None or next_offset > self._offset:
                    if self._poll_conn is not None:
                        try:
                            self._persist_offset(next_offset)
                        except Exception:  # noqa: BLE001 — durable commit failed.
                            self._consecutive_failures += 1
                            delay = self._backoff_delay()
                            logger.warning(
                                "telegram_poll_persist_retry",
                                backoff=delay,
                                attempt=self._consecutive_failures,
                                update_id=update_id,
                            )
                            await asyncio.sleep(delay)
                            return "transient"
                    self._offset = next_offset
                self._seen.add(update_id)
                if len(self._seen) > _MAX_SEEN_IDS:
                    cutoff = self._offset or 0
                    self._seen = {i for i in self._seen if i >= cutoff}
                    if len(self._seen) > _MAX_SEEN_IDS:
                        self._seen = set(sorted(self._seen)[-_MAX_SEEN_IDS:])
            else:
                logger.warning("telegram_poll_not_advanced", status=status, update_id=update_id)
                break
        return "ok"

    async def _poll(self) -> None:
        """Poll for updates."""
        while self._running:
            try:
                outcome = await self._poll_batch()
            except Exception as e:  # noqa: BLE001 — keep polling after a failed update.
                self._consecutive_failures += 1
                delay = self._backoff_delay()
                logger.error("telegram_poll_error", error=str(e))
                await asyncio.sleep(delay)
                continue
            if outcome == "conflict":
                return


async def main() -> None:
    """Main entry point."""

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        print("Error: TELEGRAM_BOT_TOKEN environment variable not set")
        print("Set it with: export TELEGRAM_BOT_TOKEN=your_token_here")
        sys.exit(1)

    webhook_url = os.environ.get("TELEGRAM_WEBHOOK_URL")

    bot = TelegramBot(token=token, webhook_url=webhook_url)
    try:
        await bot.start()
    except KeyboardInterrupt:
        await bot.stop()


if __name__ == "__main__":
    asyncio.run(main())
