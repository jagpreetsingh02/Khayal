"""SQLite implementation of the Store interface (stdlib sqlite3, no ORM)."""

import json
import sqlite3
import threading
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

from pydantic import BaseModel

from app.storage.base import ConflictError, NotFoundError, Store
from app.storage.models import (
    REDACTED_TEXT,
    Alert,
    AuditEntry,
    ConsentStatus,
    ConversationState,
    DoseEvent,
    DoseStatus,
    Family,
    LoginCode,
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
  created_at TEXT NOT NULL, decided_at TEXT, pharmacy_ref TEXT
);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sharing (
  parent_id TEXT PRIMARY KEY REFERENCES members(id), readings INTEGER NOT NULL,
  alerts INTEGER NOT NULL, medicines INTEGER NOT NULL, updated_by TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS conversation_state (
  member_id TEXT PRIMARY KEY REFERENCES members(id), state TEXT NOT NULL, payload TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS outbox (
  id TEXT PRIMARY KEY, member_id TEXT NOT NULL REFERENCES members(id), kind TEXT NOT NULL,
  text TEXT NOT NULL, created_at TEXT NOT NULL, delivered_at TEXT
);
CREATE TABLE IF NOT EXISTS inbound_seen (
  channel TEXT NOT NULL, external_id TEXT NOT NULL, received_at TEXT NOT NULL,
  PRIMARY KEY (channel, external_id)
);
CREATE TABLE IF NOT EXISTS login_codes (
  id TEXT PRIMARY KEY, member_id TEXT NOT NULL REFERENCES members(id), code_hash TEXT NOT NULL,
  created_at TEXT NOT NULL, expires_at TEXT NOT NULL, tries INTEGER NOT NULL DEFAULT 0,
  used_at TEXT, revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS audit_log (
  id TEXT PRIMARY KEY, actor TEXT NOT NULL, tool TEXT NOT NULL, args_json TEXT NOT NULL,
  result_json TEXT NOT NULL, parent_id TEXT, created_at TEXT NOT NULL
);
"""

# Columns added after a table first shipped: (table, column, SQL type). Applied on open so an
# existing local database keeps working.
MIGRATIONS = [
    ("members", "consent_text_version", "TEXT"),
    ("refills", "pharmacy_ref", "TEXT"),
    ("dose_events", "followed_up_at", "TEXT"),
    ("outbox", "voice_text", "TEXT"),
    ("outbox", "voice_lang", "TEXT"),
    ("outbox", "attempts", "INTEGER NOT NULL DEFAULT 0"),
    ("outbox", "last_error", "TEXT"),
    ("outbox", "next_attempt_at", "TEXT"),
    ("outbox", "claimed_until", "TEXT"),
    ("outbox", "gave_up_at", "TEXT"),
    ("outbox", "alert_id", "TEXT"),
    ("outbox", "level", "TEXT"),
    ("outbox", "expires_at", "TEXT"),
    ("outbox", "sensitive", "INTEGER NOT NULL DEFAULT 0"),
]

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
    """`sqlite:///./khayal.db` -> `./khayal.db`; `sqlite:///:memory:` -> `:memory:`."""
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise ValueError(f"Not a sqlite URL: {database_url!r}")
    return database_url[len(prefix) :]


class SQLiteStore(Store):
    def __init__(self, path: str = ":memory:") -> None:
        # One shared connection guarded by a lock: works for :memory: and for the
        # thread pool FastMCP / FastAPI use for sync handlers.
        # The API and the MCP server are separate processes sharing one file: WAL lets one
        # read while the other writes, and busy_timeout waits out short write locks.
        self._conn = sqlite3.connect(path, check_same_thread=False, timeout=10)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        if path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
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

    def ping(self) -> None:
        with self._lock:
            self._conn.execute("SELECT 1").fetchone()

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

    def set_medicine_stock(self, medicine_id: str, stock_count: int) -> Medicine:
        self._update("medicines", "id", medicine_id, {"stock_count": stock_count})
        return self.get_medicine(medicine_id)

    def add_dose_event(self, event: DoseEvent) -> DoseEvent:
        return self._insert("dose_events", event)

    def mark_dose_followed_up(self, event_id: str, at: datetime) -> DoseEvent:
        self._update("dose_events", "id", event_id, {"status": "missed", "followed_up_at": at})
        return self._one(DoseEvent, "dose_events", "id", event_id)

    def list_parents(self) -> list[Member]:
        return self._query(Member, "SELECT * FROM members WHERE role = 'parent' ORDER BY name")

    # --- kv ---
    def get_kv(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_kv(self, key: str, value: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO kv (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def claim(self, key: str) -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO kv (key, value) VALUES (?, ?)", (key, _ts(utcnow()))
            )
        return cur.rowcount == 1

    def incr_kv(self, key: str, by: int) -> int:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO kv (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + ?",
                (key, str(by), by),
            )
            row = self._conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return int(row["value"])

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

    def get_alert(self, alert_id: str) -> Alert:
        return self._one(Alert, "alerts", "id", alert_id)

    def acknowledge_alert(self, alert_id: str, member_id: str, at: datetime | None = None) -> Alert:
        self.get_alert(alert_id)  # NotFoundError for unknown ids
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE alerts SET acknowledged_by = ?, acknowledged_at = ? "
                "WHERE id = ? AND acknowledged_by IS NULL",
                (member_id, _ts(at or utcnow()), alert_id),
            )
        return self.get_alert(alert_id)

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
        self.get_refill(refill_id)  # NotFoundError for unknown ids
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE refills SET status = ?, decided_by_member_id = ?, decided_at = ? "
                "WHERE id = ? AND status = 'pending_approval'",
                (status, member_id, _ts(at or utcnow()), refill_id),
            )
        if cur.rowcount == 0:  # someone decided it first (or it was never pending)
            raise ConflictError(f"Refill {refill_id} is {self.get_refill(refill_id).status}")
        return self.get_refill(refill_id)

    def set_refill_pharmacy(self, refill_id: str, status: RefillStatus, ref: str) -> Refill:
        self._update("refills", "id", refill_id, {"status": status, "pharmacy_ref": ref})
        return self.get_refill(refill_id)

    # --- sharing ---
    def get_sharing(self, parent_id: str) -> Sharing:
        found = self._query(Sharing, "SELECT * FROM sharing WHERE parent_id = ?", (parent_id,))
        return found[0] if found else Sharing(parent_id=parent_id)

    def set_sharing(self, sharing: Sharing) -> Sharing:
        sharing = sharing.model_copy(update={"updated_at": sharing.updated_at or utcnow()})
        return self._insert("sharing", sharing, upsert=True)

    # --- conversation state ---
    def get_conversation_state(self, member_id: str) -> ConversationState:
        found = self._query(
            ConversationState, "SELECT * FROM conversation_state WHERE member_id = ?", (member_id,)
        )
        return found[0] if found else ConversationState(member_id=member_id)

    def set_conversation_state(self, state: ConversationState) -> ConversationState:
        state = state.model_copy(update={"updated_at": utcnow()})
        return self._insert("conversation_state", state, upsert=True)

    def list_conversation_states(self) -> list[ConversationState]:
        return self._query(ConversationState, "SELECT * FROM conversation_state")

    # --- outbox ---
    def add_outbox(self, message: OutboxMessage) -> OutboxMessage:
        return self._insert("outbox", message)

    def claim_outbox(self, now: datetime, lease: timedelta, limit: int = 50) -> list[OutboxMessage]:
        # One UPDATE ... RETURNING: SQLite runs a write statement alone (per connection and,
        # via the database lock, across processes), so two claimers never get the same row.
        with self._lock, self._conn:
            rows = self._conn.execute(
                "UPDATE outbox SET claimed_until = :until, attempts = attempts + 1 "
                "WHERE id IN (SELECT id FROM outbox WHERE delivered_at IS NULL "
                "  AND gave_up_at IS NULL "
                "  AND (next_attempt_at IS NULL OR next_attempt_at <= :now) "
                "  AND (claimed_until IS NULL OR claimed_until <= :now) "
                "  AND (expires_at IS NULL OR expires_at > :now) "
                "  ORDER BY created_at, rowid LIMIT :limit) "
                "RETURNING rowid AS seq, *",
                {"now": _ts(now), "until": _ts(now + lease), "limit": limit},
            ).fetchall()
        rows = sorted(rows, key=lambda r: (r["created_at"], r["seq"]))
        return [
            OutboxMessage.model_validate({k: r[k] for k in r.keys() if k != "seq"})  # noqa: SIM118
            for r in rows
        ]

    def mark_outbox_delivered(self, message_id: str, at: datetime | None = None) -> None:
        self._finish_outbox(
            message_id,
            "delivered_at = :at, claimed_until = NULL, last_error = NULL",
            {"at": _ts(at or utcnow())},
        )

    def mark_outbox_failed(
        self,
        message_id: str,
        error: str,
        next_attempt_at: datetime | None,
        at: datetime | None = None,
    ) -> None:
        sets = "last_error = :error, claimed_until = NULL, next_attempt_at = :next"
        params: dict[str, Any] = {
            "error": error[:500],
            "next": _ts(next_attempt_at) if next_attempt_at else None,
        }
        if next_attempt_at is None:  # final: give up (and drop a secret's text)
            sets += ", gave_up_at = :at"
            params["at"] = _ts(at or utcnow())
        self._finish_outbox(message_id, sets, params, redact=next_attempt_at is None)

    def _finish_outbox(
        self, message_id: str, sets: str, params: dict[str, Any], *, redact: bool = True
    ) -> None:
        """Update one outbox row; a sensitive row's text is blanked when `redact`."""
        if redact:
            sets += ", text = CASE WHEN sensitive THEN :redacted ELSE text END"
        with self._lock, self._conn:
            cur = self._conn.execute(
                f"UPDATE outbox SET {sets} WHERE id = :_id",
                {**params, "_id": message_id, "redacted": REDACTED_TEXT},
            )
        if cur.rowcount == 0:
            raise NotFoundError(f"outbox: no row with id={message_id!r}")

    def expire_outbox(self, now: datetime, *, member_id: str | None = None) -> list[OutboxMessage]:
        expiring = "(expires_at IS NOT NULL AND expires_at <= :now)"
        params: dict[str, Any] = {"now": _ts(now), "redacted": REDACTED_TEXT}
        if member_id is not None:  # every pending secret of this member, due or not
            expiring, params["member"] = "(member_id = :member AND sensitive)", member_id
        with self._lock, self._conn:
            rows = self._conn.execute(
                "UPDATE outbox SET gave_up_at = :now, last_error = 'expired', "
                "  claimed_until = NULL, "
                "  text = CASE WHEN sensitive THEN :redacted ELSE text END "
                f"WHERE delivered_at IS NULL AND gave_up_at IS NULL AND {expiring} "
                "  AND (claimed_until IS NULL OR claimed_until <= :now) "
                "RETURNING *",
                params,
            ).fetchall()
        return [_from_row(OutboxMessage, r) for r in rows]

    def get_outbox(self, message_id: str) -> OutboxMessage:
        return self._one(OutboxMessage, "outbox", "id", message_id)

    def list_outbox(self, *, alert_id: str | None = None) -> list[OutboxMessage]:
        sql = "SELECT * FROM outbox"
        params: tuple[str, ...] = ()
        if alert_id is not None:
            sql, params = sql + " WHERE alert_id = ?", (alert_id,)
        return self._query(OutboxMessage, sql + " ORDER BY created_at, rowid", params)

    # --- dashboard sign-in codes ---
    def add_login_code(self, code: LoginCode) -> LoginCode:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE login_codes SET revoked_at = ? "
                "WHERE member_id = ? AND used_at IS NULL AND revoked_at IS NULL",
                (_ts(code.created_at), code.member_id),
            )
            row = _to_row(code)
            cols, marks = ", ".join(row), ", ".join(f":{c}" for c in row)
            self._conn.execute(f"INSERT INTO login_codes ({cols}) VALUES ({marks})", row)
        return code

    def active_login_code(self, member_id: str, now: datetime) -> LoginCode | None:
        found = self._query(
            LoginCode,
            "SELECT * FROM login_codes WHERE member_id = ? AND used_at IS NULL "
            "AND revoked_at IS NULL AND expires_at > ? ORDER BY created_at DESC, rowid DESC "
            "LIMIT 1",
            (member_id, _ts(now)),
        )
        return found[0] if found else None

    def count_login_try(self, code_id: str, max_tries: int) -> int | None:
        with self._lock, self._conn:
            row = self._conn.execute(
                "UPDATE login_codes SET tries = tries + 1 WHERE id = ? AND tries < ? "
                "AND used_at IS NULL AND revoked_at IS NULL RETURNING tries",
                (code_id, max_tries),
            ).fetchone()
        return row["tries"] if row else None

    def use_login_code(self, code_id: str, at: datetime) -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE login_codes SET used_at = ? WHERE id = ? AND used_at IS NULL "
                "AND revoked_at IS NULL",
                (_ts(at), code_id),
            )
        return cur.rowcount == 1

    # --- inbound dedup ---
    def mark_inbound_seen(self, channel: str, external_id: str) -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO inbound_seen (channel, external_id, received_at) "
                "VALUES (?, ?, ?)",
                (channel, external_id, _ts(utcnow())),
            )
        return cur.rowcount == 1

    # --- audit ---
    def add_audit(self, entry: AuditEntry) -> AuditEntry:
        return self._insert("audit_log", entry)

    def list_audit(
        self, *, tool: str | None = None, parent_id: str | None = None, limit: int = 100
    ) -> list[AuditEntry]:
        where: list[str] = []
        params: list[Any] = []
        if tool:
            where.append("tool = ?")
            params.append(tool)
        if parent_id:
            where.append("parent_id = ?")
            params.append(parent_id)
        sql = "SELECT * FROM audit_log" + (f" WHERE {' AND '.join(where)}" if where else "")
        sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
        params.append(limit)
        return self._query(AuditEntry, sql, tuple(params))

    # --- demo helpers ---
    def shift_parent_times(self, parent_id: str, delta: timedelta) -> None:
        moved: list[tuple[str, Sequence[Reading | Alert], tuple[str, ...]]] = [
            ("readings", self.list_readings(parent_id), ("created_at", "confirmed_at")),
            ("alerts", self.list_alerts(parent_id), ("created_at",)),
        ]
        with self._lock, self._conn:
            for table, rows, columns in moved:
                for row in rows:
                    fields = {c: _ts(getattr(row, c) + delta) for c in columns if getattr(row, c)}
                    sets = ", ".join(f"{c} = :{c}" for c in fields)
                    self._conn.execute(
                        f"UPDATE {table} SET {sets} WHERE id = :id", fields | {"id": row.id}
                    )

    def reset(self) -> None:
        tables = [
            "kv",
            "inbound_seen",
            "outbox",
            "login_codes",
            "audit_log",
            "sharing",
            "conversation_state",
            "refills",
            "alerts",
            "thresholds",
            "dose_events",
            "medicines",
            "readings",
            "members",
            "families",
        ]
        with self._lock, self._conn:
            for table in tables:
                self._conn.execute(f"DELETE FROM {table}")
