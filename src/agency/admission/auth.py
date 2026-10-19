"""Explicit local-owner authentication; no environment or Telegram fallback."""

from __future__ import annotations

import secrets

from pydantic import SecretStr

from agency.admission.models import OwnerPrincipal, PilotError


class OwnerAuthenticator:
    def __init__(
        self,
        *,
        owner_id: str,
        subject_id: str,
        bearer_secret: SecretStr,
        telegram_user_id: int | None = None,
        telegram_polling_marker: object | None = None,
    ) -> None:
        self._local = OwnerPrincipal(owner_id, "local", subject_id)
        if type(bearer_secret) is not SecretStr:
            raise PilotError("invalid_request")
        self._secret = bearer_secret
        self._telegram_user_id = telegram_user_id
        self._telegram_polling_marker = telegram_polling_marker

    def authenticate_local(self, authorization: str | None) -> OwnerPrincipal:
        secret = self._secret.get_secret_value()
        if type(authorization) is not str or not secret:
            raise PilotError("unauthorized")
        expected = ("Bearer " + secret).encode("utf-8")
        try:
            valid = secrets.compare_digest(authorization.encode("utf-8"), expected)
        except UnicodeError:
            valid = False
        if not valid:
            raise PilotError("unauthorized")
        return self._local

    def authenticate_telegram(
        self,
        *,
        user_id: int,
        chat_id: int,
        chat_type: str,
        update_id: int,
        polling_marker: object,
    ) -> OwnerPrincipal:
        if (
            type(self._telegram_user_id) is not int
            or self._telegram_user_id <= 0
            or self._telegram_polling_marker is None
            or polling_marker is not self._telegram_polling_marker
            or type(user_id) is not int
            or type(chat_id) is not int
            or type(update_id) is not int
            or not 0 <= update_id <= 2**63 - 1
            or user_id != self._telegram_user_id
            or chat_id != user_id
            or type(chat_type) is not str
            or chat_type != "private"
        ):
            raise PilotError("unauthorized")
        return OwnerPrincipal(self._local.owner_id, "telegram", str(user_id))
