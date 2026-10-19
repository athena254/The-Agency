"""Offline immutable owner dispatch contract, independent of admission types."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import FrozenInstanceError, asdict, dataclass, field, fields, replace
from functools import partial
from types import SimpleNamespace
from typing import Any, Self

import httpx
import pytest
from pydantic import SecretStr

from agency.llm import adapter as adapter_module
from agency.llm.adapter import LLMAdapter
from agency.llm.config import LLMConfig, ProviderKind


def owner_adapter() -> LLMAdapter:
    return LLMAdapter(
        config=LLMConfig(
            provider=ProviderKind.OPENAI,
            model="synthetic/model:exact",
            api_key=SecretStr("SYNTHETIC_OWNER_KEY"),
            base_url="https://provider.invalid/v1",
            max_tokens=123,
            temperature=0.25,
            timeout=12.0,
        )
    )


def test_freeze_is_independent_immutable_and_secret_safe() -> None:
    adapter = owner_adapter()
    snapshot = adapter.freeze_dispatch()
    assert type(snapshot) is adapter_module.LLMDispatchSnapshot
    assert [item.name for item in fields(snapshot)] == [
        "provider",
        "model",
        "base_url",
        "api_key",
        "max_tokens",
        "temperature",
        "timeout",
    ]
    assert snapshot.provider == "openai"
    assert snapshot.model == "synthetic/model:exact"
    assert snapshot.base_url == "https://provider.invalid/v1"
    assert snapshot.api_key == "SYNTHETIC_OWNER_KEY"
    assert snapshot.max_tokens == 123
    assert snapshot.temperature == 0.25
    assert snapshot.timeout == 12.0
    assert "SYNTHETIC_OWNER_KEY" not in repr(snapshot)
    attribute = "model"
    with pytest.raises(FrozenInstanceError):
        setattr(snapshot, attribute, "changed")
    adapter._model = "mutated"
    adapter._api_key = "mutated"
    assert snapshot.model == "synthetic/model:exact"
    assert snapshot.api_key == "SYNTHETIC_OWNER_KEY"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("provider", "anthropic"),
        ("provider", "openrouter"),
        ("provider", "nous"),
        ("provider", "ollama"),
        ("provider", "pollinations"),
        ("provider", "local"),
        ("provider", "OPENAI"),
        ("provider", ProviderKind.OPENAI),
        ("provider", None),
        ("model", ""),
        ("model", "   "),
        ("model", " model "),
        ("model", "model\n"),
        ("model", 42),
        ("api_key", ""),
        ("api_key", " "),
        ("api_key", " key"),
        ("api_key", "key\r\nheader"),
        ("api_key", None),
        ("base_url", None),
        ("max_tokens", True),
        ("max_tokens", 0),
        ("max_tokens", -1),
        ("max_tokens", 1.0),
        ("max_tokens", "12"),
        ("temperature", True),
        ("temperature", 1),
        ("temperature", -0.1),
        ("temperature", 2.1),
        ("temperature", float("nan")),
        ("temperature", float("inf")),
        ("temperature", "0.7"),
        ("timeout", True),
        ("timeout", 1),
        ("timeout", 0.0),
        ("timeout", -1.0),
        ("timeout", 45.01),
        ("timeout", float("nan")),
        ("timeout", float("inf")),
    ],
)
def test_snapshot_construction_rejects_invalid_values(name: str, value: object) -> None:
    values: dict[str, Any] = asdict(owner_adapter().freeze_dispatch())
    values[name] = value
    with pytest.raises((TypeError, ValueError)) as caught:
        adapter_module.LLMDispatchSnapshot(**values)
    assert "SYNTHETIC_OWNER_KEY" not in str(caught.value)
    assert "header" not in str(caught.value)


@pytest.mark.parametrize(
    "url",
    [
        "",
        "provider.invalid/v1",
        "http://provider.invalid/v1",
        "ftp://provider.invalid/v1",
        "https://user:password@provider.invalid/v1",
        "https://@provider.invalid/v1",
        "https://provider.invalid/v1?secret=x",
        "https://provider.invalid/v1?",
        "https://provider.invalid/v1#fragment",
        "https://provider.invalid/v1#",
        " https://provider.invalid/v1",
        "https://provider.invalid/v1 ",
        "https://provider.invalid/\npath",
        "https://provider.invalid\\other/v1",
        "https:///v1",
        "https://provider.invalid:bad/v1",
        "https://provider.invalid:99999/v1",
        "http://localhost.evil.invalid/v1",
        "http://127.0.0.1.evil.invalid/v1",
        "http://0.0.0.0/v1",
        "http://[::]/v1",
        "http://2130706433/v1",
        "http://127.1/v1",
        "http://127.0.0.1@provider.invalid/v1",
    ],
)
def test_snapshot_rejects_unsafe_endpoints(url: str) -> None:
    with pytest.raises(ValueError):
        replace(owner_adapter().freeze_dispatch(), base_url=url)


@pytest.mark.parametrize(
    "url",
    [
        "https://provider.invalid/v1",
        "https://provider.invalid:443/custom/v1/",
        "http://localhost:8080/v1",
        "http://127.0.0.1:8080/v1",
        "http://[::1]:8080/v1",
        "http://127.0.0.2/v1",
    ],
)
def test_snapshot_accepts_explicit_safe_endpoints_without_rewriting(url: str) -> None:
    snapshot = replace(owner_adapter().freeze_dispatch(), base_url=url)
    assert snapshot.base_url == url
    assert replace(snapshot, temperature=0.0, timeout=45.0).timeout == 45.0
    assert replace(snapshot, temperature=2.0, max_tokens=1).max_tokens == 1


@pytest.mark.parametrize("missing", ["provider", "model", "base_url", "api_key"])
def test_freeze_rejects_environment_filled_direct_arguments(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    values: dict[str, Any] = {
        "provider": "openai",
        "model": "synthetic-model",
        "base_url": "https://provider.invalid/v1",
        "api_key": "SYNTHETIC_OWNER_KEY",
        "timeout": 12.0,
    }
    monkeypatch.setenv("AGENCY_LLM_" + missing.upper(), str(values.pop(missing)))
    adapter = LLMAdapter(**values)
    with pytest.raises(ValueError):
        adapter.freeze_dispatch()


@pytest.mark.parametrize("missing", ["model", "base_url", "api_key"])
def test_freeze_rejects_empty_explicit_arguments_even_with_environment(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    values: dict[str, Any] = {
        "provider": "openai",
        "model": "synthetic-model",
        "base_url": "https://provider.invalid/v1",
        "api_key": "SYNTHETIC_OWNER_KEY",
        "timeout": 12.0,
    }
    monkeypatch.setenv("AGENCY_LLM_" + missing.upper(), str(values[missing]))
    values[missing] = ""
    with pytest.raises(ValueError):
        LLMAdapter(**values).freeze_dispatch()


def test_freeze_rejects_untyped_config_without_changing_legacy_constructor() -> None:
    config = SimpleNamespace(
        provider="openai",
        model="synthetic",
        base_url="https://provider.invalid/v1",
        api_key=SecretStr("SYNTHETIC_OWNER_KEY"),
        max_tokens=1,
        temperature=0.0,
        timeout=12.0,
    )
    adapter = LLMAdapter(config=config)
    assert adapter.provider == "openai"
    with pytest.raises(ValueError):
        adapter.freeze_dispatch()


@pytest.mark.parametrize(
    ("field", "value"),
    [("provider", 42), ("model", 42), ("base_url", False), ("max_tokens", True)],
)
def test_freeze_rejects_invalid_source_config_without_silent_normalization(
    field: str, value: object
) -> None:
    config = LLMConfig.model_construct(
        provider=ProviderKind.OPENAI,
        model="synthetic",
        base_url="https://provider.invalid/v1",
        api_key=SecretStr("SYNTHETIC_OWNER_KEY"),
        max_tokens=1,
        temperature=0.0,
        timeout=12.0,
    )
    object.__setattr__(config, field, value)
    with pytest.raises((TypeError, ValueError)):
        LLMAdapter(config=config).freeze_dispatch()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("_model", ""),
        ("_api_key", ""),
        ("_base_url", "http://provider.invalid/v1"),
        ("_timeout", 60.0),
        ("_max_tokens", True),
        ("_temperature", float("nan")),
    ],
)
def test_freeze_validates_current_effective_values_before_dispatch(
    name: str, value: object
) -> None:
    adapter = owner_adapter()
    setattr(adapter, name, value)
    with pytest.raises((TypeError, ValueError)):
        adapter.freeze_dispatch()


def test_direct_explicit_openai_and_echo_can_freeze() -> None:
    adapter = LLMAdapter(
        provider="openai",
        model="synthetic",
        base_url="https://provider.invalid/v1",
        api_key="SYNTHETIC_OWNER_KEY",
        timeout=12.0,
    )
    assert adapter.freeze_dispatch().provider == "openai"
    echo = LLMAdapter(config=LLMConfig(provider=ProviderKind.ECHO, model="echo", timeout=12.0))
    snapshot = echo.freeze_dispatch()
    assert snapshot.provider == "echo"
    assert snapshot.base_url == snapshot.api_key == ""
    assert LLMAdapter(provider="echo", model="echo", timeout=12.0).freeze_dispatch() == snapshot


@dataclass
class ProviderLeaf:
    response: httpx.Response = field(
        default_factory=lambda: httpx.Response(
            200, json={"choices": [{"message": {"content": '{"final":"synthetic answer"}'}}]}
        )
    )
    error: Exception | None = None
    requests: list[httpx.Request] = field(default_factory=list)
    options: list[dict[str, Any]] = field(default_factory=list)
    on_enter: Callable[[], Awaitable[None]] | None = None


@pytest.fixture
def http_leaf(monkeypatch: pytest.MonkeyPatch) -> ProviderLeaf:
    """Mock only transport and context entry; construct/send a real HTTPX request."""
    leaf = ProviderLeaf()

    def handle(request: httpx.Request) -> httpx.Response:
        leaf.requests.append(request)
        if leaf.error is not None:
            raise leaf.error
        return leaf.response

    class Client(httpx.AsyncClient):
        async def __aenter__(self) -> Self:
            if leaf.on_enter is not None:
                await leaf.on_enter()
            await super().__aenter__()
            return self

    def factory(**kwargs: Any) -> httpx.AsyncClient:
        leaf.options.append(dict(kwargs))
        return Client(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return leaf


@pytest.mark.parametrize("saved_method", [False, True])
async def test_bound_dispatch_uses_frozen_values_at_actual_http_leaf_across_entry_await(
    http_leaf: ProviderLeaf, monkeypatch: pytest.MonkeyPatch, saved_method: bool
) -> None:
    adapter = owner_adapter()
    snapshot = adapter.freeze_dispatch()
    generate = partial(adapter.generate_bound, snapshot=snapshot)
    entered, release = asyncio.Event(), asyncio.Event()

    async def pause() -> None:
        entered.set()
        await release.wait()

    http_leaf.on_enter = pause

    def mutate() -> None:
        for name, value in {
            "_provider": "anthropic",
            "_model": "drifted",
            "_api_key": "DRIFTED_TEST_KEY",
            "_base_url": "https://drifted.invalid",
            "_max_tokens": 9999,
            "_temperature": 1.9,
            "_timeout": 60.0,
        }.items():
            setattr(adapter, name, value)
        for name in (
            "AGENCY_LLM_MODEL",
            "AGENCY_LLM_BASE_URL",
            "AGENCY_LLM_API_KEY",
            "OPENAI_API_KEY",
            "NOUS_API_KEY",
            "HTTPS_PROXY",
        ):
            monkeypatch.setenv(name, "https://drifted.invalid")

    mutate()  # Changes after freezing but before entering the coroutine.
    task = asyncio.create_task(
        generate("SYNTHETIC_PRIVATE_PROMPT")
        if saved_method
        else adapter.generate_bound("SYNTHETIC_PRIVATE_PROMPT", snapshot)
    )
    try:
        await asyncio.wait_for(entered.wait(), timeout=2.0)
        mutate()  # Actual AsyncClient.__aenter__ is suspended here.
        assert http_leaf.requests == []
        release.set()
        assert await task == '{"final":"synthetic answer"}'
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert http_leaf.options == [{"timeout": 12.0, "trust_env": False, "follow_redirects": False}]
    assert len(http_leaf.requests) == 1
    request = http_leaf.requests[0]
    assert request.method == "POST"
    assert str(request.url) == "https://provider.invalid/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer SYNTHETIC_OWNER_KEY"
    assert json.loads(request.content) == {
        "model": "synthetic/model:exact",
        "messages": [{"role": "user", "content": "SYNTHETIC_PRIVATE_PROMPT"}],
        "max_tokens": 123,
        "temperature": 0.25,
    }
    assert request.extensions["timeout"] == {
        "connect": 12.0,
        "read": 12.0,
        "write": 12.0,
        "pool": 12.0,
    }


async def test_bound_echo_keeps_existing_scripted_leaf_patch_convention(
    http_leaf: ProviderLeaf, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = LLMAdapter(provider="echo", model="echo", timeout=12.0)
    snapshot = adapter.freeze_dispatch()
    prompts: list[str] = []

    def scripted(prompt: str) -> str:
        prompts.append(prompt)
        return '{"action":{"name":"calculator","args":{"expression":"1+1"}}}'

    monkeypatch.setattr(adapter, "_echo_generate", scripted)
    adapter._provider = "openai"
    assert await adapter.generate_bound("synthetic", snapshot) == (
        '{"action":{"name":"calculator","args":{"expression":"1+1"}}}'
    )
    assert prompts == ["synthetic"]
    assert http_leaf.requests == []
    assert http_leaf.options == []


@pytest.mark.parametrize(
    "prompt",
    [None, b"prompt", 42, "x" * 65537, "é" * 32769],
    ids=["null", "bytes", "int", "ascii-over", "utf8-over"],
)
async def test_bound_rejects_invalid_or_oversize_prompt_before_client(
    http_leaf: ProviderLeaf, prompt: Any
) -> None:
    adapter = owner_adapter()
    with pytest.raises((TypeError, ValueError)):
        await adapter.generate_bound(prompt, adapter.freeze_dispatch())
    assert http_leaf.requests == []
    assert http_leaf.options == []


@pytest.mark.parametrize(
    "text",
    ["x" * 65536, "é" * 32768, "", '{"final":"exact"}'],
    ids=["ascii-limit", "utf8-limit", "empty", "protocol"],
)
async def test_bound_accepts_exact_utf8_output_boundary_and_preserves_protocol(
    http_leaf: ProviderLeaf, text: str
) -> None:
    http_leaf.response = httpx.Response(200, json={"choices": [{"message": {"content": text}}]})
    adapter = owner_adapter()
    generate = partial(adapter.generate_bound, snapshot=adapter.freeze_dispatch())
    assert await generate("é" * 32768) == text
    assert len(http_leaf.requests) == 1


@pytest.mark.parametrize("provider", ["openai", "echo"])
@pytest.mark.parametrize(
    "text",
    ["x" * 65537, "é" * 32769, None, ["text"]],
    ids=["ascii-over", "utf8-over", "null", "list"],
)
async def test_bound_rejects_invalid_or_oversize_output_without_fallback(
    http_leaf: ProviderLeaf, monkeypatch: pytest.MonkeyPatch, provider: str, text: Any
) -> None:
    adapter = (
        owner_adapter()
        if provider == "openai"
        else LLMAdapter(provider="echo", model="echo", timeout=12.0)
    )
    calls: list[str] = []

    def scripted(prompt: str) -> Any:
        calls.append(prompt)
        return text

    monkeypatch.setattr(adapter, "_echo_generate", scripted)
    http_leaf.response = httpx.Response(200, json={"choices": [{"message": {"content": text}}]})
    generate = partial(adapter.generate_bound, snapshot=adapter.freeze_dispatch())
    with pytest.raises((TypeError, ValueError)):
        await generate("synthetic")
    assert len(http_leaf.requests) == int(provider == "openai")
    assert len(calls) == int(provider == "echo")


@pytest.mark.parametrize("kind", ["duck", "subclass", "tampered"])
async def test_bound_requires_valid_exact_snapshot_before_client(
    http_leaf: ProviderLeaf, kind: str
) -> None:
    adapter = owner_adapter()
    original = adapter.freeze_dispatch()
    snapshot: Any
    if kind == "duck":
        snapshot = SimpleNamespace(**asdict(original))
    elif kind == "subclass":

        class Derived(adapter_module.LLMDispatchSnapshot):
            pass

        snapshot = Derived(**asdict(original))
    else:
        snapshot = original
        object.__setattr__(snapshot, "timeout", 60.0)
    with pytest.raises((TypeError, ValueError)):
        await adapter.generate_bound("synthetic", snapshot)
    assert http_leaf.requests == []
    assert http_leaf.options == []


@pytest.mark.parametrize("status", [301, 302, 307, 308, 400, 429, 500])
async def test_bound_http_failure_or_redirect_is_one_attempt_without_fallback(
    http_leaf: ProviderLeaf, status: int
) -> None:
    from structlog.testing import capture_logs

    http_leaf.response = httpx.Response(
        status, headers={"location": "https://redirect.invalid"}, text="SYNTHETIC_PRIVATE_ERROR"
    )
    adapter = owner_adapter()
    with capture_logs() as logs, pytest.raises(httpx.HTTPStatusError):
        await adapter.generate_bound("SYNTHETIC_PRIVATE_PROMPT", adapter.freeze_dispatch())
    assert len(http_leaf.requests) == len(http_leaf.options) == 1
    assert all("SYNTHETIC_PRIVATE" not in repr(row) for row in logs)
    assert all("SYNTHETIC_OWNER_KEY" not in repr(row) for row in logs)


@pytest.mark.parametrize("error", [httpx.ReadTimeout, httpx.ConnectError])
async def test_bound_transport_failure_propagates_once(
    http_leaf: ProviderLeaf, error: type[httpx.RequestError]
) -> None:
    adapter = owner_adapter()
    failure = error("SYNTHETIC_PRIVATE_ERROR")
    http_leaf.error = failure
    generate = partial(adapter.generate_bound, snapshot=adapter.freeze_dispatch())
    with pytest.raises(error) as caught:
        await generate("synthetic")
    assert caught.value is failure
    assert len(http_leaf.requests) == len(http_leaf.options) == 1


@pytest.mark.parametrize(
    "body", [b"not json", b"{}", b'{"choices":[]}', b'{"choices":[{"message":{}}]}']
)
async def test_bound_malformed_response_is_not_a_second_generation(
    http_leaf: ProviderLeaf, body: bytes
) -> None:
    http_leaf.response = httpx.Response(200, content=body)
    adapter = owner_adapter()
    with pytest.raises((ValueError, KeyError, IndexError)):
        await adapter.generate_bound("synthetic", adapter.freeze_dispatch())
    assert len(http_leaf.requests) == len(http_leaf.options) == 1


async def test_bound_cancellation_during_client_entry_never_sends(
    http_leaf: ProviderLeaf,
) -> None:
    entered = asyncio.Event()

    async def pause() -> None:
        entered.set()
        await asyncio.Event().wait()

    http_leaf.on_enter = pause
    adapter = owner_adapter()
    task = asyncio.create_task(adapter.generate_bound("synthetic", adapter.freeze_dispatch()))
    try:
        await asyncio.wait_for(entered.wait(), timeout=2.0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert http_leaf.requests == []
    assert len(http_leaf.options) == 1


@pytest.mark.parametrize(
    "provider", ["openai", "nous", "openrouter", "anthropic", "ollama", "pollinations"]
)
async def test_ordinary_provider_generate_still_uses_legacy_leaf(
    http_leaf: ProviderLeaf, provider: str
) -> None:
    adapter = LLMAdapter(
        provider=provider,
        model="legacy",
        base_url="http://localhost:9999/v1",
        api_key="SYNTHETIC_LEGACY_KEY",
        timeout=60.0,
    )
    if provider == "anthropic":
        body: object = {"content": [{"text": "legacy text"}]}
    elif provider == "ollama":
        body = {"response": "legacy text"}
    else:
        body = {"choices": [{"message": {"content": "legacy text"}}]}
    http_leaf.response = httpx.Response(200, json=body)
    assert await adapter.generate("synthetic", {"model": "override-model"}) == "legacy text"
    assert len(http_leaf.requests) == 1
    payload = json.loads(http_leaf.requests[0].content)
    assert payload["model"] == "override-model"
    assert http_leaf.options == [{"timeout": 60.0}]


async def test_ordinary_echo_generate_and_stream_keep_context_override() -> None:
    adapter = LLMAdapter(provider="unsupported", model="legacy")
    assert await adapter.generate("legacy", {"provider": "echo"}) == "Echo: legacy"
    assert [text async for text in adapter.stream("legacy", {"provider": "echo"})] == [
        "Echo: legacy"
    ]
