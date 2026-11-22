"""Channel-agnostic message types. Channel adapters (simulator, Telegram, Twilio) convert to
and from these; the state machine never sees channel details."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.reader.schema import ImageInput
from app.storage.models import Channel


class AudioInput(BaseModel):
    """A voice note from the parent. Kept in memory only; never stored."""

    data: bytes = b""
    mime: str | None = None
    filename: str | None = None
    # Simulator sample voice notes only: the known transcript (used by the mock STT).
    transcript_hint: str | None = None


class Voice(BaseModel):
    """What to say aloud with a message: short, already in the member's language."""

    text: str
    lang: str = "en"


class Incoming(BaseModel):
    channel: Channel
    sender_ref: str  # phone number / chat id; mapped to a member server-side
    text: str | None = None  # message text, photo caption, or a voice note's transcript
    image: ImageInput | None = None
    audio: AudioInput | None = None
    via_voice: bool = False  # the text came from a voice note (speech-to-text)
    received_at: datetime | None = None


class Outgoing(BaseModel):
    channel: Channel
    to_ref: str
    member_id: str | None  # None only for replies to unregistered senders
    text: str
    kind: Literal["message", "voice_call"] = "message"
    voice: Voice | None = None  # say this as a voice note (if voice is enabled)
    audio_url: str | None = None  # set at delivery when TTS produced audio
    voice_mock: bool = False  # mock voice: no audio file; the simulator uses the browser
