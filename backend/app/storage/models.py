"""Typed records for every table in the data model (brief §7).

All timestamps are timezone-aware UTC. IDs are short prefixed strings so the same
records work in SQLite now and DynamoDB later.
"""

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

Role = Literal["parent", "child"]
Channel = Literal["simulator", "telegram", "twilio"]  # same names as the CHANNEL setting
ConsentStatus = Literal["pending", "granted", "paused", "revoked"]
ReadingType = Literal["bp", "glucose"]
ReadingStatus = Literal["unconfirmed", "confirmed", "rejected", "expired"]
ReadingSource = Literal["photo", "voice", "manual"]
DoseStatus = Literal["pending", "taken", "missed"]
SetBy = Literal["default", "doctor"]
AlertLevel = Literal["amber", "red"]
RefillStatus = Literal["pending_approval", "approved", "rejected", "sent_to_pharmacy", "fulfilled"]
Actor = Literal["agent", "member", "system"]


def utcnow() -> datetime:
    """Now (UTC) on the app clock: real time plus the demo offset (see app/clock.py)."""
    from app import clock

    return datetime.now(UTC) + clock.offset()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class Family(BaseModel):
    id: str = Field(default_factory=lambda: new_id("fam"))
    name: str


class Member(BaseModel):
    id: str = Field(default_factory=lambda: new_id("mem"))
    family_id: str
    role: Role
    name: str
    channel: Channel
    channel_ref: str  # phone number / chat id on that channel
    language: str = "en"
    consent_status: ConsentStatus = "pending"
    consent_at: datetime | None = None
    consent_text_version: str | None = None  # which consent notice the parent agreed to (§6.5)


class Reading(BaseModel):
    id: str = Field(default_factory=lambda: new_id("rd"))
    parent_id: str
    type: ReadingType
    systolic: int | None = None
    diastolic: int | None = None
    pulse: int | None = None
    glucose_mgdl: float | None = None
    context: str | None = None  # e.g. fasting / post_meal / random
    status: ReadingStatus = "unconfirmed"
    source: ReadingSource = "photo"
    confidence: float | None = None
    image_ref: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    confirmed_at: datetime | None = None


class Medicine(BaseModel):
    id: str = Field(default_factory=lambda: new_id("med"))
    parent_id: str
    brand: str
    generic: str
    strength: str
    times: list[str] = Field(default_factory=list)  # "HH:MM", Asia/Kolkata
    stock_count: int = 0
    daily_dose: int = 1  # units per day, used for the refill quantity cap
    is_schedule_x: bool = False
    # Extension to §7: a medicine only gets reminders once the parent confirmed it
    # (strip photo read back, or added by family). Unconfirmed = not yet verified.
    confirmed: bool = False


class DoseEvent(BaseModel):
    id: str = Field(default_factory=lambda: new_id("dose"))
    medicine_id: str
    due_at: datetime
    status: DoseStatus = "pending"
    responded_at: datetime | None = None  # the parent's own reply (None if never answered)
    # Set when the scheduler marked it missed after 30 min and sent the one gentle follow-up;
    # a later "1" still counts as taken.
    followed_up_at: datetime | None = None


class Threshold(BaseModel):
    parent_id: str
    rule: str
    value: float
    set_by: SetBy = "default"


class Alert(BaseModel):
    id: str = Field(default_factory=lambda: new_id("al"))
    parent_id: str
    level: AlertLevel
    reason: str
    reading_id: str | None = None
    notified_member_ids: list[str] = Field(default_factory=list)
    acknowledged_by: str | None = None
    acknowledged_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)


class Refill(BaseModel):
    id: str = Field(default_factory=lambda: new_id("rf"))
    medicine_id: str
    qty: int
    status: RefillStatus = "pending_approval"
    requested_by: Literal["agent"] = "agent"
    decided_by_member_id: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    decided_at: datetime | None = None
    # Demo partner pharmacy reference once handed over (no real order is placed).
    pharmacy_ref: str | None = None


ShareCategory = Literal["readings", "alerts", "medicines"]
SHARE_CATEGORIES: tuple[ShareCategory, ...] = ("readings", "alerts", "medicines")


class Sharing(BaseModel):
    """What the parent lets the family see (§6.5, parent-first). Only the parent changes it.
    Default: everything the consent notice mentions is shared."""

    parent_id: str
    readings: bool = True  # readings, charts, the doctor-set limits
    alerts: bool = True  # alerts on the dashboard and in family chats
    medicines: bool = True  # medicines, reminders, doses, refill requests
    updated_by: str | None = None  # member id
    updated_at: datetime | None = None

    def allows(self, category: ShareCategory) -> bool:
        return bool(getattr(self, category))


class ConversationState(BaseModel):
    member_id: str
    state: str = "idle"
    payload: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=utcnow)


class AuditEntry(BaseModel):
    id: str = Field(default_factory=lambda: new_id("aud"))
    actor: Actor
    tool: str
    args_json: str
    result_json: str
    # Extension to §7: which parent the call acted for (resolved server-side), if any.
    parent_id: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class OutboxMessage(BaseModel):
    """A message for a member written by the MCP server (e.g. a family alert). The MCP server
    may run in another process, so the API picks these up from the store and delivers them
    on the member's channel.

    Delivery is claimed with a short lease, marked delivered only after the channel accepted
    it, and retried with backoff otherwise (see app/channels/service.py)."""

    id: str = Field(default_factory=lambda: new_id("out"))
    member_id: str
    kind: Literal["message", "voice_call"] = "message"
    text: str
    # Optional voice note: a short phrase in the member's language (see app/voice/phrases.py).
    voice_text: str | None = None
    voice_lang: str | None = None
    # Set for alert messages: which alert, and its level (red messages retry until delivered).
    alert_id: str | None = None
    level: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    delivered_at: datetime | None = None
    attempts: int = 0  # delivery attempts started (counted when claimed)
    last_error: str | None = None
    next_attempt_at: datetime | None = None  # None: due now
    claimed_until: datetime | None = None  # lease of the process sending it right now
    gave_up_at: datetime | None = None  # final failure (never for red alert messages)

    @property
    def retries_forever(self) -> bool:
        """Red alert text messages keep retrying until delivered."""
        return self.level == "red" and self.kind == "message"
