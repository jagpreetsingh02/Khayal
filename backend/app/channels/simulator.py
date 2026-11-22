"""Web simulator channel: the chat lives in memory and the /simulator page polls it.

Photos and voice recordings are not kept: the feed only notes that one was sent (and which
demo sample), plus the voice note's transcript once speech-to-text ran. Outgoing voice notes
appear as "voice note" bubbles (real audio, or the browser's voice in mock mode); red-alert
calls appear as an "Incoming call" card in the family member's pane.
"""

import base64
import binascii
import itertools
import threading
from typing import Any, Literal

from pydantic import BaseModel

from app.channels.base import Channel, ChannelError
from app.conversation.messages import AudioInput, Incoming, Outgoing
from app.reader.schema import ImageInput
from app.storage.models import utcnow

MAX_PHOTO_BYTES = 10 * 1024 * 1024
MAX_AUDIO_BYTES = 5 * 1024 * 1024

# Sample voice notes for mock mode: (name -> what the parent says). The mock STT returns
# exactly this text; with ElevenLabs, real recordings go to Scribe.
VOICE_SAMPLES = {
    "took_medicine_hi": "मैंने दवा ले ली",
    "bp_reading_en": "My BP is 142 by 88",
    "sugar_hinglish": "Sugar 110 hai",
    "bp_high_hi": "मेरा बीपी 186 बटा 121 है",
    "yes_hi": "हाँ, सही है",
    "weather_en": "What is the weather today?",
}
FEED_LIMIT = 200  # messages kept per chat


class SimulatorEvent(BaseModel):
    """What the page posts to /simulator/api/send."""

    sender_ref: str
    text: str | None = None
    sample: str | None = None  # a file in samples/ (the page's sample picker)
    image_b64: str | None = None  # an uploaded photo
    filename: str | None = None
    audio_b64: str | None = None  # a voice note recorded in the browser
    audio_mime: str | None = None
    voice_sample: str | None = None  # a VOICE_SAMPLES name (mock mode)
    client_id: str | None = None  # lets the page show its own preview of the photo


class FeedItem(BaseModel):
    seq: int
    at: str
    direction: Literal["in", "out", "note"]  # in = sent by the member, out = sent to them
    text: str | None = None
    kind: str = "message"  # message | voice_call (call card)
    photo: bool = False
    sample: str | None = None
    client_id: str | None = None
    voice: bool = False  # in: a voice note; out: a voice note bubble
    transcript: str | None = None  # in: what speech-to-text heard
    voice_text: str | None = None  # out: what the voice note says (member's language)
    voice_lang: str | None = None
    audio_url: str | None = None  # real TTS audio; None + voice_mock -> browser voice
    voice_mock: bool = False


class SimulatorChannel(Channel):
    name = "simulator"

    def __init__(self, load_sample=None) -> None:
        self._load_sample = load_sample  # name -> bytes (None if not a known sample)
        self._feeds: dict[str, list[FeedItem]] = {}
        self._seq = itertools.count(1)
        self._lock = threading.Lock()

    def _add(self, ref: str, **fields: Any) -> FeedItem:
        with self._lock:
            item = FeedItem(seq=next(self._seq), at=utcnow().isoformat(), **fields)
            feed = self._feeds.setdefault(ref, [])
            feed.append(item)
            del feed[:-FEED_LIMIT]
            return item

    def receive(self, event: SimulatorEvent) -> Incoming:
        if event.voice_sample or event.audio_b64:
            return self._receive_voice(event)
        image = None
        if event.sample:
            data = self._load_sample(event.sample) if self._load_sample else None
            if data is None:
                raise ChannelError(f"Unknown sample {event.sample!r}")
            image = ImageInput(data=data, filename=event.sample)
        elif event.image_b64:
            try:
                data = base64.b64decode(event.image_b64, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ChannelError("Photo is not valid base64") from exc
            if len(data) > MAX_PHOTO_BYTES:
                raise ChannelError("Photo is larger than 10 MB")
            image = ImageInput(data=data, filename=event.filename)
        text = (event.text or "").strip() or None
        if text is None and image is None:
            raise ChannelError("Empty message")
        self._add(
            event.sender_ref,
            direction="in",
            text=text,
            photo=image is not None,
            sample=event.sample,
            client_id=event.client_id,
        )
        return Incoming(channel="simulator", sender_ref=event.sender_ref, text=text, image=image)

    def _receive_voice(self, event: SimulatorEvent) -> Incoming:
        if event.voice_sample:
            if event.voice_sample not in VOICE_SAMPLES:
                raise ChannelError(f"Unknown voice sample {event.voice_sample!r}")
            audio = AudioInput(transcript_hint=VOICE_SAMPLES[event.voice_sample])
        else:
            try:
                data = base64.b64decode(event.audio_b64 or "", validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ChannelError("Voice note is not valid base64") from exc
            if not data or len(data) > MAX_AUDIO_BYTES:
                raise ChannelError("Voice note is empty or larger than 5 MB")
            audio = AudioInput(data=data, mime=event.audio_mime, filename="voice-note.webm")
        self._add(
            event.sender_ref,
            direction="in",
            voice=True,
            sample=event.voice_sample,
            client_id=event.client_id,
        )
        return Incoming(channel="simulator", sender_ref=event.sender_ref, audio=audio)

    def set_transcript(self, ref: str, transcript: str | None) -> None:
        """Show what speech-to-text heard under the member's latest voice note."""
        with self._lock:
            for item in reversed(self._feeds.get(ref, [])):
                if item.direction == "in" and item.voice:
                    item.transcript = transcript or "(not understood)"
                    return

    def send(self, msg: Outgoing) -> None:
        voice = msg.voice is not None and (msg.audio_url is not None or msg.voice_mock)
        self._add(
            msg.to_ref,
            direction="out",
            text=msg.text,
            kind=msg.kind,
            voice=voice,
            voice_text=msg.voice.text if voice and msg.voice else None,
            voice_lang=msg.voice.lang if voice and msg.voice else None,
            audio_url=msg.audio_url,
            voice_mock=msg.voice_mock,
        )

    def call_card(
        self, ref: str, script: str, *, lang: str, audio_url: str | None, mock: bool
    ) -> None:
        """The mock red-alert call: an "Incoming call" card in this member's pane."""
        self._add(
            ref,
            direction="out",
            kind="voice_call",
            text=script,
            voice=True,
            voice_text=script,
            voice_lang=lang,
            audio_url=audio_url,
            voice_mock=mock,
        )

    def note(self, ref: str, text: str) -> None:
        """A grey system line in a chat, e.g. "5 minutes later"."""
        self._add(ref, direction="note", text=text)

    def feed(self, ref: str) -> list[FeedItem]:
        with self._lock:
            return list(self._feeds.get(ref, []))

    def clear(self) -> None:
        with self._lock:
            self._feeds.clear()
