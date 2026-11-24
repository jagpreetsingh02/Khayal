"""How the MCP server hands family messages over for delivery.

`StoreNotifier` (default) writes them to the store's outbox; the API process picks them up
and sends them on each member's channel (simulator / Telegram / WhatsApp). This works when
the MCP server runs in its own process. `OutboxNotifier` keeps them in memory (unit tests).
Alert messages carry the alert id and level, so delivery can be tracked per alert and
red alert messages retried until delivered.
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
    alert_id: str | None = None
    level: str | None = None


class Notifier(Protocol):
    def send(
        self, member: Member, text: str, *, alert_id: str | None = None, level: str | None = None
    ) -> None: ...

    def voice_call(
        self, member: Member, text: str, *, alert_id: str | None = None, level: str | None = None
    ) -> None: ...


class OutboxNotifier:
    """Mock notifier: records what would be sent."""

    def __init__(self) -> None:
        self.outbox: list[OutboundMessage] = []

    def send(
        self, member: Member, text: str, *, alert_id: str | None = None, level: str | None = None
    ) -> None:
        self.outbox.append(
            OutboundMessage(
                member_id=member.id, kind="message", text=text, alert_id=alert_id, level=level
            )
        )

    def voice_call(
        self, member: Member, text: str, *, alert_id: str | None = None, level: str | None = None
    ) -> None:
        self.outbox.append(
            OutboundMessage(
                member_id=member.id, kind="voice_call", text=text, alert_id=alert_id, level=level
            )
        )


class StoreNotifier:
    """Writes to the shared store's outbox; the API delivers (see app/channels/service.py)."""

    def __init__(self, store_of: Callable[[], Store]) -> None:
        self._store_of = store_of

    def send(
        self, member: Member, text: str, *, alert_id: str | None = None, level: str | None = None
    ) -> None:
        self._store_of().add_outbox(
            OutboxMessage(member_id=member.id, text=text, alert_id=alert_id, level=level)
        )

    def voice_call(
        self, member: Member, text: str, *, alert_id: str | None = None, level: str | None = None
    ) -> None:
        self._store_of().add_outbox(
            OutboxMessage(
                member_id=member.id, kind="voice_call", text=text, alert_id=alert_id, level=level
            )
        )
