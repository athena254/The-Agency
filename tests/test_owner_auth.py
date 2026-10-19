"""Owner identity and closed wire contracts; synthetic secrets only."""

from __future__ import annotations

import pytest
from pydantic import SecretStr


def test_local_bearer_is_exact_and_never_an_owner_claim():
    from agency.admission.auth import OwnerAuthenticator
    from agency.admission.models import OwnerPrincipal, PilotError

    auth = OwnerAuthenticator(
        owner_id="owner", subject_id="local-owner", bearer_secret=SecretStr("synthetic-secret")
    )
    assert auth.authenticate_local("Bearer synthetic-secret") == OwnerPrincipal(
        "owner", "local", "local-owner"
    )
    for value in (
        None,
        "",
        "synthetic-secret",
        "bearer synthetic-secret",
        "Bearer synthetic-secret ",
        "Bearer wrong",
        "Bearer \N{SNOWMAN}",
    ):
        with pytest.raises(PilotError) as exc:
            auth.authenticate_local(value)
        assert exc.value.status_code == 401
        assert "synthetic-secret" not in str(exc.value)
    empty = OwnerAuthenticator(
        owner_id="owner", subject_id="local-owner", bearer_secret=SecretStr("")
    )
    with pytest.raises(PilotError):
        empty.authenticate_local("Bearer ")


def test_telegram_requires_configured_owner_private_chat_and_exact_polling_marker():
    from agency.admission.auth import OwnerAuthenticator
    from agency.admission.models import OwnerPrincipal, PilotError

    marker = object()
    auth = OwnerAuthenticator(
        owner_id="owner",
        subject_id="local",
        bearer_secret=SecretStr("synthetic"),
        telegram_user_id=42,
        telegram_polling_marker=marker,
    )
    good = {
        "user_id": 42,
        "chat_id": 42,
        "chat_type": "private",
        "update_id": 0,
        "polling_marker": marker,
    }
    assert auth.authenticate_telegram(**good) == OwnerPrincipal("owner", "telegram", "42")
    for key, value in (
        ("user_id", 43),
        ("chat_id", 43),
        ("chat_type", "group"),
        ("update_id", -1),
        ("user_id", True),
        ("update_id", True),
        ("polling_marker", object()),
    ):
        with pytest.raises(PilotError):
            auth.authenticate_telegram(**(good | {key: value}))
    unconfigured = OwnerAuthenticator(
        owner_id="owner", subject_id="local", bearer_secret=SecretStr("synthetic")
    )
    with pytest.raises(PilotError):
        unconfigured.authenticate_telegram(**good)


def test_closed_wire_models_preserve_ids_and_bound_counts():
    from agency.admission import models as m
    from pydantic import ValidationError

    request = m.OwnerTaskRequest(request_id="A.b:-_9", thread_id="thread", message=" \ufeffhi\r\n ")
    assert request.message == " \ufeffhi\r\n "
    assert request.request_id == "A.b:-_9"
    for bad in (" x", "x ", "é", "x" * 129, "", "a/b", "a\n"):
        with pytest.raises(ValidationError):
            m.OwnerTaskRequest(request_id=bad, thread_id="t", message="hi")
    for changes in (
        {"owner_id": "evil"},
        {"message": ""},
        {"message": "x" * 8001},
        {"request_id": 1},
        {"thread_id": "x" * 129},
    ):
        with pytest.raises(ValidationError):
            m.OwnerTaskRequest.model_validate(request.model_dump() | changes)
    limits = {
        "requests_per_hour": 5,
        "requests_per_day": 20,
        "model_calls_per_hour": 40,
        "model_calls_per_day": 160,
        "model_calls_per_request": 8,
        "tool_calls_per_request": 4,
    }
    m.PilotLimits(**limits)
    for key, ceiling in limits.items():
        for bad in (True, 0, -1, ceiling + 1):
            with pytest.raises(ValidationError):
                m.PilotLimits(**(limits | {key: bad}))
    for model, body in (
        (m.PilotUsage, {"model_attempts": 0, "tool_attempts": 0}),
        (
            m.PilotCapabilities,
            {
                "enabled": True,
                "ready": False,
                "mode": "single_owner_pilot",
                "tools": ["calculator"],
                "disabled": ["shell"],
                "limits": limits,
                "provider_ready": False,
                "uncertainty_blocked": False,
            },
        ),
        (
            m.ThreadView,
            {
                "id": "t",
                "title": "Title",
                "parent_thread_id": None,
                "state": "active",
                "created_at": "now",
                "updated_at": "now",
            },
        ),
        (
            m.MessageView,
            {"id": "m", "thread_id": "t", "role": "user", "content": "hello", "created_at": "now"},
        ),
        (
            m.RequestReceipt,
            {"request_id": "r", "state": "COMPLETED", "replayed": False, "resource_id": "t"},
        ),
        (
            m.OwnerTaskView,
            {
                "request_id": "r",
                "task_id": "task",
                "thread_id": "t",
                "state": "ADMITTED",
                "replayed": False,
                "cancel_requested": False,
                "response": None,
                "assessment": None,
                "error_code": None,
                "uncertain": False,
                "usage": {"model_attempts": 0, "tool_attempts": 0},
                "created_at": "now",
                "updated_at": "now",
            },
        ),
        (
            m.OwnerError,
            {
                "error": "Unavailable",
                "code": "disabled",
                "request_id": None,
                "task_id": None,
                "uncertain": False,
                "retryable": False,
            },
        ),
    ):
        assert model.model_validate(body).model_dump() == body
        with pytest.raises(ValidationError):
            model.model_validate(body | {"authority": True})
    with pytest.raises(ValidationError):
        m.PilotUsage(model_attempts=True, tool_attempts=0)
