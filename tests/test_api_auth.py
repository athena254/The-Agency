"""TokenRegistry + principal dependency: fail-closed auth for the enabled surface."""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

import pytest
from fastapi import HTTPException

from agency.api.auth import Principal, TokenRegistry, principal_dependency


def _write_tokens(path: Path, entries: list[dict[str, object]]) -> str:
    path.write_text(json.dumps(entries), encoding="utf-8")
    return str(path)


VALID_TOKEN = "sk-test-token-with-at-least-24-chars"
OWNER_TOKEN = "sk-owner-token-with-at-least-24-chars"


def _registry(tmp_path: Path) -> TokenRegistry:
    path = _write_tokens(
        tmp_path / "tokens.json",
        [
            {"token": VALID_TOKEN, "caller_id": "agent-coder-1", "role": "agent"},
            {"token": OWNER_TOKEN, "caller_id": "danny", "role": "owner"},
        ],
    )
    return TokenRegistry.load(path)


def test_load_parses_roles_and_default_scopes(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    principal = registry.authenticate(f"Bearer {VALID_TOKEN}")
    assert principal.caller_id == "agent-coder-1"
    assert principal.role == "agent"
    assert principal.has_scopes("tasks:write", "tasks:read", "chat:send")
    assert not principal.has_scopes("tasks:admin")


def test_owner_role_has_admin_scope(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    principal = registry.authenticate(f"Bearer {OWNER_TOKEN}")
    assert principal.has_scopes("tasks:admin")


def test_authenticate_failures(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    with pytest.raises(HTTPException) as missing:
        registry.authenticate(None)
    assert missing.value.status_code == 401
    with pytest.raises(HTTPException) as bad_scheme:
        registry.authenticate("Basic abc")
    assert bad_scheme.value.status_code == 401
    with pytest.raises(HTTPException) as unknown:
        registry.authenticate("Bearer totally-unknown-token-value-1234")
    assert unknown.value.status_code == 401


def test_empty_registry_fails_closed() -> None:
    registry = TokenRegistry([])
    with pytest.raises(HTTPException) as denied:
        registry.authenticate(f"Bearer {VALID_TOKEN}")
    assert denied.value.status_code == 503
    assert denied.value.detail == "Peer authorization is unavailable."


def test_load_rejects_bad_files(tmp_path: Path) -> None:
    short = _write_tokens(
        tmp_path / "short.json", [{"token": "abc", "caller_id": "x", "role": "agent"}]
    )
    with pytest.raises(ValueError, match="token missing/short"):
        TokenRegistry.load(short)
    dup = _write_tokens(
        tmp_path / "dup.json",
        [
            {"token": VALID_TOKEN, "caller_id": "a", "role": "agent"},
            {"token": VALID_TOKEN, "caller_id": "b", "role": "agent"},
        ],
    )
    with pytest.raises(ValueError, match="duplicate token"):
        TokenRegistry.load(dup)
    role = _write_tokens(
        tmp_path / "role.json", [{"token": VALID_TOKEN, "caller_id": "a", "role": "wizard"}]
    )
    with pytest.raises(ValueError, match="unknown role"):
        TokenRegistry.load(role)
    not_array = tmp_path / "not_array.json"
    not_array.write_text("{}", encoding="utf-8")
    with pytest.raises(TypeError, match="JSON array"):
        TokenRegistry.load(str(not_array))


def test_explicit_scopes_narrow_role(tmp_path: Path) -> None:
    path = _write_tokens(
        tmp_path / "scoped.json",
        [
            {
                "token": VALID_TOKEN,
                "caller_id": "agent-readonly",
                "role": "agent",
                "scopes": ["tasks:read"],
            }
        ],
    )
    principal = TokenRegistry.load(path).authenticate(f"Bearer {VALID_TOKEN}")
    assert principal.has_scopes("tasks:read")
    assert not principal.has_scopes("tasks:write")


@pytest.mark.asyncio
async def test_require_principal_dependency(tmp_path: Path) -> None:
    registry = _registry(tmp_path)

    class FakeState:
        token_registry = registry

    class FakeApp:
        state = FakeState()

    class FakeRequest:
        app = FakeApp()
        headers: ClassVar[dict[str, str]] = {"authorization": f"Bearer {VALID_TOKEN}"}
        state = type("State", (), {})()

    request = FakeRequest()
    principal = await principal_dependency("tasks:write")(request)  # type: ignore[arg-type]
    assert isinstance(principal, Principal)
    assert request.state.principal is principal
    with pytest.raises(HTTPException) as forbidden:
        await principal_dependency("tasks:admin")(request)  # type: ignore[arg-type]
    assert forbidden.value.status_code == 403


@pytest.mark.asyncio
async def test_dependency_without_registry_is_503() -> None:
    class FakeState:
        pass

    class FakeApp:
        state = FakeState()

    class FakeRequest:
        app = FakeApp()
        headers: ClassVar[dict[str, str]] = {}
        state = type("State", (), {})()

    with pytest.raises(HTTPException) as denied:
        await principal_dependency("tasks:write")(FakeRequest())  # type: ignore[arg-type]
    assert denied.value.status_code == 503
