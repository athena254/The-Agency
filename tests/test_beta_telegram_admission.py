"""Invite-only Telegram admission boundary (beta slice).

Fail-closed opt-in: beta_mode + nonempty positive user-ID allowlist, private
chat with matching chat/user ID, enforced in the common handler before any
profile, command, LLM, tool or governance path.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from agency.telegram.config import TelegramConfig
from agency.telegram.handler import BETA_CREATION_DISABLED, TelegramHandler

INVITED = [101, 202]


def _beta_config(**over: Any) -> TelegramConfig:
    args: dict[str, Any] = {"bot_token": "test", "beta_mode": True, "allowed_user_ids": INVITED}
    args.update(over)
    return TelegramConfig(**args)


def _update(
    user_id: Any,
    text: Any,
    *,
    chat_id: Any = "same",
    chat_type: str = "private",
) -> dict[str, Any]:
    cid = user_id if chat_id == "same" else chat_id
    msg: dict[str, Any] = {"from": {"id": user_id}, "chat": {"id": cid, "type": chat_type}}
    if text is not None:
        msg["text"] = text
    return {"message": msg}


def _handler(
    config: TelegramConfig | None = None, *, butler: Any = "auto"
) -> tuple[TelegramHandler, Any]:
    cfg = config if config is not None else _beta_config()
    b = AsyncMock() if butler == "auto" else butler
    if b is not None and not isinstance(b, SimpleNamespace):
        b.handle_message.return_value = "ok"
    h = TelegramHandler(cfg, butler=b)
    h._adapter.send_message = AsyncMock()  # type: ignore[method-assign]
    return h, b


# --- CHECK-4 / P-T4: malformed or empty allowlist refuses to start beta ---


def test_beta_requires_nonempty_allowlist() -> None:
    with pytest.raises(ValueError):
        TelegramConfig(bot_token="t", beta_mode=True)
    with pytest.raises(ValueError):
        TelegramConfig(bot_token="t", beta_mode=True, allowed_user_ids=[])


@pytest.mark.parametrize(
    "bad",
    [[0], [-5], [True], [False], ["101"], [101.5], [None], ["abc"], [(101,)]],
    ids=["zero", "negative", "bool-true", "bool-false", "str-id", "float", "none", "abc", "tuple"],
)
def test_beta_rejects_non_positive_or_non_int_ids(bad: Any) -> None:
    with pytest.raises(ValueError):
        TelegramConfig(bot_token="t", beta_mode=True, allowed_user_ids=bad)


def test_beta_rejects_string_ids_as_whole() -> None:
    with pytest.raises((ValueError, TypeError)):
        TelegramConfig(bot_token="t", beta_mode=True, allowed_user_ids="101,202")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "raw", ["", "   ", "abc", "0", "-5", "True", "101.5", "101,,202", "101,abc", "101;202"]
)
def test_beta_rejects_malformed_env_allowlist(raw: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENCY_BETA_MODE", "1")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", raw)
    with pytest.raises(ValueError):
        TelegramConfig(bot_token="t")


def test_beta_parses_env_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENCY_BETA_MODE", "1")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "101, 202")
    cfg = TelegramConfig(bot_token="t")
    assert cfg.beta_mode is True
    assert cfg.allowed_user_ids == frozenset({101, 202})


def test_nonbeta_default_ignores_ambient_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "101,202")
    monkeypatch.delenv("AGENCY_BETA_MODE", raising=False)
    cfg = TelegramConfig(bot_token="t")
    assert cfg.beta_mode is False
    assert cfg.allowed_user_ids == frozenset()


# --- P-T1/P-T2: admission rejects before any side effect, both paths ---


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "upd",
    [
        _update(999, "hello"),  # uninvited private user
        _update(101, "hello", chat_type="group"),  # group, admitted user
        _update(101, "hello", chat_type="supergroup"),  # supergroup
        _update(101, "hello", chat_id=777),  # chat/user mismatch (shared chat)
        _update(101, "hello", chat_type="channel", chat_id=101),  # non-private type
        _update(None, "hello"),  # missing user id
        _update(True, "hello"),  # bool user id
        _update(0, "hello"),  # non-positive
        _update("101", "hello"),  # string id confusion
        _update(101, ""),  # empty text
        _update(101, None),  # missing text key
        _update(101, "x" * 5000),  # oversize text
    ],
    ids=[
        "uninvited",
        "group",
        "supergroup",
        "mismatch",
        "channel",
        "missing-id",
        "bool-id",
        "zero-id",
        "string-id",
        "empty-text",
        "missing-text",
        "oversize",
    ],
)
async def test_beta_handle_update_rejects_with_zero_side_effects(upd: dict[str, Any]) -> None:
    h, butler = _handler()
    get_spy = AsyncMock(side_effect=lambda uid: None)
    orig_get = h._profiles.get_name
    h._profiles.get_name = get_spy  # type: ignore[method-assign]
    try:
        result = await h.handle_update(upd)
    finally:
        h._profiles.get_name = orig_get  # type: ignore[method-assign]
    assert result["status"] == "rejected"
    butler.handle_message.assert_not_awaited()
    h._adapter.send_message.assert_not_awaited()  # type: ignore[attr-defined]
    get_spy.assert_not_awaited()
    assert h._profiles.get_name(101) is None
    await h.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "msg",
    [
        {"from": {"id": 999}, "chat": {"id": 999, "type": "private"}, "text": "hello"},
        {"from": {"id": 101}, "chat": {"id": 101, "type": "group"}, "text": "hello"},
        {"from": {"id": 101}, "chat": {"id": 777, "type": "private"}, "text": "hello"},
        {"from": {"username": "alice"}, "chat": {"id": 101, "type": "private"}, "text": "hello"},
        {"from": {"id": True}, "chat": {"id": 1, "type": "private"}, "text": "hello"},
        {"from": {"id": 101}, "chat": {"id": 101, "type": "private"}, "text": ""},
        {"from": {"id": 101}, "chat": {"id": 101, "type": "private"}, "text": "x" * 5000},
        {"from": {"id": 101}, "chat": {"id": 101, "type": "private"}},
    ],
    ids=["uninvited", "group", "mismatch", "missing-id", "bool-id", "empty", "oversize", "no-text"],
)
async def test_beta_process_message_rejects_with_zero_side_effects(msg: dict[str, Any]) -> None:
    h, butler = _handler()
    with pytest.raises(ValueError):
        await h.process_message(msg)
    butler.handle_message.assert_not_awaited()
    await h.close()


@pytest.mark.asyncio
async def test_beta_no_telegram_response_to_unauthorized() -> None:
    h, _ = _handler()
    for upd in (_update(999, "hello"), _update(101, "hello", chat_type="group")):
        result = await h.handle_update(upd)
        assert result["status"] == "rejected"
    h._adapter.send_message.assert_not_awaited()  # type: ignore[attr-defined]
    await h.close()


# --- admitted surface: /name /status chat /research ---


@pytest.mark.asyncio
async def test_beta_admitted_chat_and_status() -> None:
    h, butler = _handler()
    r = await h.handle_update(_update(101, "hello"))
    assert r["status"] == "ok"
    butler.handle_message.assert_awaited_once()
    sender = butler.handle_message.await_args.args[1]
    assert sender == "telegram:101"

    h2_butler = AsyncMock()
    h2_butler.handle_message.return_value = "ok"
    h2_butler.orchestrator.health_check = AsyncMock(return_value={})
    h2, _ = _handler(butler=h2_butler)
    r = await h2.handle_update(_update(202, "/status"))
    assert r["status"] == "ok"
    h2._adapter.send_message.assert_awaited()  # type: ignore[attr-defined]
    await h.close()
    await h2.close()


@pytest.mark.asyncio
async def test_beta_admitted_name_and_research(tmp_path: Any) -> None:
    from agency.telegram.profile_store import ProfileStore

    store = ProfileStore(str(tmp_path / "p.db"))
    h, _ = _handler()
    h._profiles = store
    await h.handle_update(_update(101, "/name Atlas"))
    assert store.get_name(101) == "Atlas"
    # uninvited cannot write names
    await h.handle_update(_update(999, "/name Spoof"))
    assert store.get_name(999) is None

    orchestrator = AsyncMock()
    orchestrator.list_agents.return_value = [SimpleNamespace(id="r", domain="research")]
    orchestrator.submit_task.return_value = SimpleNamespace(task_id="t")
    orchestrator.execute_task.return_value = SimpleNamespace(output="cited")
    hb, _ = _handler(butler=SimpleNamespace(orchestrator=orchestrator))
    hb._profiles = ProfileStore(":memory:")
    r = await hb.handle_update(_update(101, "/research planets"))
    assert r["status"] == "ok"
    orchestrator.execute_task.assert_awaited_once()
    await h.close()
    await hb.close()
    store.close()


# --- P-T3: creation denied, help hides it, non-beta preserved ---


@pytest.mark.asyncio
@pytest.mark.parametrize("cmd", ["/propose_agent X d c1", "/propose-agent X d c1"])
async def test_beta_denies_propose_variants_before_governance(cmd: str) -> None:
    h, butler = _handler()
    propose_spy = AsyncMock(return_value="SHOULD-NOT-RUN")
    h._handle_propose_agent = propose_spy  # type: ignore[method-assign]
    result = await h.handle_update(_update(101, cmd))
    assert result["status"] == "rejected"
    propose_spy.assert_not_awaited()
    sent = h._adapter.send_message.await_args.args[1]  # type: ignore[attr-defined]
    assert "disabled" in sent.lower()
    butler.handle_message.assert_not_awaited()
    await h.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    ["Create an agent called StockBot for finance", "create a new agent", "make a bot called X"],
)
async def test_beta_denies_plain_english_creation(text: str) -> None:
    h, butler = _handler()
    plain_spy = AsyncMock(return_value="SHOULD-NOT-RUN")
    h._handle_plain_english_agent_creation = plain_spy  # type: ignore[method-assign]
    result = await h.handle_update(_update(101, text))
    assert result["status"] == "rejected"
    plain_spy.assert_not_awaited()
    sent = h._adapter.send_message.await_args.args[1]  # type: ignore[attr-defined]
    assert "disabled" in sent.lower()
    butler.handle_message.assert_not_awaited()
    await h.close()


@pytest.mark.asyncio
async def test_beta_ordinary_chat_not_blocked_as_creation() -> None:
    h, butler = _handler()
    result = await h.handle_update(_update(101, "hello there"))
    assert result["status"] == "ok"
    butler.handle_message.assert_awaited_once()
    await h.close()


@pytest.mark.asyncio
async def test_beta_proposals_list_disabled() -> None:
    h, _ = _handler()
    lst_spy = AsyncMock(return_value="SHOULD-NOT-RUN")
    h._list_proposals = lst_spy  # type: ignore[method-assign]
    result = await h.handle_update(_update(101, "/proposals"))
    assert result["status"] == "rejected"
    lst_spy.assert_not_awaited()
    await h.close()


@pytest.mark.asyncio
async def test_beta_help_hides_creation_and_nonbeta_keeps_it() -> None:
    hb, _ = _handler()
    await hb.handle_update(_update(101, "/help"))
    beta_help = hb._adapter.send_message.await_args.args[1]  # type: ignore[attr-defined]
    assert "propose_agent" not in beta_help and "propose-agent" not in beta_help
    await hb.close()

    hn, _ = _handler(TelegramConfig(bot_token="test"), butler=None)
    hn._adapter.send_message = AsyncMock()  # type: ignore[method-assign]
    await hn.handle_update({"message": {"chat": {"id": 101}, "from": {"id": 101}, "text": "/help"}})
    legacy_help = hn._adapter.send_message.await_args.args[1]
    assert "propose_agent" in legacy_help
    await hn.close()


@pytest.mark.asyncio
async def test_nonbeta_legacy_paths_unchanged() -> None:
    h, butler = _handler(TelegramConfig(bot_token="test"), butler="auto")
    # group + unlisted user still flow to Butler in non-beta
    r = await h.handle_update(_update(999, "hello", chat_type="group", chat_id=555))
    assert r["status"] == "ok"
    assert butler.handle_message.await_args.args[1] == "telegram:chat:555:user:999"
    # creation routing preserved in non-beta
    r = await h.handle_update(_update(999, "/propose_agent X d c1"))
    assert r["command"] == "/propose-agent"
    await h.close()


# --- review fix: fail closed on invalid beta mode ---


@pytest.mark.parametrize("raw", ["treu", "2", "enabled", "truthy", "yes please"])
def test_beta_rejects_invalid_env_beta_mode(raw: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENCY_BETA_MODE", raw)
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "101,202")
    with pytest.raises(ValueError):
        TelegramConfig(bot_token="t")


@pytest.mark.parametrize("bad", [2, 1, 0, 1.5, ["true"]])
def test_beta_rejects_non_bool_non_str_explicit(bad: Any) -> None:
    with pytest.raises(ValueError):
        TelegramConfig(bot_token="t", beta_mode=bad, allowed_user_ids=[101])  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", ["treu", "2", "enabled"])
def test_beta_rejects_unknown_explicit_string(bad: str) -> None:
    with pytest.raises(ValueError):
        TelegramConfig(bot_token="t", beta_mode=bad, allowed_user_ids=[101])


# --- review fix: process_message must deny creation shortcuts before side effects ---


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cmd",
    ["/propose_agent X d c1", "/propose-agent X d c1", "/proposals"],
    ids=["underscore", "hyphen", "proposals"],
)
async def test_beta_process_message_denies_creation_shortcuts(cmd: str) -> None:
    h, butler = _handler()
    profile_spy = Mock(return_value=None)
    orig_get = h._profiles.get_name
    h._profiles.get_name = profile_spy  # type: ignore[method-assign]
    try:
        result = await h.process_message(
            {"from": {"id": 101}, "chat": {"id": 101, "type": "private"}, "text": cmd}
        )
    finally:
        h._profiles.get_name = orig_get  # type: ignore[method-assign]
    assert result == BETA_CREATION_DISABLED
    butler.handle_message.assert_not_awaited()
    profile_spy.assert_not_called()
    await h.close()


@pytest.mark.asyncio
async def test_nonbeta_process_message_keeps_legacy_flow() -> None:
    h, butler = _handler(TelegramConfig(bot_token="test"), butler="auto")
    result = await h.process_message(
        {"from": {"id": 999}, "chat": {"id": 555, "type": "group"}, "text": "/proposals"}
    )
    assert result == "ok"
    butler.handle_message.assert_awaited_once()
    await h.close()


# --- review fix: malformed webhook body fails closed with zero calls ---


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "upd",
    [None, [], "message", 123, 3.14, True],
    ids=["none", "list", "str", "int", "float", "bool"],
)
async def test_beta_handle_update_rejects_malformed_update(upd: Any) -> None:
    h, butler = _handler()
    profile_spy = Mock(return_value=None)
    orig_get = h._profiles.get_name
    h._profiles.get_name = profile_spy  # type: ignore[method-assign]
    try:
        result = await h.handle_update(upd)  # type: ignore[arg-type]
    finally:
        h._profiles.get_name = orig_get  # type: ignore[method-assign]
    assert result["status"] == "rejected"
    butler.handle_message.assert_not_awaited()
    h._adapter.send_message.assert_not_awaited()  # type: ignore[attr-defined]
    profile_spy.assert_not_called()
    await h.close()


# --- review blocker B-02: ordinary text must never reach _detect_agent_creation_intent or _handle_plain_english_agent_creation ---


@pytest.mark.asyncio
async def test_beta_never_calls_detect_intent_for_ordinary_text() -> None:
    """B-02: 'my name is Alice' must not reach _detect_agent_creation_intent."""
    h, butler = _handler()
    detect_spy = Mock(return_value=None)
    h._detect_agent_creation_intent = detect_spy  # type: ignore[method-assign]
    plain_spy = AsyncMock(return_value="SHOULD-NOT-RUN")
    h._handle_plain_english_agent_creation = plain_spy  # type: ignore[method-assign]
    result = await h.handle_update(_update(101, "my name is Alice"))
    assert result["status"] == "ok"
    detect_spy.assert_not_called()
    plain_spy.assert_not_awaited()
    butler.handle_message.assert_awaited_once()
    await h.close()


@pytest.mark.asyncio
async def test_beta_never_calls_detect_intent_for_stockbot_phrase() -> None:
    """B-02: 'Please create one called StockBot for finance' must not reach _detect_agent_creation_intent."""
    h, butler = _handler()
    detect_spy = Mock(return_value=None)
    h._detect_agent_creation_intent = detect_spy  # type: ignore[method-assign]
    plain_spy = AsyncMock(return_value="SHOULD-NOT-RUN")
    h._handle_plain_english_agent_creation = plain_spy  # type: ignore[method-assign]
    result = await h.handle_update(_update(101, "Please create one called StockBot for finance"))
    assert result == {"status": "rejected", "reason": "agent creation disabled"}
    detect_spy.assert_not_called()
    plain_spy.assert_not_awaited()
    butler.handle_message.assert_not_awaited()
    h._adapter.send_message.assert_awaited_once_with(101, BETA_CREATION_DISABLED)  # type: ignore[attr-defined]
    await h.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "status"),
    [("my name is Alice", "ok"), ("Please create one called StockBot for finance", "rejected")],
)
async def test_beta_plain_text_no_proposal_or_votes(text: str, status: str) -> None:
    """B-02: ordinary text must not submit proposals or trigger synthetic votes."""
    h, butler = _handler()
    # Spy on lattice to catch submit_proposal/resolve_agent_proposal calls
    lattice_spy = AsyncMock()
    butler.orchestrator._lattice = lattice_spy  # type: ignore[attr-defined]
    butler.orchestrator.list_agents = AsyncMock(return_value=[])
    result = await h.handle_update(_update(101, text))
    assert result["status"] == status
    # No proposal submitted, no votes resolved
    lattice_spy.submit_proposal.assert_not_awaited()
    butler.orchestrator.resolve_agent_proposal.assert_not_awaited()
    if status == "rejected":
        butler.handle_message.assert_not_awaited()
    else:
        butler.handle_message.assert_awaited_once()
    await h.close()


@pytest.mark.asyncio
async def test_beta_process_message_refuses_create_one_phrase_before_butler() -> None:
    h, butler = _handler()
    response = await h.process_message(
        {
            "from": {"id": 101},
            "chat": {"id": 101, "type": "private"},
            "text": "Please create one called StockBot for finance",
        }
    )
    assert response == BETA_CREATION_DISABLED
    butler.handle_message.assert_not_awaited()
    await h.close()


# --- review blocker B-16: unknown slash commands must refuse deterministically ---


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cmd",
    ["/some_unapproved_command", "/researchoops", "/unknown_cmd", "/research-oops"],
    ids=["unapproved", "researchoops", "unknown", "research-prefix-bypass"],
)
async def test_beta_rejects_unknown_slash_commands_in_handle_update(cmd: str) -> None:
    """B-16: unsupported slash tokens must return rejection with zero Butler/model/tool calls."""
    h, butler = _handler()
    result = await h.handle_update(_update(101, cmd))
    assert result["status"] == "rejected"
    assert result.get("reason") == "unsupported command"
    butler.handle_message.assert_not_awaited()
    h._adapter.send_message.assert_awaited_once_with(101, "Unsupported command in this beta.")  # type: ignore[attr-defined]
    await h.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("cmd", ["/some_unapproved_command", "/researchoops"])
async def test_beta_process_message_rejects_unknown_slash_commands(cmd: str) -> None:
    """B-16: process_message must refuse unsupported slash tokens before Butler."""
    h, butler = _handler()
    response = await h.process_message(
        {"from": {"id": 101}, "chat": {"id": 101, "type": "private"}, "text": cmd}
    )
    assert response == "Unsupported command in this beta."
    butler.handle_message.assert_not_awaited()
    await h.close()


# --- end review blockers ---
