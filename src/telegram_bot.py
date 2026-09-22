"""Telegram bot entry point."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import ClassVar

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

logger = structlog.get_logger(__name__)


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
    ]

    def __init__(self, token: str, webhook_url: str | None = None) -> None:
        self._config = TelegramConfig(bot_token=token, webhook_url=webhook_url)
        self._orchestrator = AgencyOrchestrator()
        self._demo = DemoAgent()
        self._butler = ButlerService(config=ButlerConfig(), orchestrator=self._orchestrator)
        self._handler = TelegramHandler(self._config, butler=self._butler)
        self._running = False
        self._offset: int | None = None

    async def start(self) -> None:
        """Start the bot."""
        logger.info("telegram_bot_starting")
        await self._orchestrator.start()
        await self._butler.start()
        self._running = True

        # Register the command menu so Telegram's autocomplete matches
        # what the handler actually supports.
        try:
            await self._handler._adapter.set_my_commands(self.COMMANDS)
            logger.info("telegram_commands_registered", count=len(self.COMMANDS))
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
        await self._handler.close()
        await self._butler.stop()
        await self._orchestrator.stop()
        logger.info("telegram_bot_stopped")

    async def _poll(self) -> None:
        """Poll for updates."""
        while self._running:
            try:
                updates = await self._handler._adapter.get_updates(
                    offset=self._offset,
                    timeout=30,
                )
                for update in updates:
                    self._offset = update["update_id"] + 1
                    await self._handler.handle_update(update)
            except Exception as e:
                logger.error("telegram_poll_error", error=str(e))
                await asyncio.sleep(5)


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
