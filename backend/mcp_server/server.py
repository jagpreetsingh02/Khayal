"""Care Circle MCP server (brief §5.5).

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
from typing import Annotated, Any, Literal
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware, MiddlewareContext
from pydantic import BaseModel, Field

from app.alerts.evaluate import evaluate_for_alert
from app.alerts.notifier import Notifier, StoreNotifier
from app.alerts.thresholds import (
    LEVEL_RANK,
    Evaluation,
    effective_thresholds,
    evaluate_missed_doses,
    local_today,
)
from app.config import get_settings
from app.conversation.templates import RETAKE_TIPS, format_value, read_back
from app.reader.intake import build_reading
from app.storage import NotFoundError, Store, get_store
from app.storage.models import Alert, AuditEntry, Member, Reading, Refill, utcnow
from mcp_server.guardrails import AMBER_MAX_PER_DAY, REFILL_MAX_DAYS, blocked_terms
from mcp_server.identity import resolve_parent, sender_from_context
from mcp_server.summary import build_summary_text, filter_summary

TZ = "Asia/Kolkata"
RANGE_DAYS = {"today": 1, "7d": 7, "14d": 14, "30d": 30}
HHMM = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


class ReadingValues(BaseModel):
    """Numbers read from the device screen. Use null for anything not shown."""

    systolic: int | None = None
    diastolic: int | None = None
    pulse: int | None = None
    glucose_value: float | None = None
    glucose_unit: Literal["mg/dL", "mmol/L"] | None = None


def _local(ts: datetime | None) -> str | None:
    return ts.astimezone(ZoneInfo(TZ)).isoformat(timespec="minutes") if ts else None


def _reading_out(r: Reading) -> dict[str, Any]:
    out: dict[str, Any] = {"id": r.id, "type": r.type, "time": _local(r.created_at)}
    if r.type == "bp":
        out |= {"systolic": r.systolic, "diastolic": r.diastolic, "pulse": r.pulse}
    else:
        out |= {"glucose_mgdl": r.glucose_mgdl, "context": r.context}
    return out


class AuditMiddleware(Middleware):
    """Writes every tool call, success or failure, to audit_log."""

    def __init__(self, store_of) -> None:
        self._store_of = store_of

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        store: Store = self._store_of()
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
) -> FastMCP:
    """Build the MCP server. Defaults: store from settings; family messages go to the
    store's outbox, from where the API delivers them."""

    def db() -> Store:
        return store or get_store()

    notifier = notifier or StoreNotifier(db)

    mcp = FastMCP(
        "care-circle",
        instructions=(
            "Care Circle tools for one elderly parent (resolved from the sender). Log meter "
            "readings, read history and medicines, set reminders, alert family when a "
            "doctor-set limit is crossed, request refills for family approval. Never "
            "diagnose or give dosing advice."
        ),
    )
    mcp.add_middleware(AuditMiddleware(db))

    def parent_of(ctx: Context) -> Member:
        return resolve_parent(ctx, db())

    def own_medicine(parent: Member, med_id: str):
        try:
            med = db().get_medicine(med_id)
        except NotFoundError:
            med = None
        if med is None or med.parent_id != parent.id:
            raise ToolError(f"Medicine {med_id} not found for this parent.")
        return med

    def thresholds_for(parent: Member) -> dict[str, float]:
        return effective_thresholds(db().list_thresholds(parent.id))

    @mcp.tool
    def log_reading(
        type: Literal["bp", "glucose"],
        values: ReadingValues,
        ctx: Context,
        confidence: Annotated[float | None, Field(ge=0, le=1)] = None,
        image_ref: str | None = None,
        context: Literal["fasting", "post_meal", "random"] | None = None,
        source: Literal["photo", "voice", "manual"] = "photo",
    ) -> dict[str, Any]:
        """Store a meter reading as UNCONFIRMED and return the read-back question for the
        parent. It becomes confirmed only when the parent replies yes (handled by the
        server, not by a tool). Implausible values are not stored: ask for a retake."""
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
            context=context,
            source=source,
            confidence=confidence,
            image_ref=image_ref,
        )
        if reading is None:
            return {"stored": False, "problems": check.problems, "ask_retake": RETAKE_TIPS}
        db().add_reading(reading)
        return {
            "stored": True,
            "reading_id": reading.id,
            "status": reading.status,
            "read_back": read_back(format_value(reading)),
            "unusual_vs_history": check.flags,
        }

    @mcp.tool
    def get_history(
        ctx: Context,
        range: Literal["today", "7d", "14d", "30d"] = "7d",
        type: Literal["bp", "glucose"] | None = None,
    ) -> dict[str, Any]:
        """Confirmed readings for the parent over a time range (read-only)."""
        parent = parent_of(ctx)
        since = clock() - timedelta(days=RANGE_DAYS[range])
        rows = db().list_readings(parent.id, since=since, type=type, status="confirmed")
        return {"range": range, "count": len(rows), "readings": [_reading_out(r) for r in rows]}

    @mcp.tool
    def get_meds(ctx: Context) -> dict[str, Any]:
        """The parent's medicines with reminder times and stock (read-only)."""
        parent = parent_of(ctx)
        meds = db().list_medicines(parent.id)
        return {
            "medicines": [
                {
                    "id": m.id,
                    "brand": m.brand,
                    "generic": m.generic,
                    "strength": m.strength,
                    "times": m.times,
                    "stock_count": m.stock_count,
                    "days_left": m.stock_count // m.daily_dose if m.daily_dose else None,
                    "confirmed": m.confirmed,
                    "is_schedule_x": m.is_schedule_x,
                }
                for m in meds
            ]
        }

    @mcp.tool
    def create_reminder(
        med_id: str,
        times: Annotated[list[str], Field(min_length=1, max_length=6)],
        ctx: Context,
    ) -> dict[str, Any]:
        """Set daily reminder times ("HH:MM", 24 h, Asia/Kolkata) for a confirmed medicine.
        Replaces the medicine's previous reminder times."""
        parent = parent_of(ctx)
        med = own_medicine(parent, med_id)
        if not med.confirmed:
            raise ToolError("Reminders are only set for medicines the parent has confirmed.")
        bad = [t for t in times if not HHMM.match(t)]
        if bad:
            raise ToolError(f"Times must be HH:MM in 24-hour format, got {bad}.")
        clean = sorted(set(times))
        med = db().set_medicine_times(med.id, clean)
        return {"med_id": med.id, "brand": med.brand, "times": med.times, "timezone": TZ}

    @mcp.tool
    def alert_family(
        level: Literal["amber", "red"],
        reason: str,
        ctx: Context,
        reading_id: str | None = None,
    ) -> dict[str, Any]:
        """Alert the family when a doctor-set limit is crossed. With reading_id the reading
        must be confirmed; without it, only the missed-doses rule can trigger. The server
        re-checks the limits; families receive server-written text, not `reason`. Red also
        rings family members when voice is enabled. Amber is rate-capped; red never is."""
        parent = parent_of(ctx)
        s = db()
        if blocked_terms(reason):
            raise ToolError("Reason must describe numbers only, without diagnosis or dosing.")

        t = thresholds_for(parent)
        if reading_id:
            try:
                reading = s.get_reading(reading_id)
            except NotFoundError:
                reading = None
            if reading is None or reading.parent_id != parent.id:
                raise ToolError(f"Reading {reading_id} not found for this parent.")
            if reading.status != "confirmed":
                raise ToolError("No alert before the parent confirms the reading.")
            evaluation = evaluate_for_alert(s, reading, clock())
        else:
            today = local_today(TZ)
            day_start = datetime.combine(today, datetime.min.time(), ZoneInfo(TZ))
            doses = s.list_dose_events(parent.id, since=day_start, until=day_start + timedelta(1))
            evaluation = evaluate_missed_doses(doses, today, t, TZ)

        if evaluation.level == "none" and evaluation.ask_repeat:
            raise ToolError(f"Not sent: {evaluation.reason}")
        if evaluation.level == "none":
            raise ToolError("No limit set by the doctor was crossed; alert not sent.")
        if LEVEL_RANK[level] > LEVEL_RANK[evaluation.level]:
            raise ToolError(
                f"Requested {level} but the server evaluated {evaluation.level}; not sent."
            )
        final: Evaluation = evaluation  # a red evaluation is sent as red even if amber asked

        # Parent-first (§6.5): the parent decides whether amber alerts are shared. Red alerts
        # (a confirmed severe repeat BP, glucose below the red limit) always reach the family.
        if final.level != "red" and not s.get_sharing(parent.id).alerts:
            return {
                "sent": False,
                "not_shared": True,
                "why": "The parent has chosen not to share amber alerts with the family.",
            }

        recent = s.list_alerts(parent.id, since=clock() - timedelta(days=1))
        if reading_id:
            dup = next(
                (a for a in recent if a.reading_id == reading_id and a.level == final.level),
                None,
            )
            if dup:
                return {"sent": False, "already_sent": True, "alert_id": dup.id}
        if final.level == "amber" and sum(a.level == "amber" for a in recent) >= AMBER_MAX_PER_DAY:
            return {"sent": False, "suppressed": True, "why": "amber rate cap reached for today"}

        family = s.list_members(parent.family_id, role="child")
        text = f"{final.level.upper()} alert for {parent.name}: {final.reason}"
        voice = final.level == "red" and get_settings().voice != "none"
        for member in family:
            notifier.send(member, text)
            if voice:
                notifier.voice_call(member, text)
        alert = s.add_alert(
            Alert(
                parent_id=parent.id,
                level=final.level,
                reason=final.reason,
                reading_id=reading_id,
                notified_member_ids=[m.id for m in family],
                created_at=clock(),
            )
        )
        return {
            "sent": True,
            "alert_id": alert.id,
            "level": final.level,
            "rule": final.rule,
            "message": text,
            "notified_member_ids": alert.notified_member_ids,
            "voice_call": voice,
            "ask_repeat": final.ask_repeat,
        }

    @mcp.tool
    def request_refill(
        med_id: str, qty: Annotated[int, Field(gt=0)], ctx: Context
    ) -> dict[str, Any]:
        """Create a refill request for family approval (status pending_approval). It is
        NOT an order: a family member must approve it on the dashboard. Quantity is capped
        at one month's supply; Schedule X medicines are excluded."""
        parent = parent_of(ctx)
        med = own_medicine(parent, med_id)
        if med.is_schedule_x:
            raise ToolError("Schedule X medicines cannot be refilled through Care Circle.")
        cap = med.daily_dose * REFILL_MAX_DAYS
        if qty > cap:
            raise ToolError(f"Quantity {qty} exceeds one month's supply ({cap}).")
        pending = db().list_refills(parent.id, status="pending_approval")
        if any(r.medicine_id == med.id for r in pending):
            raise ToolError("A refill for this medicine is already waiting for approval.")
        if not db().get_sharing(parent.id).medicines:
            raise ToolError(
                "The parent has not shared medicines with the family, so no one can approve "
                "a refill."
            )
        refill = db().add_refill(Refill(medicine_id=med.id, qty=qty))
        family = db().list_members(parent.family_id, role="child")
        for member in family:
            notifier.send(
                member,
                f"Refill request for {parent.name}: {med.brand} x{qty}. "
                "Approve or reject it on the Care Circle dashboard.",
            )
        return {
            "refill_id": refill.id,
            "status": refill.status,
            "brand": med.brand,
            "qty": qty,
            "note": "Waiting for a family member to approve on the dashboard.",
            "notified_member_ids": [m.id for m in family],
        }

    @mcp.tool
    def weekly_summary(ctx: Context) -> dict[str, Any]:
        """Descriptive 7-day summary: counts and ranges against doctor-set limits only."""
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
        return {
            "text": text,
            "filtered": bool(hits),
            "period_start": _local(start),
            "period_end": _local(end),
            "audio_url": None,  # voice summary arrives in Phase 5
        }

    return mcp


mcp = create_server()


def main() -> None:
    """Run the server on MCP_SERVER_URL (default http://localhost:8001/mcp), localhost only."""
    url = urlparse(get_settings().mcp_server_url)
    mcp.run(transport="http", host="127.0.0.1", port=url.port or 8001, path=url.path or "/mcp")


if __name__ == "__main__":
    main()
