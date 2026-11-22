"""Storage interface. SQLite implements it now; a DynamoDB store can replace it later
without touching the MCP tools, rules or API code."""

from abc import ABC, abstractmethod
from datetime import datetime

from app.storage.models import (
    Alert,
    AuditEntry,
    ConversationState,
    DoseEvent,
    DoseStatus,
    Family,
    Medicine,
    Member,
    Reading,
    ReadingStatus,
    ReadingType,
    Refill,
    RefillStatus,
    Role,
    SetBy,
    Threshold,
)


class NotFoundError(LookupError):
    """Raised when a record does not exist."""


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
    def set_medicine_times(self, medicine_id: str, times: list[str]) -> Medicine: ...

    @abstractmethod
    def add_dose_event(self, event: DoseEvent) -> DoseEvent: ...

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
    def list_alerts(self, parent_id: str, *, since: datetime | None = None) -> list[Alert]: ...

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
        """Called only by the dashboard API after a family member's click."""

    # --- conversation state ---
    @abstractmethod
    def get_conversation_state(self, member_id: str) -> ConversationState: ...

    @abstractmethod
    def set_conversation_state(self, state: ConversationState) -> ConversationState: ...

    # --- audit ---
    @abstractmethod
    def add_audit(self, entry: AuditEntry) -> AuditEntry: ...

    @abstractmethod
    def list_audit(self, *, tool: str | None = None, limit: int = 100) -> list[AuditEntry]:
        """Newest first."""
