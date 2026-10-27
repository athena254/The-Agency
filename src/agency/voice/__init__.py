"""Voice (STT/TTS) support for The Agency."""

from __future__ import annotations

import asyncio
import os
import tempfile
from typing import Any

import httpx


class VoiceError(RuntimeError):
    """Raised when a voice backend is unavailable or fails."""


class CloudVoiceProvider:
    """OpenAI-compatible STT/TTS over HTTP."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        stt_model: str = "whisper-1",
        tts_model: str = "tts-1",
        tts_voice: str = "alloy",
    ) -> None:
        self._api_key = (
            api_key or os.environ.get("OPENAI_API_KEY") or os.environ.get("AGENCY_LLM_API_KEY", "")
        )
        self._base_url = (
            base_url or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1"
        ).rstrip("/")
        self._stt_model = stt_model
        self._tts_model = tts_model
        self._tts_voice = tts_voice

    def available(self) -> bool:
        return bool(self._api_key)

    async def transcribe(self, audio: bytes, *, filename: str = "audio.ogg") -> str:
        if not self._api_key:
            raise VoiceError("cloud voice: no API key configured")
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self._base_url}/audio/transcriptions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                files={"file": (filename, audio)},
                data={"model": self._stt_model},
            )
            resp.raise_for_status()
            payload = resp.json()
        text = payload.get("text")
        if not isinstance(text, str):
            raise VoiceError("cloud voice: unexpected transcription response")
        return text.strip()

    async def synthesize(self, text: str) -> bytes:
        if not self._api_key:
            raise VoiceError("cloud voice: no API key configured")
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self._base_url}/audio/speech",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={"model": self._tts_model, "voice": self._tts_voice, "input": text},
            )
            resp.raise_for_status()
            return resp.content


class LocalVoiceProvider:
    """Local Whisper + edge-tts (optional dependencies)."""

    def __init__(self, whisper_model: str = "base", tts_voice: str = "en-US-AriaNeural") -> None:
        self._whisper_model = whisper_model
        self._tts_voice = tts_voice

    def available(self) -> bool:
        try:
            import edge_tts  # type: ignore[import-not-found]  # noqa: F401
            import whisper  # type: ignore[import-not-found]  # noqa: F401
        except Exception:  # noqa: BLE001
            return False
        return True

    async def transcribe(self, audio: bytes, *, filename: str = "audio.ogg") -> str:
        try:
            import whisper
        except Exception as exc:
            raise VoiceError("local voice: 'whisper' package not installed") from exc
        suffix = os.path.splitext(filename)[1] or ".ogg"
        fd, path = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        try:

            def _write() -> None:
                with open(path, "wb") as fh:
                    fh.write(audio)

            await asyncio.to_thread(_write)
            model = whisper.load_model(self._whisper_model)
            result = model.transcribe(path)
            return str(result.get("text", "")).strip()
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    async def synthesize(self, text: str) -> bytes:
        try:
            import edge_tts
        except Exception as exc:
            raise VoiceError("local voice: 'edge_tts' package not installed") from exc
        fd, path = tempfile.mkstemp(suffix=".mp3")
        os.close(fd)
        try:
            communicate = edge_tts.Communicate(text, self._tts_voice)
            await communicate.save(path)

            def _read() -> bytes:
                with open(path, "rb") as fh:
                    return fh.read()

            return await asyncio.to_thread(_read)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass


class VoiceService:
    """Picks a backend based on availability and preference."""

    def __init__(self, prefer: str = "auto") -> None:
        self._prefer = prefer
        self._cloud = CloudVoiceProvider()
        self._local = LocalVoiceProvider()

    def _backend(self, prefer: str | None = None) -> Any:
        choice = prefer or self._prefer
        if choice == "cloud":
            if not self._cloud.available():
                raise VoiceError("cloud voice unavailable (no API key)")
            return self._cloud
        if choice == "local":
            if not self._local.available():
                raise VoiceError("local voice unavailable (missing whisper/edge_tts)")
            return self._local
        if self._cloud.available():
            return self._cloud
        if self._local.available():
            return self._local
        raise VoiceError("no voice backend available")

    async def transcribe(
        self, audio: bytes, *, prefer: str | None = None, filename: str = "audio.ogg"
    ) -> str:
        backend = self._backend(prefer)
        return await backend.transcribe(audio, filename=filename)  # type: ignore[no-any-return]

    async def synthesize(self, text: str, *, prefer: str | None = None) -> bytes:
        backend = self._backend(prefer)
        return await backend.synthesize(text)  # type: ignore[no-any-return]
