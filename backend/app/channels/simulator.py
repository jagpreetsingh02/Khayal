"""Web simulator channel: the chat lives in memory and the /simulator page polls it.

Photos are not kept: the page shows its own copy, the feed only notes that a photo was sent
(and which demo sample, if it was one).
"""

import base64
import binascii
import itertools
import threading
from typing import Any, Literal

from pydantic import BaseModel

from app.channels.base import Channel, ChannelError
from app.conversation.messages import Incoming, Outgoing
from app.reader.schema import ImageInput
from app.storage.models import utcnow

MAX_PHOTO_BYTES = 10 * 1024 * 1024
FEED_LIMIT = 200  # messages kept per chat


class SimulatorEvent(BaseModel):
    """What the page posts to /simulator/api/send."""

    sender_ref: str
    text: str | None = None
    sample: str | None = None  # a file in samples/ (the page's sample picker)
    image_b64: str | None = None  # an uploaded photo
    filename: str | None = None
    client_id: str | None = None  # lets the page show its own preview of the photo


class FeedItem(BaseModel):
    seq: int
    at: str
    direction: Literal["in", "out", "note"]  # in = sent by the member, out = sent to them
    text: str | None = None
    kind: str = "message"
    photo: bool = False
    sample: str | None = None
    client_id: str | None = None


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

    def send(self, msg: Outgoing) -> None:
        self._add(msg.to_ref, direction="out", text=msg.text, kind=msg.kind)

    def note(self, ref: str, text: str) -> None:
        """A grey system line in a chat, e.g. "5 minutes later"."""
        self._add(ref, direction="note", text=text)

    def feed(self, ref: str) -> list[FeedItem]:
        with self._lock:
            return list(self._feeds.get(ref, []))

    def clear(self) -> None:
        with self._lock:
            self._feeds.clear()
