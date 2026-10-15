"""Telegram webhook server for The Agency."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from secrets import compare_digest
from typing import Any

import structlog
from fastapi import FastAPI, Request, Response
from starlette.requests import ClientDisconnect

from agency.telegram.config import TelegramConfig
from agency.telegram.handler import TelegramHandler
from agency.telegram.profile_store import ProfileStore

logger = structlog.get_logger(__name__)


def create_app(config: TelegramConfig, handler: TelegramHandler | None = None) -> FastAPI:
    """Create FastAPI app for Telegram webhook."""
    if config.beta_mode:
        raise ValueError("beta webhook disabled until Butler lifecycle and budget/audit are wired")
    if handler is not None:
        _handler = handler
    else:
        profiles = ProfileStore(os.environ.get("REMEX_PROFILE_DB_PATH", "data/remex_profiles.db"))
        try:
            _handler = TelegramHandler(config, profile_store=profiles)
        except BaseException as primary:
            try:
                profiles.close()
            except BaseException:  # noqa: BLE001 - preserve primary failure/cancellation
                primary.add_note("Telegram profile construction rollback also failed")
            raise

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        except BaseException as primary:
            try:
                await _handler.close()
            except BaseException:  # noqa: BLE001 - preserve primary failure/cancellation
                primary.add_note("Telegram lifespan cleanup also failed")
            raise
        else:
            await _handler.close()

    app = FastAPI(title="The Agency — Telegram Webhook", lifespan=lifespan)

    @app.get("/telegram/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "service": "telegram-webhook"}

    @app.post("/telegram/webhook")
    async def webhook(request: Request) -> Response:
        # Authentication precedes body parsing; an unset secret disables the
        # endpoint instead of silently allowing forged Telegram updates.
        if not config.webhook_secret:
            return Response(status_code=503)
        secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not compare_digest(secret, config.webhook_secret):
            logger.warning("telegram_webhook_invalid_secret")
            return Response(status_code=403)
        try:
            body = await request.json()
        except (ValueError, ClientDisconnect):
            return Response(status_code=400)
        if not isinstance(body, dict):
            return Response(status_code=400)

        result = await _handler.handle_update(body)
        return Response(content=str(result), media_type="application/json")

    return app
