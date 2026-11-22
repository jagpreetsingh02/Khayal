"""Storage interface. SQLite implements it now; a DynamoDB store can replace it later
without touching the MCP tools, rules or API code."""

from abc import ABC, abstractmethod
from datetime import datetime, timedelta

from app.storage.models import (
    Alert,
    AuditEntry,
    ConsentStatus,
    ConversationState,
    DoseEvent,
    DoseStatus,
    Family,
    Medicine,
    Member,
    OutboxMessage,
    Reading,
    ReadingStatus,
    ReadingType,
    Refill,
    RefillStatus,
    Role,
    SetBy,
    Sharing,
    Threshold,
)


class NotFoundError(LookupError):
    """Raised when a record does not exist."""


class ConflictError(ValueError):
    """Raised when a state change is not allowed from the record's current state."""


class Store(ABC):
    # --- families / members ---
    @abstractmethod
    def add_family(self, family: Family) -> Family: ...

    @abstractmethod
    def get_family(self, family_id: str) -> Family: ...

    @abstractmethod
    def add_member(self, member: Member) -> Member: ...

    @abstractmethod
    def get_member(self, member_id: str) -> Member: ...

    @abstractmethod
    def find_member_by_channel(self, channel: str, channel_ref: str) -> Member | None:
        """Server-side sender mapping: (channel, sender address) -> member."""

    @abstractmethod
    def set_consent(
        self,
        member_id: str,
        status: ConsentStatus,
        *,
        text_version: str | None = None,
        at: datetime | None = None,
    ) -> Member:
        """Record the parent's consent reply (§6.5): granted / paused / revoked."""

    @abstractmethod
    def list_members(self, family_id: str, role: Role | None = None) -> list[Member]: ...

    # --- readings ---
    @abstractmethod
    def add_reading(self, reading: Reading) -> Reading: ...

    @abstractmethod
    def get_reading(self, reading_id: str) -> Reading: ...

    @abstractmethod
    def list_readings(
        self,
        parent_id: str,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        type: ReadingType | None = None,
        status: ReadingStatus | None = None,
    ) -> list[Reading]:
        """Oldest first."""

    @abstractmethod
    def set_reading_status(
        self, reading_id: str, status: ReadingStatus, at: datetime | None = None
    ) -> Reading:
        """Used by the server-side read-back flow only; there is no MCP tool for it."""

    # --- medicines / doses ---
    @abstractmethod
    def add_medicine(self, medicine: Medicine) -> Medicine: ...

    @abstractmethod
    def get_medicine(self, medicine_id: str) -> Medicine: ...

    @abstractmethod
    def list_medicines(self, parent_id: str) -> list[Medicine]: ...

    @abstractmethod
    def confirm_medicine(self, medicine_id: str) -> Medicine:
        """The parent confirmed the strip read-back; only then do reminders apply."""

    @abstractmethod
    def set_medicine_times(self, medicine_id: str, times: list[str]) -> Medicine: ...

    @abstractmethod
    def set_medicine_stock(self, medicine_id: str, stock_count: int) -> Medicine: ...

    @abstractmethod
    def add_dose_event(self, event: DoseEvent) -> DoseEvent: ...

    @abstractmethod
    def mark_dose_followed_up(self, event_id: str, at: datetime) -> DoseEvent:
        """No reply 30 min after the reminder: missed, one follow-up sent (scheduler)."""

    @abstractmethod
    def list_parents(self) -> list[Member]:
        """Every parent in every family (the scheduler walks them)."""

    # --- small key/value state: demo clock, job claims, usage counters ---
    @abstractmethod
    def get_kv(self, key: str) -> str | None: ...

    @abstractmethod
    def set_kv(self, key: str, value: str) -> None: ...

    @abstractmethod
    def claim(self, key: str) -> bool:
        """Atomically mark a job key as done; True only for the first caller (idempotent jobs)."""

    @abstractmethod
    def incr_kv(self, key: str, by: int) -> int:
        """Add `by` to an integer counter and return the new value."""

    @abstractmethod
    def list_dose_events(
        self, parent_id: str, *, since: datetime | None = None, until: datetime | None = None
    ) -> list[DoseEvent]:
        """Dose events for all of a parent's medicines, oldest first."""

    @abstractmethod
    def set_dose_status(
        self, event_id: str, status: DoseStatus, at: datetime | None = None
    ) -> DoseEvent: ...

    # --- thresholds ---
    @abstractmethod
    def list_thresholds(self, parent_id: str) -> list[Threshold]:
        """Per-parent overrides only; defaults live in app.alerts.thresholds."""

    @abstractmethod
    def set_threshold(
        self, parent_id: str, rule: str, value: float, set_by: SetBy = "doctor"
    ) -> Threshold: ...

    # --- alerts ---
    @abstractmethod
    def add_alert(self, alert: Alert) -> Alert: ...

    @abstractmethod
    def get_alert(self, alert_id: str) -> Alert: ...

    @abstractmethod
    def list_alerts(self, parent_id: str, *, since: datetime | None = None) -> list[Alert]: ...

    @abstractmethod
    def acknowledge_alert(self, alert_id: str, member_id: str, at: datetime | None = None) -> Alert:
        """First acknowledgement wins; acknowledging again returns the alert unchanged."""

    # --- refills ---
    @abstractmethod
    def add_refill(self, refill: Refill) -> Refill: ...

    @abstractmethod
    def get_refill(self, refill_id: str) -> Refill: ...

    @abstractmethod
    def list_refills(
        self, parent_id: str, *, status: RefillStatus | None = None
    ) -> list[Refill]: ...

    @abstractmethod
    def decide_refill(
        self, refill_id: str, status: RefillStatus, member_id: str, at: datetime | None = None
    ) -> Refill:
        """Called only by the dashboard API after a family member's click. Atomic: only a
        `pending_approval` refill can be decided; otherwise raises ConflictError."""

    @abstractmethod
    def set_refill_pharmacy(self, refill_id: str, status: RefillStatus, ref: str) -> Refill:
        """Pharmacy hand-over after approval (keeps who decided and when)."""

    # --- sharing (parent-first, §6.5) ---
    @abstractmethod
    def get_sharing(self, parent_id: str) -> Sharing:
        """The parent's choices; defaults (everything shared) if never set."""

    @abstractmethod
    def set_sharing(self, sharing: Sharing) -> Sharing: ...

    # --- conversation state ---
    @abstractmethod
    def get_conversation_state(self, member_id: str) -> ConversationState: ...

    @abstractmethod
    def set_conversation_state(self, state: ConversationState) -> ConversationState: ...

    @abstractmethod
    def list_conversation_states(self) -> list[ConversationState]:
        """All stored states (the scheduler checks them for timeouts)."""

    # --- outbox (messages written by the MCP server, delivered by the API) ---
    @abstractmethod
    def add_outbox(self, message: OutboxMessage) -> OutboxMessage: ...

    @abstractmethod
    def take_outbox(self) -> list[OutboxMessage]:
        """Return undelivered messages, oldest first, and mark them delivered."""

    # --- audit ---
    @abstractmethod
    def add_audit(self, entry: AuditEntry) -> AuditEntry: ...

    @abstractmethod
    def list_audit(
        self, *, tool: str | None = None, parent_id: str | None = None, limit: int = 100
    ) -> list[AuditEntry]:
        """Newest first."""

    # --- demo helpers (web simulator only) ---
    def shift_parent_times(self, parent_id: str, delta: timedelta) -> None:
        """Move a parent's readings and alerts in time ("skip 5 minutes" in the simulator)."""
        raise NotImplementedError

    def reset(self) -> None:
        """Delete every row (simulator "reset demo")."""
        raise NotImplementedError
