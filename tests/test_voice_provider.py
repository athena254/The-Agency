"""Offline regression tests for voice STT/TTS providers and selection.

All HTTP goes through ``httpx.MockTransport`` and the optional local backends
are replaced by in-process fakes, so no provider, model, or network is used.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import types
from collections.abc import Callable
from typing import Any, ClassVar

import httpx
import pytest

from agency.voice import CloudVoiceProvider, LocalVoiceProvider, VoiceError, VoiceService


def _clear_voice_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AGENCY_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)


def _install_mock_transport(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> None:
    original_client = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        return original_client(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


class _FakeWhisperModel:
    def __init__(self, text: str, seen: dict[str, Any] | None = None) -> None:
        self._text = text
        self._seen = seen

    def transcribe(self, path: str) -> dict[str, str]:
        if self._seen is not None:
            with open(path, "rb") as handle:
                self._seen["audio"] = handle.read()
            self._seen["path"] = path
        return {"text": self._text}


def _install_fake_whisper(
    monkeypatch: pytest.MonkeyPatch,
    *,
    text: str = "local text",
    seen: dict[str, Any] | None = None,
) -> None:
    fake = types.ModuleType("whisper")

    def load_model(name: str) -> _FakeWhisperModel:
        if seen is not None:
            seen["model"] = name
        return _FakeWhisperModel(text, seen)

    fake.load_model = load_model  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "whisper", fake)


def _write_spoken(path: str) -> None:
    with open(path, "wb") as handle:
        handle.write(b"SPOKEN")


class _FakeCommunicate:
    calls: ClassVar[list[tuple[str, str, str]]] = []

    def __init__(self, text: str, voice: str) -> None:
        self._text = text
        self._voice = voice

    async def save(self, path: str) -> None:
        type(self).calls.append((self._text, self._voice, path))
        await asyncio.to_thread(_write_spoken, path)


def _install_fake_edge_tts(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = types.ModuleType("edge_tts")
    fake.Communicate = _FakeCommunicate  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "edge_tts", fake)
    _FakeCommunicate.calls.clear()


# ------------------------------------------------------------------ #
# Cloud availability
# ------------------------------------------------------------------ #


def test_cloud_available_tracks_configured_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_voice_env(monkeypatch)

    assert CloudVoiceProvider().available() is False
    assert CloudVoiceProvider(api_key="secret").available() is True


def test_cloud_reads_api_key_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_voice_env(monkeypatch)

    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    assert CloudVoiceProvider().available() is True

    monkeypatch.delenv("OPENAI_API_KEY")
    monkeypatch.setenv("AGENCY_LLM_API_KEY", "agency-key")
    assert CloudVoiceProvider().available() is True


# ------------------------------------------------------------------ #
# Cloud transcription HTTP contract
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_transcribe_without_api_key_raises_before_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_voice_env(monkeypatch)
    calls: list[httpx.Request] = []
    _install_mock_transport(
        monkeypatch,
        lambda request: calls.append(request) or httpx.Response(200, json={"text": "x"}),
    )

    with pytest.raises(VoiceError, match="no API key configured"):
        await CloudVoiceProvider().transcribe(b"audio")

    assert calls == []


@pytest.mark.asyncio
async def test_transcribe_posts_multipart_and_returns_stripped_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"text": "  hello world \n"})

    _install_mock_transport(monkeypatch, handler)
    provider = CloudVoiceProvider(
        api_key="secret",
        base_url="https://voice.test/v1/",
        stt_model="whisper-large",
    )

    text = await provider.transcribe(b"RIFFdata", filename="note.wav")

    assert text == "hello world"
    [request] = requests
    assert request.method == "POST"
    assert str(request.url) == "https://voice.test/v1/audio/transcriptions"
    assert request.headers["authorization"] == "Bearer secret"
    assert request.headers["content-type"].startswith("multipart/form-data; boundary=")
    body = request.content
    assert b'name="model"' in body
    assert b"whisper-large" in body
    assert b'name="file"; filename="note.wav"' in body
    assert b"RIFFdata" in body


@pytest.mark.asyncio
async def test_transcribe_uses_default_filename_and_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []
    _install_mock_transport(
        monkeypatch,
        lambda request: requests.append(request) or httpx.Response(200, json={"text": "ok"}),
    )

    await CloudVoiceProvider(api_key="secret", base_url="https://voice.test/v1").transcribe(b"a")

    [request] = requests
    assert b'filename="audio.ogg"' in request.content
    assert b"whisper-1" in request.content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [{}, {"text": None}, {"text": 3}, {"text": ["not", "text"]}],
    ids=["missing", "null", "number", "list"],
)
async def test_transcribe_rejects_unexpected_response_payload(
    monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any]
) -> None:
    _install_mock_transport(monkeypatch, lambda _request: httpx.Response(200, json=payload))

    with pytest.raises(VoiceError, match="unexpected transcription response"):
        await CloudVoiceProvider(api_key="secret").transcribe(b"audio")


@pytest.mark.asyncio
async def test_transcribe_propagates_http_status_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_mock_transport(
        monkeypatch, lambda _request: httpx.Response(401, json={"error": "denied"})
    )

    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        await CloudVoiceProvider(api_key="secret").transcribe(b"audio")

    assert excinfo.value.response.status_code == 401


# ------------------------------------------------------------------ #
# Cloud synthesis HTTP contract
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_synthesize_without_api_key_raises_before_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_voice_env(monkeypatch)
    calls: list[httpx.Request] = []
    _install_mock_transport(
        monkeypatch,
        lambda request: calls.append(request) or httpx.Response(200, content=b"audio"),
    )

    with pytest.raises(VoiceError, match="no API key configured"):
        await CloudVoiceProvider().synthesize("hello")

    assert calls == []


@pytest.mark.asyncio
async def test_synthesize_posts_json_and_returns_audio_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=b"MP3BYTES")

    _install_mock_transport(monkeypatch, handler)
    provider = CloudVoiceProvider(
        api_key="secret",
        base_url="https://voice.test/v1/",
        tts_model="tts-1-hd",
        tts_voice="verse",
    )

    audio = await provider.synthesize("hello there")

    assert audio == b"MP3BYTES"
    [request] = requests
    assert request.method == "POST"
    assert str(request.url) == "https://voice.test/v1/audio/speech"
    assert request.headers["authorization"] == "Bearer secret"
    assert request.headers["content-type"].startswith("application/json")
    assert json.loads(request.content) == {
        "model": "tts-1-hd",
        "voice": "verse",
        "input": "hello there",
    }


@pytest.mark.asyncio
async def test_synthesize_propagates_http_status_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_mock_transport(
        monkeypatch, lambda _request: httpx.Response(500, json={"error": "boom"})
    )

    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        await CloudVoiceProvider(api_key="secret").synthesize("hello")

    assert excinfo.value.response.status_code == 500


# ------------------------------------------------------------------ #
# Local backend availability and errors
# ------------------------------------------------------------------ #


def test_local_available_is_false_when_optional_packages_are_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "edge_tts", None)
    monkeypatch.setitem(sys.modules, "whisper", None)

    assert LocalVoiceProvider().available() is False


def test_local_available_is_true_when_both_packages_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "edge_tts", types.ModuleType("edge_tts"))
    monkeypatch.setitem(sys.modules, "whisper", types.ModuleType("whisper"))

    assert LocalVoiceProvider().available() is True


@pytest.mark.asyncio
async def test_local_transcribe_without_whisper_raises_voice_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "whisper", None)

    with pytest.raises(VoiceError, match="'whisper' package not installed"):
        await LocalVoiceProvider().transcribe(b"audio")


@pytest.mark.asyncio
async def test_local_synthesize_without_edge_tts_raises_voice_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "edge_tts", None)

    with pytest.raises(VoiceError, match="'edge_tts' package not installed"):
        await LocalVoiceProvider().synthesize("hello")


# ------------------------------------------------------------------ #
# Local backend request/response contracts
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_local_transcribe_writes_temp_file_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}
    _install_fake_whisper(monkeypatch, text="  transcribed  ", seen=seen)

    text = await LocalVoiceProvider(whisper_model="tiny").transcribe(
        b"OGG DATA", filename="clip.wav"
    )

    assert text == "transcribed"
    assert seen["model"] == "tiny"
    assert seen["audio"] == b"OGG DATA"
    assert seen["path"].endswith(".wav")
    assert os.path.exists(seen["path"]) is False


@pytest.mark.asyncio
async def test_local_synthesize_writes_temp_file_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_edge_tts(monkeypatch)

    audio = await LocalVoiceProvider(tts_voice="en-GB-X").synthesize("hello")

    assert audio == b"SPOKEN"
    [(text, voice, path)] = _FakeCommunicate.calls
    assert (text, voice) == ("hello", "en-GB-X")
    assert path.endswith(".mp3")
    assert os.path.exists(path) is False


# ------------------------------------------------------------------ #
# Backend selection
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_voice_service_auto_without_backends_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_voice_env(monkeypatch)
    monkeypatch.setattr(LocalVoiceProvider, "available", lambda _self: False)
    service = VoiceService()

    with pytest.raises(VoiceError, match="no voice backend available"):
        await service.transcribe(b"audio")


@pytest.mark.asyncio
async def test_voice_service_explicit_cloud_unavailable_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_voice_env(monkeypatch)
    service = VoiceService(prefer="cloud")

    with pytest.raises(VoiceError, match="cloud voice unavailable"):
        await service.synthesize("hello")


@pytest.mark.asyncio
async def test_voice_service_explicit_local_unavailable_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_voice_env(monkeypatch)
    monkeypatch.setattr(LocalVoiceProvider, "available", lambda _self: False)
    service = VoiceService(prefer="local")

    with pytest.raises(VoiceError, match="local voice unavailable"):
        await service.transcribe(b"audio")


@pytest.mark.asyncio
async def test_voice_service_auto_uses_cloud_when_key_is_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_voice_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    requests: list[httpx.Request] = []
    _install_mock_transport(
        monkeypatch,
        lambda request: (
            requests.append(request) or httpx.Response(200, json={"text": "cloud text"})
        ),
    )

    result = await VoiceService().transcribe(b"audio")

    assert result == "cloud text"
    assert len(requests) == 1
    assert str(requests[0].url) == "https://api.openai.com/v1/audio/transcriptions"


@pytest.mark.asyncio
async def test_voice_service_auto_falls_back_to_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_voice_env(monkeypatch)
    _install_fake_whisper(monkeypatch, text="local text")
    _install_fake_edge_tts(monkeypatch)

    result = await VoiceService().transcribe(b"audio")

    assert result == "local text"


@pytest.mark.asyncio
async def test_voice_service_prefer_local_overrides_available_cloud(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_voice_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    _install_fake_whisper(monkeypatch, text="local wins")
    _install_fake_edge_tts(monkeypatch)

    result = await VoiceService().transcribe(b"audio", prefer="local")

    assert result == "local wins"


@pytest.mark.asyncio
async def test_voice_service_synthesize_dispatches_to_cloud(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_voice_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    _install_mock_transport(
        monkeypatch, lambda _request: httpx.Response(200, content=b"OUTPUT AUDIO")
    )

    assert await VoiceService().synthesize("hello") == b"OUTPUT AUDIO"
