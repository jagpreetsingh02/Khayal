"""Text-to-speech for voice notes (brief §5.8).

- MockTTS (VOICE=mock, default): no audio file; the simulator shows a "voice note" bubble and
  speaks it with the browser's own voice. No keys, no network.
- ElevenLabsTTS (VOICE=elevenlabs + key + voice id): MP3 from the ElevenLabs API, cached on
  disk by (model, voice, language, text) so repeated phrases (reminders) cost nothing, and a
  daily character budget (ELEVENLABS_DAILY_CHAR_BUDGET) protects the free plan's credits.
  Any problem (no key, over budget, API error) -> None: the message goes out as text only.

Model choice: eleven_v4_turbo lists Hindi and Tamil and is the low-latency model in
ElevenLabs' models overview (checked 23 Nov 2026); eleven_flash_v2_5 is cheaper per character
(set ELEVENLABS_TTS_MODEL) but its Tamil support was not confirmed in the docs.
"""

import hashlib
import logging
import re
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

import httpx

from app.conversation.messages import Voice
from app.storage.base import Store
from app.storage.models import utcnow

log = logging.getLogger(__name__)

ELEVENLABS_BASE = "https://api.elevenlabs.io"
OUTPUT_FORMAT = "mp3_44100_64"  # small files for chat voice notes
CLIP_NAME = re.compile(r"^[0-9a-f]{32}\.mp3$")
TZ = ZoneInfo("Asia/Kolkata")


class TTS(Protocol):
    name: str

    def speak(self, voice: Voice) -> str | None:
        """URL path of the audio (e.g. /voice/<id>.mp3), or None for no audio file."""
        ...


class MockTTS:
    name = "mock"

    def speak(self, voice: Voice) -> str | None:
        return None  # the simulator speaks the text with the browser's voice


def budget_key(day: str) -> str:
    return f"tts_chars:{day}"


class ElevenLabsTTS:
    name = "elevenlabs"

    def __init__(
        self,
        api_key: str,
        voice_id: str,
        *,
        model: str,
        cache_dir: Path,
        store: Store,
        daily_char_budget: int,
        client: httpx.Client | None = None,
        base_url: str = ELEVENLABS_BASE,
    ) -> None:
        self._key = api_key
        self.voice_id = voice_id
        self.model = model
        self.cache_dir = cache_dir
        self.store = store
        self.budget = daily_char_budget
        self._client = client or httpx.Client(timeout=30)
        self._base = base_url.rstrip("/")

    def __repr__(self) -> str:  # never show the key
        return f"ElevenLabsTTS(model={self.model!r})"

    def clip_id(self, voice: Voice) -> str:
        raw = "|".join((self.model, self.voice_id, voice.lang, voice.text))
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def speak(self, voice: Voice) -> str | None:
        clip = self.clip_id(voice)
        path = self.cache_dir / f"{clip}.mp3"
        if path.is_file():
            return f"/voice/{clip}.mp3"  # cached: no API call, no credits
        day = utcnow().astimezone(TZ).date().isoformat()
        used = int(self.store.get_kv(budget_key(day)) or 0)
        if used + len(voice.text) > self.budget:
            log.info("TTS daily budget reached (%d/%d chars): text only", used, self.budget)
            return None
        body: dict = {"text": voice.text, "model_id": self.model}
        if "multilingual_v2" not in self.model:  # language_code not supported there
            body["language_code"] = voice.lang
        try:
            resp = self._client.post(
                f"{self._base}/v1/text-to-speech/{self.voice_id}",
                params={"output_format": OUTPUT_FORMAT},
                headers={"xi-api-key": self._key},
                json=body,
            )
        except httpx.HTTPError as exc:
            log.warning("TTS request failed: %s", type(exc).__name__)
            return None
        if resp.status_code != 200 or not resp.content:
            log.warning("TTS HTTP %s: text only", resp.status_code)
            return None
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_bytes(resp.content)
        self.store.incr_kv(budget_key(day), len(voice.text))
        return f"/voice/{clip}.mp3"


def get_tts(settings, store: Store) -> TTS | None:
    """VOICE=none -> None (no voice notes); mock -> MockTTS; elevenlabs -> real TTS when the
    key and voice id are set, else None (silently text only)."""
    if settings.voice == "mock":
        return MockTTS()
    if settings.voice == "elevenlabs":
        if settings.elevenlabs_api_key and settings.elevenlabs_voice_id:
            return ElevenLabsTTS(
                settings.elevenlabs_api_key,
                settings.elevenlabs_voice_id,
                model=settings.elevenlabs_tts_model,
                cache_dir=Path(settings.voice_cache_dir),
                store=store,
                daily_char_budget=settings.elevenlabs_daily_char_budget,
            )
        log.info("VOICE=elevenlabs without ELEVENLABS_API_KEY/VOICE_ID: text only")
    return None
