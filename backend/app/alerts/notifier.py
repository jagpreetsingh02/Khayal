"""Delivers alert messages to family members.

Phase 1 ships the mock outbox only (messages kept in memory, visible in tests and the
simulator). Real channel delivery (Telegram / WhatsApp) and voice calls plug in later
behind the same two methods.
"""

from typing import Protocol

from pydantic import BaseModel

from app.storage.models import Member


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
