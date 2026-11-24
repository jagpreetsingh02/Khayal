"""Channel layer (brief §5.1): turn a channel's inbound event into an `Incoming`, and send
an `Outgoing` back out. The conversation engine never sees channel details.

Implementations: simulator (web page, always on), telegram, twilio (WhatsApp).
Sender mapping (channel address -> member) happens server-side in the store, never here.
"""

from abc import ABC, abstractmethod
from typing import Any

from app.conversation.messages import Incoming, Outgoing


class ChannelError(RuntimeError):
    """A channel could not receive or deliver a message."""


class Channel(ABC):
    name: str

    @abstractmethod
    def receive(self, event: Any) -> Incoming | None:
        """Channel-specific event -> Incoming (None = nothing to handle, e.g. a sticker)."""

    @abstractmethod
    def send(self, msg: Outgoing) -> None:
        """Deliver one message to `msg.to_ref` on this channel. Returns only when the
        channel accepted it; raises (e.g. ChannelError) otherwise, so it can be retried."""

    def sender_of(self, event: Any) -> str | None:
        """The sender's address in a raw event, without any network call (used to keep one
        sender's messages in order before the event is fully received)."""
        return getattr(event, "sender_ref", None)
