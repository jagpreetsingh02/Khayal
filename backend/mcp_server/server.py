"""Khayal MCP server (brief §5.5).

Guardrails built in:
- The parent is resolved server-side from the request sender (see identity.py); no tool
  takes a parent_id / member_id argument.
- log_reading only writes `unconfirmed` readings. Confirmation is the parent's read-back
  reply, handled server-side; there is no MCP tool that confirms a reading.
- alert_family only fires for confirmed readings / server-evaluated rules, caps amber,
  never caps red, and families only ever see server-written text. Amber is not sent when
  the parent has chosen not to share alerts (parent-first); red always goes out. A first severe BP
  alerts nobody: red needs a confirming repeat, amber needs 15 minutes without one.
- Consent (§6.5): a parent who has not agreed, or has paused / revoked, is refused by
  every tool (nothing logged, alerted or reminded).
- request_refill creates `pending_approval` only, capped at one month's supply, never for
  Schedule X medicines, and tells the family (server-written text) to decide on the dashboard.
- There is deliberately NO approve_refill tool: approval exists only in the dashboard
  API, triggered by a family member.
- weekly_summary passes through an output filter that blocks diagnosis / dosing words.
- Every tool call (including failures) is written to audit_log.
"""

import json
import re
from collections.abc import Callable
from datetime import datetime, timedelta
from statistics import mean
from typing import Annotated, Any, Literal
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.tools.base import ToolResult
from mcp_types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from app import clock as app_clock
from app.alerts.evaluate import evaluate_for_alert, whose_doctor
from app.alerts.notifier import Notifier, StoreNotifier
from app.alerts.thresholds import (
    LEVEL_RANK,
    Evaluation,
    alert_level,
    effective_thresholds,
    evaluate_missed_doses,
    local_today,
)
from app.config import get_settings
from app.conversation.templates import format_value, read_back
from app.reader.intake import build_reading
from app.storage import ConflictError, NotFoundError, Store, get_store
from app.storage.models import Alert, AuditEntry, Medicine, Member, Reading, Refill, utcnow
from app.storage.safe_text import NOTE_MAX, UnsafeTextError, check_note
from mcp_server.auth import SharedSecretVerifier
from mcp_server.guardrails import AMBER_MAX_PER_DAY, REFILL_MAX_DAYS
from mcp_server.http_guard import guard_middleware
from mcp_server.identity import resolve_parent, sender_from_context
from mcp_server.summary import build_summary_text, filter_summary

TZ = "Asia/Kolkata"
RANGE_DAYS = {"today": 1, "7d": 7, "14d": 14, "30d": 30}
HHMM = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
RETAKE = "ask the parent to retake the photo (or type the numbers)."


class ReadingValues(BaseModel):
    """Numbers read from the device screen. Use null for anything not shown."""

    model_config = ConfigDict(extra="forbid")

    systolic: int | None = None
    diastolic: int | None = None
    pulse: int | None = None
    glucose_value: float | None = None
    glucose_unit: Literal["mg/dL", "mmol/L"] | None = None
    display: Literal["HI", "LO"] | None = Field(
        default=None, description='Glucometer only: the screen shows "HI" or "LO", no number.'
    )


# --- output models (published as each tool's outputSchema) ---


class LogReadingOut(BaseModel):
    stored: bool
    reading_id: str
    status: Literal["unconfirmed"]
    read_back: str = Field(description="Send this to the parent and wait for 1 / 2.")
    unusual_vs_history: list[str]


class HistoryReading(BaseModel):
    id: str
    type: Literal["bp", "glucose"]
    time: str | None
    systolic: int | None = None
    diastolic: int | None = None
    pulse: int | None = None
    glucose_mgdl: float | None = None
    context: str | None = None
    source: str


class TypeStats(BaseModel):
    count: int
    latest: str | None = None
    latest_time: str | None = None
    lowest: str | None = None
    highest: str | None = None
    average: str | None = None


class HistoryOut(BaseModel):
    range: Literal["today", "7d", "14d", "30d"]
    response_format: Literal["concise", "detailed"]
    count: int
    bp: TypeStats
    glucose: TypeStats
    readings: list[HistoryReading] | None = Field(
        default=None, description="Only with response_format=detailed."
    )


class MedicineOut(BaseModel):
    id: str
    brand: str
    generic: str
    strength: str
    times: list[str]
    reminder_note: str | None
    stock_count: int
    days_left: int | None
    confirmed: bool
    is_schedule_x: bool


class MedsOut(BaseModel):
    medicines: list[MedicineOut]


class ReminderOut(BaseModel):
    med_id: str
    brand: str
    times: list[str]
    reminder_note: str | None
    timezone: str


class AlertOut(BaseModel):
    sent: bool
    level: Literal["amber", "red"] | None = None
    rule: str | None = None
    alert_id: str | None = None
    message: str | None = Field(default=None, description="The server-written family text.")
    notified_member_ids: list[str] = Field(default_factory=list)
    voice_call: bool = False
    ask_repeat: bool = False
    already_sent: bool = False
    suppressed: bool = False
    not_shared: bool = False
    why: str | None = None


class RefillOut(BaseModel):
    refill_id: str
    status: Literal["pending_approval"]
    brand: str
    qty: int
    note: str
    notified_member_ids: list[str]


class SummaryOut(BaseModel):
    text: str
    filtered: bool
    period_start: str | None
    period_end: str | None
    audio_url: str | None


def _schema(model: type[BaseModel]) -> dict[str, Any]:
    return model.model_json_schema()


def _result(out: BaseModel, summary: str) -> ToolResult:
    """Structured content (matches the tool's outputSchema) plus a short text summary."""
    return ToolResult(content=summary, structured_content=out.model_dump(mode="json"))


def _annotations(
    title: str, *, read_only: bool, idempotent: bool, open_world: bool = False
) -> ToolAnnotations:
    """Every hint set explicitly: the spec defaults (destructive, open-world) are wrong here."""
    return ToolAnnotations(
        title=title,
        read_only_hint=read_only,
        destructive_hint=False,
        idempotent_hint=idempotent,
        open_world_hint=open_world,
    )


def _local(ts: datetime | None) -> str | None:
    return ts.astimezone(ZoneInfo(TZ)).isoformat(timespec="minutes") if ts else None


def _reading_out(r: Reading) -> HistoryReading:
    out = HistoryReading(id=r.id, type=r.type, time=_local(r.created_at), source=r.source)
    if r.type == "bp":
        return out.model_copy(
            update={"systolic": r.systolic, "diastolic": r.diastolic, "pulse": r.pulse}
        )
    return out.model_copy(update={"glucose_mgdl": r.glucose_mgdl, "context": r.context})


def _stats(rows: list[Reading]) -> TypeStats:
    if not rows:
        return TypeStats(count=0)
    last = rows[-1]
    if last.type == "bp":
        key = [(r.systolic or 0, r.diastolic or 0) for r in rows]
        lo, hi = min(key), max(key)
        avg = f"{mean(k[0] for k in key):.0f}/{mean(k[1] for k in key):.0f}"
        return TypeStats(
            count=len(rows),
            latest=f"{last.systolic}/{last.diastolic}",
            latest_time=_local(last.created_at),
            lowest=f"{lo[0]}/{lo[1]}",
            highest=f"{hi[0]}/{hi[1]}",
            average=avg,
        )
    values = [r.glucose_mgdl for r in rows if r.glucose_mgdl is not None]
    return TypeStats(
        count=len(rows),
        latest=f"{last.glucose_mgdl:g} mg/dL",
        latest_time=_local(last.created_at),
        lowest=f"{min(values):g} mg/dL",
        highest=f"{max(values):g} mg/dL",
        average=f"{mean(values):.0f} mg/dL",
    )


def _med_out(m: Medicine) -> MedicineOut:
    return MedicineOut(
        id=m.id,
        brand=m.brand,
        generic=m.generic,
        strength=m.strength,
        times=m.times,
        reminder_note=m.reminder_note,
        stock_count=m.stock_count,
        days_left=m.stock_count // m.daily_dose if m.daily_dose else None,
        confirmed=m.confirmed,
        is_schedule_x=m.is_schedule_x,
    )


class AuditMiddleware(Middleware):
    """Writes every tool call, success or failure, to audit_log."""

    def __init__(self, store_of) -> None:
        self._store_of = store_of

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        store: Store = self._store_of()
        app_clock.sync_from(store)  # the API may have advanced the demo clock
        name = context.message.name
        args = context.message.arguments or {}
        parent_id = None
        sender = sender_from_context(context.fastmcp_context)
        if sender:
            member = store.find_member_by_channel(*sender)
            parent_id = member.id if member and member.role == "parent" else None

        def record(result: Any) -> None:
            store.add_audit(
                AuditEntry(
                    actor="agent",
                    tool=name,
                    args_json=json.dumps(args, default=str),
                    result_json=json.dumps(result, default=str),
                    parent_id=parent_id,
                )
            )

        try:
            result = await call_next(context)
        except Exception as exc:
            record({"error": str(exc)})
            raise
        record(result.structured_content)
        return result


def create_server(
    store: Store | None = None,
    notifier: Notifier | None = None,
    clock: Callable[[], datetime] = utcnow,
    secret: str | None = None,
) -> FastMCP:
    """Build the MCP server. Defaults: store from settings; family messages go to the
    store's outbox, from where the API delivers them. `secret` (default MCP_SHARED_SECRET):
    HTTP requests must send "Authorization: Bearer <secret>"; empty = no HTTP auth (only
    for in-process tests: main() refuses to serve without a secret)."""
    if secret is None:
        secret = get_settings().mcp_shared_secret

    def db() -> Store:
        return store or get_store()

    notifier = notifier or StoreNotifier(db)

    mcp = FastMCP(
        "khayal",
        # Inputs are validated strictly against each tool's schema ("10" is not quietly read
        # as 10). Unknown arguments, e.g. a recipient or a message body, are always refused.
        strict_input_validation=True,
        instructions=(
            "Khayal tools for one elderly parent (resolved from the sender). Log meter "
            "readings, read history and medicines, set reminders, alert family when a "
            "doctor-set limit is crossed, request refills for family approval. Never "
            "diagnose or give dosing advice."
        ),
        auth=SharedSecretVerifier(secret) if secret else None,
    )
    mcp.add_middleware(AuditMiddleware(db))

    def parent_of(ctx: Context) -> Member:
        return resolve_parent(ctx, db())

    def own_medicine(parent: Member, med_id: str) -> Medicine:
        try:
            med = db().get_medicine(med_id)
        except NotFoundError:
            med = None
        if med is None or med.parent_id != parent.id:
            raise ToolError(f"Medicine {med_id} not found for this parent.")
        return med

    def thresholds_for(parent: Member) -> dict[str, float]:
        return effective_thresholds(db().list_thresholds(parent.id))

    @mcp.tool(
        title="Log a meter reading (unconfirmed)",
        annotations=_annotations(
            "Log a meter reading (unconfirmed)", read_only=False, idempotent=False
        ),
        output_schema=_schema(LogReadingOut),
    )
    def log_reading(
        type: Literal["bp", "glucose"],
        values: ReadingValues,
        ctx: Context,
        confidence: Annotated[float | None, Field(ge=0, le=1)] = None,
        image_ref: Annotated[str | None, Field(max_length=64)] = None,
        context: Literal["fasting", "post_meal", "random"] | None = None,
        source: Literal["photo", "voice", "manual"] = "photo",
    ) -> ToolResult:
        """Store a meter reading as UNCONFIRMED and return the read-back question for the
        parent. It becomes confirmed only when the parent replies yes (handled by the
        server, not by a tool). Implausible values are refused with an error that says what
        to do (SYS 60-260, DIA 30-160, pulse 30-200, glucose 20-600 mg/dL, SYS > DIA;
        a glucometer showing HI / LO goes in values.display)."""
        parent = parent_of(ctx)
        reading, check = build_reading(
            db(),
            parent.id,
            type,
            systolic=values.systolic,
            diastolic=values.diastolic,
            pulse=values.pulse,
            glucose_value=values.glucose_value,
            glucose_unit=values.glucose_unit,
            display=values.display,
            context=context,
            source=source,
            confidence=confidence,
            image_ref=image_ref,
        )
        if reading is None:
            raise ToolError("; ".join(check.problems) + f"; {RETAKE} Nothing was stored.")
        db().add_reading(reading)
        question = read_back(format_value(reading))
        out = LogReadingOut(
            stored=True,
            reading_id=reading.id,
            status="unconfirmed",
            read_back=question,
            unusual_vs_history=check.flags,
        )
        return _result(out, f"Stored unconfirmed {reading.id}. Ask the parent: {question}")

    @mcp.tool(
        title="Read confirmed readings",
        annotations=_annotations("Read confirmed readings", read_only=True, idempotent=True),
        output_schema=_schema(HistoryOut),
    )
    def get_history(
        ctx: Context,
        range: Literal["today", "7d", "14d", "30d"] = "7d",
        type: Literal["bp", "glucose"] | None = None,
        response_format: Literal["concise", "detailed"] = "concise",
    ) -> ToolResult:
        """Confirmed readings for the parent over a time range (read-only). concise
        (default): counts, latest, lowest, highest and average per type. detailed: also
        every reading with its id and time."""
        parent = parent_of(ctx)
        since = clock() - timedelta(days=RANGE_DAYS[range])
        rows = db().list_readings(parent.id, since=since, type=type, status="confirmed")
        bp = _stats([r for r in rows if r.type == "bp"])
        glucose = _stats([r for r in rows if r.type == "glucose"])
        out = HistoryOut(
            range=range,
            response_format=response_format,
            count=len(rows),
            bp=bp,
            glucose=glucose,
            readings=[_reading_out(r) for r in rows] if response_format == "detailed" else None,
        )
        parts = [
            f"{st.count} {name}" + (f" (latest {st.latest}, avg {st.average})" if st.count else "")
            for name, st in (("BP", bp), ("glucose", glucose))
        ]
        return _result(out, f"Confirmed readings, {range}: " + "; ".join(parts) + ".")

    @mcp.tool(
        title="Read the medicine list",
        annotations=_annotations("Read the medicine list", read_only=True, idempotent=True),
        output_schema=_schema(MedsOut),
    )
    def get_meds(ctx: Context) -> ToolResult:
        """The parent's medicines with reminder times and stock (read-only)."""
        parent = parent_of(ctx)
        meds = [_med_out(m) for m in db().list_medicines(parent.id)]
        names = ", ".join(f"{m.brand} ({m.days_left} days left)" for m in meds) or "none"
        return _result(MedsOut(medicines=meds), f"{len(meds)} medicines: {names}.")

    @mcp.tool(
        title="Set daily reminder times",
        annotations=_annotations("Set daily reminder times", read_only=False, idempotent=True),
        output_schema=_schema(ReminderOut),
    )
    def create_reminder(
        med_id: str,
        times: Annotated[list[str], Field(min_length=1, max_length=6)],
        ctx: Context,
        note: Annotated[
            str | None,
            Field(max_length=NOTE_MAX, description='Optional short label, e.g. "after food".'),
        ] = None,
    ) -> ToolResult:
        """Set daily reminder times ("HH:MM", 24 h, Asia/Kolkata) for a confirmed medicine.
        Replaces the medicine's previous reminder times (same call twice = same result).
        `note` is a short label only: no links, phone numbers or instructions."""
        parent = parent_of(ctx)
        med = own_medicine(parent, med_id)
        if not med.confirmed:
            raise ToolError("Reminders are only set for medicines the parent has confirmed.")
        bad = [t for t in times if not HHMM.match(t)]
        if bad:
            raise ToolError(f"Times must be HH:MM in 24-hour format, got {bad}.")
        if note is not None:
            try:
                note = check_note(note, "Reminder note")
            except UnsafeTextError as exc:
                raise ToolError(f"{exc} Leave the note out or use a few plain words.") from exc
        clean = sorted(set(times))
        med = db().set_medicine_times(med.id, clean)
        if note is not None:
            med = db().set_medicine_note(med.id, note)
        out = ReminderOut(
            med_id=med.id,
            brand=med.brand,
            times=med.times,
            reminder_note=med.reminder_note,
            timezone=TZ,
        )
        return _result(out, f"Reminders for {med.brand} at {', '.join(med.times)} ({TZ}).")

    @mcp.tool(
        title="Alert the family (server-checked)",
        annotations=_annotations(
            "Alert the family (server-checked)", read_only=False, idempotent=True, open_world=True
        ),
        output_schema=_schema(AlertOut),
    )
    def alert_family(
        level: Literal["amber", "red"],
        ctx: Context,
        reading_id: str | None = None,
    ) -> ToolResult:
        """Alert the family when a limit the parent's doctor set is crossed. With reading_id
        the reading must be confirmed; without it, only the missed-doses rule can trigger.
        The server re-checks the limits and writes the message itself; recipients are the
        family members on record (no recipient or text is accepted). Red also rings family
        members when voice is enabled. Amber is rate-capped; red never is. Calling twice
        for the same reading and rule sends once."""
        parent = parent_of(ctx)
        s = db()
        t = thresholds_for(parent)
        whose = whose_doctor(s, parent.id)
        if reading_id:
            try:
                reading = s.get_reading(reading_id)
            except NotFoundError:
                reading = None
            if reading is None or reading.parent_id != parent.id:
                raise ToolError(f"Reading {reading_id} not found for this parent.")
            if reading.status != "confirmed":
                raise ToolError("No alert before the parent confirms the reading.")
            candidates = evaluate_for_alert(s, reading, clock())
        else:
            today = local_today(TZ, clock())
            day_start = datetime.combine(today, datetime.min.time(), ZoneInfo(TZ))
            doses = s.list_dose_events(parent.id, since=day_start, until=day_start + timedelta(1))
            candidates = [evaluate_missed_doses(doses, today, t, TZ, whose)]

        first = candidates[0]
        if first.level == "none" and first.ask_repeat:
            raise ToolError(f"Not sent: {first.reason}")
        if alert_level(first.level) is None:
            raise ToolError("No limit set by the doctor was crossed; alert not sent.")
        if LEVEL_RANK[level] > LEVEL_RANK[first.level]:
            raise ToolError(
                f"Requested {level} but the server evaluated {first.level}; not sent. "
                f"Call again with level={first.level}."
            )

        recent = s.list_alerts(parent.id, since=clock() - timedelta(days=1))
        if not reading_id:  # missed-doses rule: at most one alert per (IST) day
            today = local_today(TZ, clock())
            dup = next(
                (
                    a
                    for a in recent
                    if a.reading_id is None and local_today(TZ, a.created_at) == today
                ),
                None,
            )
            if dup:
                return _result(
                    AlertOut(sent=False, already_sent=True, alert_id=dup.id),
                    "Already sent today.",
                )
            final: Evaluation = first
        else:
            mine = [a for a in recent if a.reading_id == reading_id]

            def sent_before(e: Evaluation) -> Alert | None:
                # one alert per (reading, rule); rows from before rules were stored: by level
                legacy = [a for a in mine if a.rule is None and a.level == e.level]
                return next((a for a in mine if a.rule == e.rule), None) or next(iter(legacy), None)

            pending = [e for e in candidates if alert_level(e.level) and not sent_before(e)]
            if not pending:
                dup = sent_before(first)
                return _result(
                    AlertOut(sent=False, already_sent=True, alert_id=dup.id if dup else None),
                    "Already sent for this reading.",
                )
            final = pending[0]
        if LEVEL_RANK[level] > LEVEL_RANK[final.level]:
            raise ToolError(
                f"Requested {level} but the server evaluated {final.level}; not sent. "
                f"Call again with level={final.level}."
            )
        stored_level = alert_level(final.level)
        assert stored_level is not None  # a crossed limit was checked above

        # Parent-first (§6.5): the parent decides whether amber alerts are shared. Red alerts
        # (warning signs, a confirmed severe repeat BP, glucose below 54) always go out.
        if final.level != "red" and not s.get_sharing(parent.id).alerts:
            out = AlertOut(
                sent=False,
                not_shared=True,
                why="The parent has chosen not to share amber alerts with the family.",
            )
            return _result(out, "Not sent: the parent does not share amber alerts.")
        if final.level == "amber" and sum(a.level == "amber" for a in recent) >= AMBER_MAX_PER_DAY:
            out = AlertOut(sent=False, suppressed=True, why="amber rate cap reached for today")
            return _result(out, "Not sent: amber cap for today reached.")

        # Recipients: only the family members on record for this parent.
        family = s.list_members(parent.family_id, role="child")
        text = f"{final.level.upper()} alert for {parent.name}: {final.reason}"
        # A phone call only for a RED alert on a confirmed reading (never for amber / doses).
        call = final.level == "red" and bool(reading_id) and get_settings().call_provider != "none"
        try:
            alert = s.add_alert(
                Alert(
                    parent_id=parent.id,
                    level=stored_level,
                    reason=final.reason,
                    reading_id=reading_id,
                    rule=final.rule,
                    notified_member_ids=[m.id for m in family],
                    created_at=clock(),
                )
            )
        except ConflictError:  # the database's one-alert-per-(reading, rule) guard
            dup = next(
                (a for a in s.list_alerts(parent.id) if a.reading_id == reading_id
                 and a.rule == final.rule),
                None,
            )  # fmt: skip
            return _result(
                AlertOut(sent=False, already_sent=True, alert_id=dup.id if dup else None),
                "Already sent for this reading.",
            )
        # Written to the outbox with the alert id: delivery is tracked per alert, and red
        # messages are retried until a channel accepts them (app/channels/service.py).
        for member in family:
            notifier.send(member, text, alert_id=alert.id, level=final.level)
            if call:
                notifier.voice_call(member, text, alert_id=alert.id, level=final.level)
        out = AlertOut(
            sent=True,
            alert_id=alert.id,
            level=stored_level,
            rule=final.rule,
            message=text,
            notified_member_ids=alert.notified_member_ids,
            voice_call=call,
            ask_repeat=final.ask_repeat,
        )
        return _result(out, f"Sent {final.level} ({final.rule}) to {len(family)} family members.")

    @mcp.tool(
        title="Request a refill for family approval",
        annotations=_annotations(
            "Request a refill for family approval", read_only=False, idempotent=True
        ),
        output_schema=_schema(RefillOut),
    )
    def request_refill(med_id: str, qty: Annotated[int, Field(gt=0)], ctx: Context) -> ToolResult:
        """Create a refill request for family approval (status pending_approval). It is
        NOT an order: a family member must approve it on the dashboard, and there is no
        tool to approve. Quantity is capped at one month's supply; Schedule X medicines are
        excluded; one pending request per medicine (a second call is refused)."""
        parent = parent_of(ctx)
        med = own_medicine(parent, med_id)
        if med.is_schedule_x:
            raise ToolError("Schedule X medicines cannot be refilled through Khayal.")
        cap = med.daily_dose * REFILL_MAX_DAYS
        if qty > cap:
            raise ToolError(f"Quantity {qty} exceeds one month's supply ({cap}); use {cap}.")
        pending = db().list_refills(parent.id, status="pending_approval")
        if any(r.medicine_id == med.id for r in pending):
            raise ToolError("A refill for this medicine is already waiting for approval.")
        if not db().get_sharing(parent.id).medicines:
            raise ToolError(
                "The parent has not shared medicines with the family, so no one can approve "
                "a refill."
            )
        refill = db().add_refill(Refill(medicine_id=med.id, qty=qty, status="pending_approval"))
        family = db().list_members(parent.family_id, role="child")
        for member in family:
            notifier.send(
                member,
                f"Refill request for {parent.name}: {med.brand} x{qty}. "
                "Approve or reject it on the Khayal dashboard.",
            )
        out = RefillOut(
            refill_id=refill.id,
            status="pending_approval",
            brand=med.brand,
            qty=qty,
            note="Waiting for a family member to approve on the dashboard.",
            notified_member_ids=[m.id for m in family],
        )
        return _result(out, f"Refill {refill.id} for {med.brand} x{qty} is pending approval.")

    @mcp.tool(
        title="Weekly summary (descriptive)",
        annotations=_annotations("Weekly summary (descriptive)", read_only=True, idempotent=True),
        output_schema=_schema(SummaryOut),
    )
    def weekly_summary(ctx: Context) -> ToolResult:
        """Descriptive 7-day summary: counts, ranges and the home BP average against the
        limits the doctor set and the 135/85 home reference. No recipients or text are
        accepted; the server writes it and filters diagnosis / dosing words."""
        parent = parent_of(ctx)
        s = db()
        end = clock()
        start = end - timedelta(days=7)
        readings = s.list_readings(parent.id, since=start, status="confirmed")
        doses = s.list_dose_events(parent.id, since=start, until=end)
        alerts = s.list_alerts(parent.id, since=start)
        raw = build_summary_text(
            parent, readings, doses, alerts, thresholds_for(parent), start, end, TZ
        )
        text, hits = filter_summary(raw)
        out = SummaryOut(
            text=text,
            filtered=bool(hits),
            period_start=_local(start),
            period_end=_local(end),
            audio_url=None,  # the scheduler adds the voice note when it sends the summary
        )
        return _result(out, text)

    return mcp


mcp = create_server()


def main() -> None:
    """Run the server on MCP_HOST (default 127.0.0.1) and the port/path of MCP_SERVER_URL
    (default http://localhost:8001/mcp). Refuses to start without MCP_SHARED_SECRET
    (scripts/start.py makes one per run)."""
    from app.startup_checks import enforce

    settings = get_settings()
    enforce(settings, "mcp")
    if not settings.mcp_shared_secret:
        raise SystemExit(
            "MCP_SHARED_SECRET is empty: set it in .env (same value for the API), "
            "or start everything with scripts/start.py, which makes one per run."
        )
    url = urlparse(settings.mcp_server_url)
    mcp.run(
        transport="http",
        host=settings.mcp_host,
        port=url.port or 8001,
        path=url.path or "/mcp",
        # Host allowlist + no browser Origin (DNS rebinding), before auth and sessions.
        middleware=guard_middleware(settings.mcp_server_url, settings.mcp_allowed_hosts),
    )


if __name__ == "__main__":
    main()
