"""SQLite implementation of the Store interface (stdlib sqlite3, no ORM)."""

import json
import sqlite3
import threading
from datetime import UTC, datetime
from typing import Any, TypeVar

from pydantic import BaseModel

from app.storage.base import NotFoundError, Store
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
    Reading,
    ReadingStatus,
    ReadingType,
    Refill,
    RefillStatus,
    Role,
    SetBy,
    Threshold,
    utcnow,
)

M = TypeVar("M", bound=BaseModel)

SCHEMA = """
CREATE TABLE IF NOT EXISTS families (id TEXT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS members (
  id TEXT PRIMARY KEY, family_id TEXT NOT NULL REFERENCES families(id),
  role TEXT NOT NULL, name TEXT NOT NULL, channel TEXT NOT NULL, channel_ref TEXT NOT NULL,
  language TEXT NOT NULL, consent_status TEXT NOT NULL, consent_at TEXT,
  consent_text_version TEXT,
  UNIQUE (channel, channel_ref)
);
CREATE TABLE IF NOT EXISTS readings (
  id TEXT PRIMARY KEY, parent_id TEXT NOT NULL REFERENCES members(id), type TEXT NOT NULL,
  systolic INTEGER, diastolic INTEGER, pulse INTEGER, glucose_mgdl REAL, context TEXT,
  status TEXT NOT NULL, source TEXT NOT NULL, confidence REAL, image_ref TEXT,
  created_at TEXT NOT NULL, confirmed_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_readings_parent_time ON readings(parent_id, created_at);
CREATE TABLE IF NOT EXISTS medicines (
  id TEXT PRIMARY KEY, parent_id TEXT NOT NULL REFERENCES members(id), brand TEXT NOT NULL,
  generic TEXT NOT NULL, strength TEXT NOT NULL, times TEXT NOT NULL, stock_count INTEGER NOT NULL,
  daily_dose INTEGER NOT NULL, is_schedule_x INTEGER NOT NULL, confirmed INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS dose_events (
  id TEXT PRIMARY KEY, medicine_id TEXT NOT NULL REFERENCES medicines(id), due_at TEXT NOT NULL,
  status TEXT NOT NULL, responded_at TEXT
);
CREATE TABLE IF NOT EXISTS thresholds (
  parent_id TEXT NOT NULL REFERENCES members(id), rule TEXT NOT NULL, value REAL NOT NULL,
  set_by TEXT NOT NULL, PRIMARY KEY (parent_id, rule)
);
CREATE TABLE IF NOT EXISTS alerts (
  id TEXT PRIMARY KEY, parent_id TEXT NOT NULL REFERENCES members(id), level TEXT NOT NULL,
  reason TEXT NOT NULL, reading_id TEXT, notified_member_ids TEXT NOT NULL,
  acknowledged_by TEXT, acknowledged_at TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS refills (
  id TEXT PRIMARY KEY, medicine_id TEXT NOT NULL REFERENCES medicines(id), qty INTEGER NOT NULL,
  status TEXT NOT NULL, requested_by TEXT NOT NULL, decided_by_member_id TEXT,
  created_at TEXT NOT NULL, decided_at TEXT
);
CREATE TABLE IF NOT EXISTS conversation_state (
  member_id TEXT PRIMARY KEY REFERENCES members(id), state TEXT NOT NULL, payload TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
  id TEXT PRIMARY KEY, actor TEXT NOT NULL, tool TEXT NOT NULL, args_json TEXT NOT NULL,
  result_json TEXT NOT NULL, parent_id TEXT, created_at TEXT NOT NULL
);
"""

# Columns added after a table first shipped: (table, column, SQL type). Applied on open so an
# existing local database keeps working.
MIGRATIONS = [("members", "consent_text_version", "TEXT")]

# Columns stored as JSON text.
JSON_COLUMNS = {"times", "notified_member_ids", "payload"}


def _ts(value: datetime) -> str:
    """Fixed-width UTC timestamp so string comparison in SQL matches time order."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _to_row(model: BaseModel) -> dict[str, Any]:
    row: dict[str, Any] = {}
    for key, value in model.model_dump().items():
        if isinstance(value, datetime):
            value = _ts(value)
        elif key in JSON_COLUMNS:
            value = json.dumps(value)
        elif isinstance(value, bool):
            value = int(value)
        row[key] = value
    return row


def _from_row(cls: type[M], row: sqlite3.Row) -> M:
    data = {k: (json.loads(row[k]) if k in JSON_COLUMNS else row[k]) for k in row.keys()}  # noqa: SIM118
    return cls.model_validate(data)


def path_from_url(database_url: str) -> str:
    """`sqlite:///./care_circle.db` -> `./care_circle.db`; `sqlite:///:memory:` -> `:memory:`."""
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise ValueError(f"Not a sqlite URL: {database_url!r}")
    return database_url[len(prefix) :]


class SQLiteStore(Store):
    def __init__(self, path: str = ":memory:") -> None:
        # One shared connection guarded by a lock: works for :memory: and for the
        # thread pool FastMCP / FastAPI use for sync handlers.
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._lock = threading.Lock()
        with self._lock, self._conn:
            self._conn.executescript(SCHEMA)
            for table, column, sql_type in MIGRATIONS:
                cols = {r["name"] for r in self._conn.execute(f"PRAGMA table_info({table})")}
                if column not in cols:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")

    @classmethod
    def from_url(cls, database_url: str) -> "SQLiteStore":
        return cls(path_from_url(database_url))

    def close(self) -> None:
        self._conn.close()

    # --- generic helpers ---
    def _insert(self, table: str, model: M, *, upsert: bool = False) -> M:
        row = _to_row(model)
        cols = ", ".join(row)
        marks = ", ".join(f":{c}" for c in row)
        verb = "INSERT OR REPLACE" if upsert else "INSERT"
        with self._lock, self._conn:
            self._conn.execute(f"{verb} INTO {table} ({cols}) VALUES ({marks})", row)
        return model

    def _query(self, cls: type[M], sql: str, params: tuple | dict = ()) -> list[M]:
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [_from_row(cls, r) for r in rows]

    def _one(self, cls: type[M], table: str, key: str, value: str) -> M:
        found = self._query(cls, f"SELECT * FROM {table} WHERE {key} = ?", (value,))
        if not found:
            raise NotFoundError(f"{table}: no row with {key}={value!r}")
        return found[0]

    def _update(self, table: str, key: str, value: str, fields: dict[str, Any]) -> None:
        sets = ", ".join(f"{k} = :{k}" for k in fields)
        params = {k: (_ts(v) if isinstance(v, datetime) else v) for k, v in fields.items()}
        params["_key"] = value
        with self._lock, self._conn:
            cur = self._conn.execute(f"UPDATE {table} SET {sets} WHERE {key} = :_key", params)
        if cur.rowcount == 0:
            raise NotFoundError(f"{table}: no row with {key}={value!r}")

    @staticmethod
    def _time_filters(
        column: str, since: datetime | None, until: datetime | None
    ) -> tuple[list[str], list[Any]]:
        where: list[str] = []
        params: list[Any] = []
        if since is not None:
            where.append(f"{column} >= ?")
            params.append(_ts(since))
        if until is not None:
            where.append(f"{column} < ?")
            params.append(_ts(until))
        return where, params

    # --- families / members ---
    def add_family(self, family: Family) -> Family:
        return self._insert("families", family)

    def get_family(self, family_id: str) -> Family:
        return self._one(Family, "families", "id", family_id)

    def add_member(self, member: Member) -> Member:
        return self._insert("members", member)

    def get_member(self, member_id: str) -> Member:
        return self._one(Member, "members", "id", member_id)

    def find_member_by_channel(self, channel: str, channel_ref: str) -> Member | None:
        found = self._query(
            Member,
            "SELECT * FROM members WHERE channel = ? AND channel_ref = ?",
            (channel, channel_ref),
        )
        return found[0] if found else None

    def set_consent(
        self,
        member_id: str,
        status: ConsentStatus,
        *,
        text_version: str | None = None,
        at: datetime | None = None,
    ) -> Member:
        fields: dict[str, Any] = {"consent_status": status, "consent_at": at or utcnow()}
        if text_version is not None:
            fields["consent_text_version"] = text_version
        self._update("members", "id", member_id, fields)
        return self.get_member(member_id)

    def list_members(self, family_id: str, role: Role | None = None) -> list[Member]:
        sql, params = "SELECT * FROM members WHERE family_id = ?", [family_id]
        if role:
            sql += " AND role = ?"
            params.append(role)
        return self._query(Member, sql + " ORDER BY name", tuple(params))

    # --- readings ---
    def add_reading(self, reading: Reading) -> Reading:
        return self._insert("readings", reading)

    def get_reading(self, reading_id: str) -> Reading:
        return self._one(Reading, "readings", "id", reading_id)

    def list_readings(
        self,
        parent_id: str,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        type: ReadingType | None = None,
        status: ReadingStatus | None = None,
    ) -> list[Reading]:
        where, params = self._time_filters("created_at", since, until)
        where.insert(0, "parent_id = ?")
        params.insert(0, parent_id)
        if type:
            where.append("type = ?")
            params.append(type)
        if status:
            where.append("status = ?")
            params.append(status)
        sql = f"SELECT * FROM readings WHERE {' AND '.join(where)} ORDER BY created_at"
        return self._query(Reading, sql, tuple(params))

    def set_reading_status(
        self, reading_id: str, status: ReadingStatus, at: datetime | None = None
    ) -> Reading:
        fields: dict[str, Any] = {"status": status}
        if status == "confirmed":
            fields["confirmed_at"] = at or utcnow()
        self._update("readings", "id", reading_id, fields)
        return self.get_reading(reading_id)

    # --- medicines / doses ---
    def add_medicine(self, medicine: Medicine) -> Medicine:
        return self._insert("medicines", medicine)

    def get_medicine(self, medicine_id: str) -> Medicine:
        return self._one(Medicine, "medicines", "id", medicine_id)

    def list_medicines(self, parent_id: str) -> list[Medicine]:
        return self._query(
            Medicine, "SELECT * FROM medicines WHERE parent_id = ? ORDER BY brand", (parent_id,)
        )

    def confirm_medicine(self, medicine_id: str) -> Medicine:
        self._update("medicines", "id", medicine_id, {"confirmed": 1})
        return self.get_medicine(medicine_id)

    def set_medicine_times(self, medicine_id: str, times: list[str]) -> Medicine:
        self._update("medicines", "id", medicine_id, {"times": json.dumps(times)})
        return self.get_medicine(medicine_id)

    def add_dose_event(self, event: DoseEvent) -> DoseEvent:
        return self._insert("dose_events", event)

    def list_dose_events(
        self, parent_id: str, *, since: datetime | None = None, until: datetime | None = None
    ) -> list[DoseEvent]:
        where, params = self._time_filters("d.due_at", since, until)
        where.insert(0, "m.parent_id = ?")
        params.insert(0, parent_id)
        sql = (
            "SELECT d.* FROM dose_events d JOIN medicines m ON m.id = d.medicine_id "
            f"WHERE {' AND '.join(where)} ORDER BY d.due_at"
        )
        return self._query(DoseEvent, sql, tuple(params))

    def set_dose_status(
        self, event_id: str, status: DoseStatus, at: datetime | None = None
    ) -> DoseEvent:
        self._update(
            "dose_events", "id", event_id, {"status": status, "responded_at": at or utcnow()}
        )
        return self._one(DoseEvent, "dose_events", "id", event_id)

    # --- thresholds ---
    def list_thresholds(self, parent_id: str) -> list[Threshold]:
        return self._query(
            Threshold, "SELECT * FROM thresholds WHERE parent_id = ? ORDER BY rule", (parent_id,)
        )

    def set_threshold(
        self, parent_id: str, rule: str, value: float, set_by: SetBy = "doctor"
    ) -> Threshold:
        threshold = Threshold(parent_id=parent_id, rule=rule, value=value, set_by=set_by)
        return self._insert("thresholds", threshold, upsert=True)

    # --- alerts ---
    def add_alert(self, alert: Alert) -> Alert:
        return self._insert("alerts", alert)

    def list_alerts(self, parent_id: str, *, since: datetime | None = None) -> list[Alert]:
        where, params = self._time_filters("created_at", since, None)
        where.insert(0, "parent_id = ?")
        params.insert(0, parent_id)
        sql = f"SELECT * FROM alerts WHERE {' AND '.join(where)} ORDER BY created_at"
        return self._query(Alert, sql, tuple(params))

    # --- refills ---
    def add_refill(self, refill: Refill) -> Refill:
        return self._insert("refills", refill)

    def get_refill(self, refill_id: str) -> Refill:
        return self._one(Refill, "refills", "id", refill_id)

    def list_refills(self, parent_id: str, *, status: RefillStatus | None = None) -> list[Refill]:
        sql = (
            "SELECT r.* FROM refills r JOIN medicines m ON m.id = r.medicine_id "
            "WHERE m.parent_id = ?"
        )
        params: list[Any] = [parent_id]
        if status:
            sql += " AND r.status = ?"
            params.append(status)
        return self._query(Refill, sql + " ORDER BY r.created_at", tuple(params))

    def decide_refill(
        self, refill_id: str, status: RefillStatus, member_id: str, at: datetime | None = None
    ) -> Refill:
        self._update(
            "refills",
            "id",
            refill_id,
            {"status": status, "decided_by_member_id": member_id, "decided_at": at or utcnow()},
        )
        return self.get_refill(refill_id)

    # --- conversation state ---
    def get_conversation_state(self, member_id: str) -> ConversationState:
        found = self._query(
            ConversationState, "SELECT * FROM conversation_state WHERE member_id = ?", (member_id,)
        )
        return found[0] if found else ConversationState(member_id=member_id)

    def set_conversation_state(self, state: ConversationState) -> ConversationState:
        state = state.model_copy(update={"updated_at": utcnow()})
        return self._insert("conversation_state", state, upsert=True)

    # --- audit ---
    def add_audit(self, entry: AuditEntry) -> AuditEntry:
        return self._insert("audit_log", entry)

    def list_audit(self, *, tool: str | None = None, limit: int = 100) -> list[AuditEntry]:
        sql, params = "SELECT * FROM audit_log", []
        if tool:
            sql += " WHERE tool = ?"
            params.append(tool)
        sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
        params.append(limit)
        return self._query(AuditEntry, sql, tuple(params))
