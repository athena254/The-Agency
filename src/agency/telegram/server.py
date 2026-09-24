"""Telegram webhook server for The Agency."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI, Request, Response
from starlette.requests import ClientDisconnect

from agency.telegram.config import TelegramConfig
from agency.telegram.handler import TelegramHandler

logger = structlog.get_logger(__name__)


def create_app(config: TelegramConfig, handler: TelegramHandler | None = None) -> FastAPI:
    """Create FastAPI app for Telegram webhook."""
    app = FastAPI(title="The Agency — Telegram Webhook")
    _handler = handler or TelegramHandler(config)

    @app.get("/telegram/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "service": "telegram-webhook"}

    @app.post("/telegram/webhook")
    async def webhook(request: Request) -> Response:
        try:
            body = await request.json()
        except (ValueError, ClientDisconnect):
            return Response(status_code=400)
        if not isinstance(body, dict):
            return Response(status_code=400)

        # Verify secret token if configured
        if config.webhook_secret:
            secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
            if secret != config.webhook_secret:
                logger.warning("telegram_webhook_invalid_secret")
                return Response(status_code=403)

        result = await _handler.handle_update(body)
        return Response(content=str(result), media_type="application/json")

    @app.on_event("shutdown")
    async def shutdown() -> None:
        await _handler.close()

    return app
