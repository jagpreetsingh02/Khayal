"""Channel-agnostic message types. Channel adapters (simulator, Telegram, Twilio; Phase 3)
convert to and from these; the state machine never sees channel details."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.reader.schema import ImageInput
from app.storage.models import Channel


class Incoming(BaseModel):
    channel: Channel
    sender_ref: str  # phone number / chat id; mapped to a member server-side
    text: str | None = None  # message text or photo caption
    image: ImageInput | None = None
    received_at: datetime | None = None


class Outgoing(BaseModel):
    channel: Channel
    to_ref: str
    member_id: str | None  # None only for replies to unregistered senders
    text: str
    kind: Literal["message", "voice_call"] = "message"
