"""Speech-to-text for parent voice notes (brief §5.2): ElevenLabs Scribe.

POST /v1/speech-to-text, multipart `file` + `model_id` (scribe_v3, checked 23 Nov 2026;
scribe_v1 is deprecated); the transcript is in `text`. The language is auto-detected, so
Hinglish works. The transcript is only text: a spoken reading still gets the same read-back
("I read 142/88 — correct?") before anything is saved.

MockSTT knows only the simulator's sample voice notes (their transcript travels with them);
a real recording in mock mode is "not understood", honestly.
"""

import logging
from typing import Protocol

import httpx

from app.conversation.messages import AudioInput

log = logging.getLogger(__name__)

ELEVENLABS_BASE = "https://api.elevenlabs.io"
MAX_AUDIO_BYTES = 5 * 1024 * 1024  # ~5 minutes of compressed speech; voice notes are short


class STT(Protocol):
    name: str

    def transcribe(self, audio: AudioInput) -> str | None: ...


class MockSTT:
    name = "mock"

    def transcribe(self, audio: AudioInput) -> str | None:
        return audio.transcript_hint


class ElevenLabsSTT:
    name = "elevenlabs"

    def __init__(
        self,
        api_key: str,
        *,
        model: str,
        client: httpx.Client | None = None,
        base_url: str = ELEVENLABS_BASE,
    ) -> None:
        self._key = api_key
        self.model = model
        self._client = client or httpx.Client(timeout=60)
        self._base = base_url.rstrip("/")

    def __repr__(self) -> str:
        return f"ElevenLabsSTT(model={self.model!r})"

    def transcribe(self, audio: AudioInput) -> str | None:
        if audio.transcript_hint is not None and not audio.data:
            return audio.transcript_hint  # a simulator sample, no audio to send
        if not audio.data or len(audio.data) > MAX_AUDIO_BYTES:
            return None
        try:
            resp = self._client.post(
                f"{self._base}/v1/speech-to-text",
                headers={"xi-api-key": self._key},
                data={"model_id": self.model},
                files={
                    "file": (
                        audio.filename or "voice-note.webm",
                        audio.data,
                        audio.mime or "application/octet-stream",
                    )
                },
            )
        except httpx.HTTPError as exc:
            log.warning("STT request failed: %s", type(exc).__name__)
            return None
        if resp.status_code != 200:
            log.warning("STT HTTP %s", resp.status_code)
            return None
        try:
            text = str(resp.json().get("text") or "").strip()
        except ValueError:
            return None
        return text or None


def get_stt(settings) -> STT | None:
    if settings.voice == "none":
        return None
    if settings.voice == "elevenlabs" and settings.elevenlabs_api_key:
        return ElevenLabsSTT(settings.elevenlabs_api_key, model=settings.elevenlabs_stt_model)
    return MockSTT()
