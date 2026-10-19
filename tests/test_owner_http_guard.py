"""Real owner authentication/guard exercised through a test-only ASGI endpoint."""

from __future__ import annotations

import importlib
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import SecretStr

from agency.admission.auth import OwnerAuthenticator
from agency.admission.models import PilotError

BEARER = "synthetic-owner-http-guard-token"
CSRF = "synthetic-owner-http-guard-csrf"
AUTH = OwnerAuthenticator(
    owner_id="owner-http-test", subject_id="local-http-test", bearer_secret=SecretStr(BEARER)
)


async def invoke(
    *,
    auth: OwnerAuthenticator | None = AUTH,
    write: bool = False,
    headers: list[tuple[bytes, bytes]] | None = None,
    peer: tuple[str, int] | None = ("127.0.0.1", 12345),
    scheme: str = "http",
    chunks: list[bytes] | None = None,
    fault: Exception | None = None,
    csrf_token: str | None = CSRF,
) -> tuple[int, dict[str, Any], int, list[tuple[bytes, bytes]]]:
    guard = importlib.import_module("agency.prototype.owner_guard").guard_owner_request
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)

    @app.exception_handler(PilotError)
    async def error_handler(request: Request, error: PilotError) -> JSONResponse:
        return JSONResponse(
            {"error": str(error), "code": error.code}, status_code=error.status_code
        )

    @app.api_route("/owner", methods=["GET", "POST"])
    async def endpoint(request: Request) -> dict[str, Any]:
        principal, body = await guard(request, auth, csrf_token=csrf_token, write=write)
        return {"owner_id": principal.owner_id, "transport": principal.transport, "body": body}

    reads = 0
    remaining = list(chunks if chunks is not None else [b"{}"])
    messages: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        nonlocal reads
        reads += 1
        if fault is not None:
            raise fault
        if not remaining:
            return {"type": "http.disconnect"}
        body = remaining.pop(0)
        return {"type": "http.request", "body": body, "more_body": bool(remaining)}

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST" if write else "GET",
            "scheme": scheme,
            "path": "/owner",
            "raw_path": b"/owner",
            "query_string": b"",
            "root_path": "",
            "headers": headers if headers is not None else valid_headers(),
            "client": peer,
            "server": ("127.0.0.1", 8000),
        },
        receive,
        send,
    )
    import json

    response = json.loads(b"".join(m.get("body", b"") for m in messages))
    return messages[0]["status"], response, reads, messages[0]["headers"]


def valid_headers() -> list[tuple[bytes, bytes]]:
    return [
        (b"host", b"localhost:8000"),
        (b"authorization", ("Bearer " + BEARER).encode()),
        (b"x-prototype-csrf", CSRF.encode()),
        (b"content-type", b"application/json"),
    ]


@pytest.mark.asyncio
async def test_disabled_guard_rejects_before_receiving() -> None:
    status, body, reads, _ = await invoke(auth=None, write=True, fault=RuntimeError(BEARER))
    assert (status, body, reads) == (
        503,
        {"error": "Owner pilot unavailable.", "code": "disabled"},
        0,
    )


@pytest.mark.asyncio
async def test_authenticated_read_returns_real_local_principal_without_body_access() -> None:
    status, body, reads, _ = await invoke(fault=RuntimeError(BEARER))
    assert (status, body, reads) == (
        200,
        {"owner_id": "owner-http-test", "transport": "local", "body": None},
        0,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "authorization",
    [
        [],
        [(b"authorization", b"wrong")],
        [(b"authorization", ("Bearer " + BEARER).encode())] * 2,
        [(b"authorization", ("Bearer " + BEARER + "\r\n").encode())],
    ],
)
@pytest.mark.parametrize("body_kind", ["malformed", "oversized", "fault"])
async def test_bad_authorization_is_401_before_body(authorization, body_kind) -> None:
    headers = [(k, v) for k, v in valid_headers() if k != b"authorization"] + authorization
    status, body, reads, response_headers = await invoke(
        write=True,
        headers=headers,
        chunks=[b"not-json" if body_kind == "malformed" else b"x" * 32769],
        fault=RuntimeError(BEARER) if body_kind == "fault" else None,
    )
    assert (status, body, reads) == (
        401,
        {"error": "Owner authentication required.", "code": "unauthorized"},
        0,
    )
    assert not any(k.lower().startswith(b"access-control-") for k, _ in response_headers)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "peer",
    [None, ("192.0.2.1", 12), ("localhost", 12), ("127.0.0.1.evil", 12), ("::ffff:192.0.2.1", 12)],
)
async def test_only_actual_numeric_loopback_peer_is_accepted(peer) -> None:
    headers = valid_headers() + [
        (b"x-forwarded-for", b"127.0.0.1"),
        (b"forwarded", b"for=127.0.0.1"),
    ]
    status, body, reads, _ = await invoke(
        peer=peer, headers=headers, write=True, fault=RuntimeError(BEARER)
    )
    assert (status, body["code"], reads) == (403, "forbidden", 0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "hosts",
    [
        [],
        [b"evil.example"],
        [b"localhost:8000", b"localhost:8000"],
        [b"localhost:evil"],
        [b"localhost:0"],
        [b"localhost:65536"],
        [b"localhost:"],
        [b"localhost:8000/path"],
        [b"localhost@evil"],
        [b"localhost,evil"],
        [b" localhost:8000"],
        [b"localhost:8000 "],
        [b"[::1]evil"],
        [b"::1"],
        [b"[::1]:x"],
        [b"127.0.0.1\r\n"],
        [b"127.1"],
        [b"127.0.0.1%zone"],
    ],
)
async def test_host_is_a_single_strict_loopback_authority(hosts) -> None:
    headers = [(k, v) for k, v in valid_headers() if k != b"host"] + [
        (b"host", host) for host in hosts
    ]
    status, body, reads, _ = await invoke(headers=headers, fault=RuntimeError(BEARER))
    assert (status, body["code"], reads) == (403, "forbidden", 0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "origins",
    [
        [b"http://localhost:8001"],
        [b"https://localhost:8000"],
        [b"http://127.0.0.1:8000"],
        [b"http://evil.example:8000"],
        [b"null"],
        [b""],
        [b"http://localhost:8000/"],
        [b"http://localhost:8000?x"],
        [b"http://localhost:8000#x"],
        [b"http://user@localhost:8000"],
        [b"http://localhost:8000", b"http://localhost:8000"],
        [b" http://localhost:8000"],
        [b"http://localhost:8000\r\n"],
        [b"http://localhost:8000 http://evil.example"],
    ],
)
async def test_origin_must_match_full_request_origin(origins) -> None:
    headers = valid_headers() + [(b"origin", value) for value in origins]
    status, body, reads, _ = await invoke(headers=headers, write=True, fault=RuntimeError(BEARER))
    assert (status, body["code"], reads) == (403, "forbidden", 0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "host, origin, scheme, peer",
    [
        (b"LOCALHOST", b"http://localhost:80", "http", ("127.0.0.2", 1)),
        (b"localhost:443", b"https://localhost", "https", ("::1", 1)),
        (b"[::1]:8000", b"http://[0:0:0:0:0:0:0:1]:8000", "http", ("::1", 1)),
        (b"127.0.0.1:8000", b"http://127.0.0.1:8000", "http", ("127.0.0.1", 1)),
    ],
)
async def test_same_origin_normalizes_host_case_ipv6_and_default_ports(
    host, origin, scheme, peer
) -> None:
    headers = [(k, v) for k, v in valid_headers() if k != b"host"] + [
        (b"host", host),
        (b"origin", origin),
    ]
    status, body, reads, _ = await invoke(
        headers=headers, scheme=scheme, peer=peer, fault=RuntimeError(BEARER)
    )
    assert (status, body["transport"], reads) == (200, "local", 0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tokens, expected",
    [
        ([], CSRF),
        ([b"wrong"], CSRF),
        ([CSRF.encode()] * 2, CSRF),
        ([CSRF.encode()], None),
        ([CSRF.encode()], ""),
        ([CSRF.encode() + b"\x00"], CSRF),
        ([b"\xff"], CSRF),
    ],
)
async def test_write_csrf_is_exact_and_checked_before_body(tokens, expected) -> None:
    headers = [(k, v) for k, v in valid_headers() if k != b"x-prototype-csrf"] + [
        (b"x-prototype-csrf", value) for value in tokens
    ]
    status, body, reads, _ = await invoke(
        headers=headers, write=True, csrf_token=expected, fault=RuntimeError(BEARER)
    )
    assert (status, body["code"], reads) == (403, "forbidden", 0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "media",
    [
        [],
        [b"text/plain"],
        [b"application/jsonx"],
        [b"application/problem+json"],
        [b"application/json"] * 2,
        [b"application/json; charset=latin1"],
        [b"application/json;"],
        [b"application/json\r\n"],
    ],
)
async def test_json_media_is_required_before_body(media) -> None:
    headers = [(k, v) for k, v in valid_headers() if k != b"content-type"] + [
        (b"content-type", value) for value in media
    ]
    status, body, reads, _ = await invoke(headers=headers, write=True, fault=RuntimeError(BEARER))
    assert (status, body["code"], reads) == (415, "media", 0)


@pytest.mark.asyncio
async def test_authenticated_write_decodes_streamed_utf8_json_object() -> None:
    headers = [(k, v) for k, v in valid_headers() if k != b"content-type"] + [
        (b"content-type", b'Application/JSON; charset="UTF-8"')
    ]
    chunks = [b'{"message":"', b"\xe2", b'\x82\xac"}']
    status, body, reads, _ = await invoke(headers=headers, write=True, chunks=chunks)
    assert (status, body["body"], reads) == (200, {"message": "€"}, 3)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"not-json",
        b"[]",
        b"null",
        b'"text"',
        b"123",
        b"true",
        b'{"a":}',
        b'{"a":"\xff"}',
        b"{} trailing",
        b"[" * 2000 + b"]" * 2000,
    ],
)
async def test_body_must_be_a_well_formed_utf8_json_object(raw) -> None:
    status, body, reads, _ = await invoke(write=True, chunks=[raw])
    assert (status, body, reads) == (
        422,
        {"error": "Invalid request.", "code": "invalid_request"},
        1,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("declared", [None, b"2", b"32769"])
async def test_streamed_limit_stops_before_reading_later_chunks(declared) -> None:
    headers = valid_headers() + ([] if declared is None else [(b"content-length", declared)])
    status, body, reads, _ = await invoke(
        write=True, headers=headers, chunks=[b"x" * 16000, b"x" * 16769, b"never-read"]
    )
    assert (status, body) == (413, {"error": "Request exceeds size limit.", "code": "too_large"})
    assert reads == (0 if declared == b"32769" else 2)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "lengths",
    [[b"-1"], [b"+2"], [b"2,2"], [b" 2"], [b"2 "], [b"2.0"], [b""], [b"2", b"2"], [b"2\r\n"]],
)
async def test_malformed_or_duplicate_content_length_fails_before_receive(lengths) -> None:
    headers = valid_headers() + [(b"content-length", value) for value in lengths]
    status, body, reads, _ = await invoke(write=True, headers=headers, fault=RuntimeError(BEARER))
    assert (status, body["code"], reads) == (422, "invalid_request", 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("declared", [b"1", b"3"])
async def test_declared_length_must_match_streamed_bytes(declared) -> None:
    status, body, reads, _ = await invoke(
        write=True, headers=valid_headers() + [(b"content-length", declared)], chunks=[b"{}"]
    )
    assert (status, body["code"], reads) == (422, "invalid_request", 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [RuntimeError(BEARER), ValueError(CSRF)])
async def test_receive_fault_is_sanitized_after_authentication(fault) -> None:
    status, body, reads, _ = await invoke(write=True, fault=fault)
    assert (status, body, reads) == (
        422,
        {"error": "Invalid request.", "code": "invalid_request"},
        1,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra, code",
    [
        ([(b"content-encoding", b"gzip")], "media"),
        ([(b"content-encoding", b"identity")] * 2, "media"),
        ([(b"transfer-encoding", b"gzip")], "invalid_request"),
        ([(b"transfer-encoding", b"chunked")] * 2, "invalid_request"),
        ([(b"transfer-encoding", b"chunked"), (b"content-length", b"2")], "invalid_request"),
    ],
)
async def test_ambiguous_or_encoded_framing_fails_before_body(extra, code) -> None:
    status, body, reads, _ = await invoke(
        write=True, headers=valid_headers() + extra, fault=RuntimeError(BEARER)
    )
    assert (status, body["code"], reads) == (415 if code == "media" else 422, code, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [b'{"value":NaN}', b'{"value":Infinity}', b'{"value":-Infinity}'])
async def test_non_json_numeric_constants_are_rejected(raw) -> None:
    status, body, reads, _ = await invoke(write=True, chunks=[raw])
    assert (status, body["code"], reads) == (422, "invalid_request", 1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "header",
    [
        (b"host ", b"localhost"),
        (b"x-invalid\x00", b"value"),
        (b"x-extra", b"value\r\nInjected: yes"),
    ],
)
async def test_malformed_raw_headers_fail_closed(header) -> None:
    status, body, reads, _ = await invoke(
        headers=valid_headers() + [header], fault=RuntimeError(BEARER)
    )
    assert (status, body["code"], reads) == (403, "forbidden", 0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra",
    [
        [],
        [(b"content-length", b"32768")],
        [(b"transfer-encoding", b"chunked")],
        [(b"content-encoding", b"identity")],
    ],
)
async def test_exact_byte_limit_is_accepted(extra) -> None:
    raw = b'{"text":"' + ("€" * 10918).encode() + b'aaa"}'
    assert len(raw) == 32768
    status, body, reads, _ = await invoke(
        write=True, headers=valid_headers() + extra, chunks=[raw[:100], raw[100:]]
    )
    assert status == 200
    assert body["body"]["text"] == "€" * 10918 + "aaa"
    assert reads == 2


@pytest.mark.asyncio
async def test_invalid_csrf_configuration_is_sanitized() -> None:
    status, body, reads, _ = await invoke(
        write=True, csrf_token="\ud800", fault=RuntimeError(BEARER)
    )
    assert (status, body["code"], reads) == (403, "forbidden", 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("length", [b"32769", b"9" * 5000])
async def test_oversized_declared_length_requires_authentication_first(length) -> None:
    headers = [(k, v) for k, v in valid_headers() if k != b"authorization"] + [
        (b"content-length", length)
    ]
    status, body, reads, _ = await invoke(write=True, headers=headers, fault=RuntimeError(BEARER))
    assert (status, body["code"], reads) == (401, "unauthorized", 0)
    headers += [(b"authorization", ("Bearer " + BEARER).encode())]
    status, body, reads, _ = await invoke(write=True, headers=headers, fault=RuntimeError(BEARER))
    assert (status, body["code"], reads) == (413, "too_large", 0)


@pytest.mark.asyncio
async def test_read_ignores_body_metadata_and_never_receives() -> None:
    headers = [
        (k, v) for k, v in valid_headers() if k not in (b"x-prototype-csrf", b"content-type")
    ]
    headers += [(b"content-type", b"text/plain"), (b"content-length", b"99999")]
    status, body, reads, _ = await invoke(
        headers=headers, csrf_token=None, fault=RuntimeError(BEARER)
    )
    assert (status, body["body"], reads) == (200, None, 0)


@pytest.mark.asyncio
async def test_missing_origin_never_replaces_bearer_authentication() -> None:
    headers = [(k, v) for k, v in valid_headers() if k != b"authorization"]
    status, body, reads, _ = await invoke(headers=headers, fault=RuntimeError(BEARER))
    assert (status, body["code"], reads) == (401, "unauthorized", 0)


@pytest.mark.asyncio
async def test_disconnect_is_a_sanitized_invalid_request() -> None:
    status, body, reads, _ = await invoke(write=True, chunks=[])
    assert (status, body["code"], reads) == (422, "invalid_request", 1)


@pytest.mark.asyncio
async def test_guard_does_not_swallow_cancellation() -> None:
    import asyncio

    from agency.prototype.owner_guard import guard_owner_request

    async def receive():
        raise asyncio.CancelledError

    request = Request(
        {
            "type": "http",
            "scheme": "http",
            "headers": valid_headers(),
            "client": ("127.0.0.1", 12345),
        },
        receive,
    )
    with pytest.raises(asyncio.CancelledError):
        await guard_owner_request(request, AUTH, csrf_token=CSRF, write=True)
