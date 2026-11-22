"""How the MCP server hands family messages over for delivery.

`StoreNotifier` (default) writes them to the store's outbox; the API process picks them up
and sends them on each member's channel (simulator / Telegram / WhatsApp). This works when
the MCP server runs in its own process. `OutboxNotifier` keeps them in memory (unit tests).
Voice calls arrive in Phase 5 behind the same method.
"""

from collections.abc import Callable
from typing import Protocol

from pydantic import BaseModel

from app.storage.base import Store
from app.storage.models import Member, OutboxMessage


class OutboundMessage(BaseModel):
    member_id: str
    kind: str  # "message" | "voice_call"
    text: str


class Notifier(Protocol):
    def send(self, member: Member, text: str) -> None: ...

    def voice_call(self, member: Member, text: str) -> None: ...


class OutboxNotifier:
    """Mock notifier: records what would be sent."""

    def __init__(self) -> None:
        self.outbox: list[OutboundMessage] = []

    def send(self, member: Member, text: str) -> None:
        self.outbox.append(OutboundMessage(member_id=member.id, kind="message", text=text))

    def voice_call(self, member: Member, text: str) -> None:
        self.outbox.append(OutboundMessage(member_id=member.id, kind="voice_call", text=text))


class StoreNotifier:
    """Writes to the shared store's outbox; the API delivers (see app/channels/service.py)."""

    def __init__(self, store_of: Callable[[], Store]) -> None:
        self._store_of = store_of

    def send(self, member: Member, text: str) -> None:
        self._store_of().add_outbox(OutboxMessage(member_id=member.id, text=text))

    def voice_call(self, member: Member, text: str) -> None:
        self._store_of().add_outbox(
            OutboxMessage(member_id=member.id, kind="voice_call", text=text)
        )
