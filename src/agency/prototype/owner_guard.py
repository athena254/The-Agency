"""Independent owner HTTP boundary; no app, service, or runtime construction."""

from __future__ import annotations

import ipaddress
import json
import re
import secrets
from typing import Any, NoReturn

from starlette.requests import Request

from agency.admission.auth import OwnerAuthenticator
from agency.admission.models import OwnerPrincipal, PilotError


def _header(request: Request, name: bytes, code: str) -> str | None:
    values: list[bytes] = [value for key, value in request.scope["headers"] if key.lower() == name]
    if len(values) > 1:
        raise PilotError(code)
    if not values:
        return None
    value = values[0]
    if any(byte < 32 or byte > 126 for byte in value):
        raise PilotError(code)
    return value.decode("ascii")


def _authority(value: str | None, scheme: str) -> tuple[str, str, int]:
    if scheme not in ("http", "https") or value is None:
        raise PilotError("forbidden")
    match = re.fullmatch(
        r"(localhost|(?:[0-9]{1,3}\.){3}[0-9]{1,3}|\[[0-9a-f:]+\])(?::([0-9]{1,5}))?",
        value,
        re.IGNORECASE,
    )
    if match is None:
        raise PilotError("forbidden")
    host = match[1].lower()
    if host != "localhost":
        try:
            address = ipaddress.ip_address(host.strip("[]"))
        except ValueError:
            raise PilotError("forbidden") from None
        if not address.is_loopback:
            raise PilotError("forbidden")
        host = str(address)
    port = int(match[2]) if match[2] is not None else (443 if scheme == "https" else 80)
    if not 1 <= port <= 65535:
        raise PilotError("forbidden")
    return scheme, host, port


def _reject_constant(value: str) -> NoReturn:
    raise ValueError("Non-JSON constant")


async def guard_owner_request(
    request: Request,
    authenticator: OwnerAuthenticator | None,
    *,
    csrf_token: str | None,
    write: bool = False,
) -> tuple[OwnerPrincipal, dict[str, Any] | None]:
    """Validate owner ingress before accessing any request body."""
    if type(authenticator) is not OwnerAuthenticator:
        raise PilotError("disabled")
    try:
        peer = request.client
        if peer is None or not ipaddress.ip_address(peer.host).is_loopback:
            raise PilotError("forbidden")
    except ValueError:
        raise PilotError("forbidden") from None
    target_origin = _authority(_header(request, b"host", "forbidden"), request.scope["scheme"])
    principal = authenticator.authenticate_local(_header(request, b"authorization", "unauthorized"))
    for name, value in request.scope["headers"]:
        if re.fullmatch(rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name) is None:
            raise PilotError("forbidden")
        if any(byte < 32 and byte != 9 or byte == 127 for byte in value):
            code = {
                b"content-type": "media",
                b"content-encoding": "media",
                b"content-length": "invalid_request",
                b"transfer-encoding": "invalid_request",
            }.get(name.lower(), "forbidden")
            raise PilotError(code)
    origin = _header(request, b"origin", "forbidden")
    if origin is not None:
        match = re.fullmatch(r"(https?)://(.+)", origin, re.IGNORECASE)
        if match is None or _authority(match[2], match[1].lower()) != target_origin:
            raise PilotError("forbidden")
    if write:
        supplied_csrf = _header(request, b"x-prototype-csrf", "forbidden")
        if type(csrf_token) is not str or not csrf_token or supplied_csrf is None:
            raise PilotError("forbidden")
        try:
            matches = secrets.compare_digest(
                supplied_csrf.encode("utf-8"), csrf_token.encode("utf-8")
            )
        except UnicodeError:
            raise PilotError("forbidden") from None
        if not matches:
            raise PilotError("forbidden")
        media = _header(request, b"content-type", "media")
        if (
            media is None
            or re.fullmatch(
                r'application/json(?: *; *charset *= *(?:utf-8|"utf-8"))? *', media, re.IGNORECASE
            )
            is None
        ):
            raise PilotError("media")
        encoding = _header(request, b"content-encoding", "media")
        if encoding is not None and encoding.lower() != "identity":
            raise PilotError("media")
        length = _header(request, b"content-length", "invalid_request")
        transfer = _header(request, b"transfer-encoding", "invalid_request")
        if transfer is not None and (transfer.lower() != "chunked" or length is not None):
            raise PilotError("invalid_request")
        if length is not None:
            if re.fullmatch(r"[0-9]+", length) is None:
                raise PilotError("invalid_request")
            significant = length.lstrip("0") or "0"
            if len(significant) > 5 or int(significant) > 32768:
                raise PilotError("too_large")
        body = bytearray()
        try:
            async for chunk in request.stream():
                if len(body) + len(chunk) > 32768:
                    raise PilotError("too_large")
                body.extend(chunk)
        except PilotError:
            raise
        except Exception:  # noqa: BLE001 — ASGI receive failures must be sanitized.
            # Transport failures must not expose raw exception/credential text.
            # Cancellation is a BaseException and deliberately propagates.
            raise PilotError("invalid_request") from None
        if length is not None and len(body) != int(significant):
            raise PilotError("invalid_request")
        try:
            decoded = json.loads(body.decode("utf-8"), parse_constant=_reject_constant)
        except (ValueError, UnicodeError, RecursionError):
            raise PilotError("invalid_request") from None
        if not isinstance(decoded, dict):
            raise PilotError("invalid_request")
        return principal, decoded
    return principal, None
