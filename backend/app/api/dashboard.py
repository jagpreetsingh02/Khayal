"""Family dashboard API (brief §5.6). Every route needs a signed-in family member.

Rules enforced here:
- Parent-first sharing (§6.5): a child sees only the categories the parent shared
  (readings / alerts / medicines); only the parent changes them. Exception: red alerts always
  reach the family, so children see red alerts even when alerts are not shared. Nothing is
  shared while consent is pending or revoked.
- Refill approval exists ONLY here (POST /api/refills/{id}/approve|reject), after a family
  member's click; the AI has no approve tool. Deciding twice is safe (idempotent).
- Limits are edited as "set by your doctor" and validated; every family action is written
  to audit_log with actor=member, next to the AI's MCP tool calls.
"""

import json
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field

from app.alerts.thresholds import (
    DEFAULT_THRESHOLDS,
    THRESHOLD_SPECS,
    effective_thresholds,
    validate_threshold_update,
)
from app.api.auth import (
    check_login_allowed,
    client_ip,
    current_member,
    demo_login_enabled,
    demo_token_ok,
    end_sessions,
    limits,
    new_session,
    record_failed_login,
    too_many,
)
from app.api.login_codes import find_member, issue_code, verify_code
from app.channels.service import KhayalService
from app.config import get_settings
from app.conversation import templates as tpl
from app.demo import PARENT_REF, ensure_demo_family
from app.pharmacy import DEMO_LABEL, DemoPharmacy
from app.storage import ConflictError, NotFoundError, Store
from app.storage.models import (
    SHARE_CATEGORIES,
    AuditEntry,
    Member,
    OutboxMessage,
    Reading,
    Refill,
    RefillStatus,
    ShareCategory,
    Sharing,
    utcnow,
)
from app.storage.safe_text import PLAN_MAX, UnsafeTextError
from app.voice import phrases

TZ = ZoneInfo("Asia/Kolkata")
RANGE_DAYS = {"7d": 7, "14d": 14, "30d": 30, "90d": 90}
APPROVED_STATES = ("approved", "sent_to_pharmacy", "fulfilled")
# Which sharing category an audit entry belongs to (children see details only if shared).
TOOL_CATEGORY: dict[str, ShareCategory] = {
    "log_reading": "readings",
    "get_history": "readings",
    "weekly_summary": "readings",
    "alert_family": "alerts",
    "get_meds": "medicines",
    "create_reminder": "medicines",
    "request_refill": "medicines",
    "dashboard:acknowledge_alert": "alerts",
    "dashboard:update_thresholds": "readings",
    "dashboard:update_care_plan": "readings",
    "conversation:symptom_check": "readings",
    "dashboard:approve_refill": "medicines",
    "dashboard:reject_refill": "medicines",
    "pharmacy:demo_order": "medicines",
    "demo:simulate_low_stock": "medicines",
}

public = APIRouter(prefix="/api", tags=["dashboard"])
router = APIRouter(prefix="/api", tags=["dashboard"], dependencies=[Depends(current_member)])
pharmacy = DemoPharmacy()
Me = Annotated[Member, Depends(current_member)]  # the signed-in family member


# --- helpers ---


def _svc(request: Request) -> KhayalService:
    return request.app.state.service


def _store(request: Request) -> Store:
    return _svc(request).store


def _unix_iso(seconds: int) -> str:
    """Session times are real wall-clock unix seconds (not the demo clock)."""
    return datetime.fromtimestamp(seconds, UTC).isoformat()


def _iso(ts: datetime | None) -> str | None:
    return ts.isoformat() if ts else None


def _name(store: Store, member_id: str | None) -> str | None:
    if not member_id:
        return None
    try:
        return store.get_member(member_id).name
    except NotFoundError:
        return None


def _parent(store: Store, me: Member, parent_id: str) -> Member:
    """The parent `parent_id`, if they are in the signed-in member's family."""
    try:
        parent = store.get_member(parent_id)
    except NotFoundError:
        parent = None
    if parent is None or parent.role != "parent" or parent.family_id != me.family_id:
        raise HTTPException(404, "Parent not found in your family.")
    return parent


def _consented(me: Member, parent: Member) -> bool:
    """Nothing is shared before consent, or after it is withdrawn (the parent sees all)."""
    return me.id == parent.id or parent.consent_status in ("granted", "paused")


def _can_see(store: Store, me: Member, parent: Member, category: ShareCategory) -> bool:
    if me.id == parent.id:
        return True  # the parent always sees their own data
    if not _consented(me, parent):
        return False
    return store.get_sharing(parent.id).allows(category)


def _alert_visible(store: Store, me: Member, parent: Member, alert) -> bool:
    """Red alerts always reach the family; amber only if the parent shares alerts."""
    if alert.level == "red":
        return _consented(me, parent)
    return _can_see(store, me, parent, "alerts")


def _require(store: Store, me: Member, parent: Member, category: ShareCategory) -> None:
    if not _can_see(store, me, parent, category):
        raise HTTPException(403, f"{parent.name} has not shared {category} with the family.")


def _visible_parents(store: Store, me: Member, category: ShareCategory) -> list[Member]:
    parents = store.list_members(me.family_id, role="parent")
    return [p for p in parents if _can_see(store, me, p, category)]


def _audit(store: Store, actor: str, tool: str, parent_id: str, args: dict, result: dict) -> None:
    store.add_audit(
        AuditEntry(
            actor=actor,  # type: ignore[arg-type]
            tool=tool,
            args_json=json.dumps(args, default=str),
            result_json=json.dumps(result, default=str),
            parent_id=parent_id,
        )
    )


def _reading(r: Reading) -> dict[str, Any]:
    return {
        "id": r.id,
        "type": r.type,
        "at": _iso(r.created_at),
        "systolic": r.systolic,
        "diastolic": r.diastolic,
        "pulse": r.pulse,
        "glucose_mgdl": r.glucose_mgdl,
        "context": r.context,
        "source": r.source,
    }


def _delivery(store: Store, alert_id: str) -> str | None:
    """Did the alert's text messages reach the family? "delivered" (all), "pending" (some
    still being retried), "failed" (some given up) or None (no tracked messages: older
    alerts)."""
    rows = [m for m in store.list_outbox(alert_id=alert_id) if m.kind == "message"]
    if not rows:
        return None
    if all(m.delivered_at for m in rows):
        return "delivered"
    if any(m.delivered_at is None and m.gave_up_at is None for m in rows):
        return "pending"
    return "failed"


def _alert(store: Store, a) -> dict[str, Any]:
    return {
        "id": a.id,
        "parent_id": a.parent_id,
        "level": a.level,
        "reason": a.reason,
        "reading_id": a.reading_id,
        "at": _iso(a.created_at),
        "acknowledged_by": _name(store, a.acknowledged_by),
        "acknowledged_at": _iso(a.acknowledged_at),
        "delivery": _delivery(store, a.id),
    }


def _refill(store: Store, r: Refill) -> dict[str, Any]:
    med = store.get_medicine(r.medicine_id)
    return {
        "id": r.id,
        "parent_id": med.parent_id,
        "medicine": {
            "id": med.id,
            "brand": med.brand,
            "generic": med.generic,
            "strength": med.strength,
        },
        "qty": r.qty,
        "status": r.status,
        "requested_by": r.requested_by,
        "requested_at": _iso(r.created_at),
        "decided_by": _name(store, r.decided_by_member_id),
        "decided_at": _iso(r.decided_at),
        "pharmacy": DEMO_LABEL if r.pharmacy_ref else None,
        "pharmacy_ref": r.pharmacy_ref,
    }


def _sharing(store: Store, me: Member, parent: Member) -> dict[str, Any]:
    s = store.get_sharing(parent.id)
    return {
        "parent_id": parent.id,
        "categories": {c: s.allows(c) for c in SHARE_CATEGORIES},
        "visible_to_me": {c: _can_see(store, me, parent, c) for c in SHARE_CATEGORIES},
        "updated_by": _name(store, s.updated_by),
        "updated_at": _iso(s.updated_at),
        "editable": me.id == parent.id,
        "consent": {
            "status": parent.consent_status,
            "at": _iso(parent.consent_at),
            "text_version": parent.consent_text_version,
        },
    }


def _family_message(store: Store, member: Member, text: str, voice=None) -> None:
    store.add_outbox(
        OutboxMessage(
            member_id=member.id,
            text=text,
            voice_text=voice.text if voice else None,
            voice_lang=voice.lang if voice else None,
        )
    )


# --- login (no session needed) ---


class TokenBody(BaseModel):
    token: str = Field(min_length=1, max_length=200)


class LoginBody(TokenBody):
    member_id: str = Field(min_length=1, max_length=100)


class CodeRequestBody(BaseModel):
    member_id: str = Field(min_length=1, max_length=100)


class CodeVerifyBody(CodeRequestBody):
    code: str = Field(min_length=1, max_length=20)


CODE_SENT = {
    "ok": True,
    "message": "If this member exists, a sign-in code is on its way to their chat. "
    "It expires in 5 minutes.",
}


def _family_parent_id(store: Store, member: Member) -> str | None:
    """Which parent's audit log a sign-in event belongs to (shown to that family)."""
    if member.role == "parent":
        return member.id
    parents = store.list_members(member.family_id, role="parent")
    return parents[0].id if len(parents) == 1 else None


def _audit_login(
    store: Store, tool: str, member: Member | None, member_id: str, method: str, ok: bool
) -> None:
    """Sign-in events in audit_log. Never codes or tokens; actor=unknown until proven."""
    _audit(
        store,
        "member" if ok else "unknown",
        tool,
        _family_parent_id(store, member) if member else None,  # type: ignore[arg-type]
        {"member_id": member_id[:100], "method": method},
        {"ok": ok},
    )


def _signed_in(request: Request, member: Member, method: str) -> dict[str, Any]:
    store = _store(request)
    _audit_login(store, "dashboard:login", member, member.id, method, True)
    return new_session(store, member)


@public.get("/login/hint")
def login_hint(request: Request) -> dict[str, Any]:
    """What the login page offers: code sign-in always; the demo token only in mock mode.
    With the public default token (mock demo) it also lists the demo family, so the code
    path can be tried by tapping a name: that token already shows the same list."""
    s = get_settings()
    demo = demo_login_enabled()
    default_token = demo and s.dashboard_demo_token == "change-me"
    members: list[dict[str, str]] = []
    parent = _store(request).find_member_by_channel("simulator", PARENT_REF)
    if default_token and parent is not None:
        members = _member_choices(_store(request), parent.family_id)
    return {
        "mode": s.app_mode,
        "demo_login": demo,
        "code_login": True,
        "default_token": default_token,
        "demo_members": members,
    }


def _member_choices(store: Store, family_id: str) -> list[dict[str, str]]:
    order = {"child": 0, "parent": 1}
    members = store.list_members(family_id)
    return [
        {"id": m.id, "name": m.name, "role": m.role}
        for m in sorted(members, key=lambda m: (order[m.role], m.name))
    ]


def _demo_login_or_403() -> None:
    if not demo_login_enabled():
        raise HTTPException(403, "The demo login is off here. Sign in with a one-time code.")


@public.post("/login/members")
def login_members(body: TokenBody, request: Request) -> dict[str, Any]:
    _demo_login_or_403()
    check_login_allowed(request)
    if not demo_token_ok(body.token):
        record_failed_login(request)
        raise HTTPException(401, "Wrong demo token.")
    store = _store(request)
    parent = ensure_demo_family(store)
    return {
        "family": store.get_family(parent.family_id).name,
        "members": _member_choices(store, parent.family_id),
    }


@public.post("/login")
def login(body: LoginBody, request: Request) -> dict[str, Any]:
    _demo_login_or_403()
    check_login_allowed(request)
    store = _store(request)
    member = find_member(store, body.member_id)
    if not demo_token_ok(body.token) or member is None:
        record_failed_login(request)
        _audit_login(store, "dashboard:login_failed", member, body.member_id, "demo_token", False)
        if member is None and demo_token_ok(body.token):
            raise HTTPException(404, "No such family member.")
        raise HTTPException(401, "Wrong demo token.")
    return _signed_in(request, member, "demo_token")


@public.post("/login/code/request")
def request_login_code(
    body: CodeRequestBody, request: Request, background: BackgroundTasks
) -> dict[str, Any]:
    """Send a one-time code to the member's own chat. Same answer whether or not the member
    exists (no member discovery); limited per member id and per IP."""
    for limiter, key in (
        (limits(request).code_ip, client_ip(request)),
        (limits(request).code_member, body.member_id),
    ):
        wait = limiter.hit(key)
        if wait is not None:
            raise too_many(wait)
    store = _store(request)
    member = find_member(store, body.member_id)
    if member is not None:
        issue_code(store, member)
        background.add_task(_svc(request).flush_outbox)  # send now, after the response
    _audit(
        store,
        "unknown",
        "dashboard:login_code_requested",
        _family_parent_id(store, member) if member else None,  # type: ignore[arg-type]
        {"member_id": body.member_id[:100]},
        {"ok": True},
    )
    return CODE_SENT


@public.post("/login/code/verify")
def verify_login_code(body: CodeVerifyBody, request: Request) -> dict[str, Any]:
    check_login_allowed(request)
    store = _store(request)
    member = verify_code(store, body.member_id, body.code.strip())
    if member is None:
        record_failed_login(request)
        known = find_member(store, body.member_id)
        _audit_login(store, "dashboard:login_failed", known, body.member_id, "code", False)
        raise HTTPException(401, "Wrong or expired code. Ask for a new one if needed.")
    return _signed_in(request, member, "code")


@router.post("/logout")
def logout(request: Request, me: Me) -> dict[str, Any]:
    """Ends every session of this member (all devices); the client drops its token too."""
    store = _store(request)
    end_sessions(store, me.id)
    _audit(
        store,
        "member",
        "dashboard:logout",
        _family_parent_id(store, me),  # type: ignore[arg-type]
        {"member_id": me.id},
        {"ok": True},
    )
    return {"ok": True}


# --- signed-in routes ---


@router.get("/me")
def get_me(request: Request, me: Me) -> dict[str, Any]:
    store = _store(request)
    parents = store.list_members(me.family_id, role="parent")
    return {
        "member": {"id": me.id, "name": me.name, "role": me.role},
        "family": store.get_family(me.family_id).name,
        "parents": [
            {"id": p.id, "name": p.name, "sharing": _sharing(store, me, p)} for p in parents
        ],
        "app_mode": get_settings().app_mode,
        "timezone": "Asia/Kolkata",
        "session": {
            "issued_at": _unix_iso(request.state.session["iat"]),
            "expires_at": _unix_iso(request.state.session["exp"]),
        },
    }


@router.get("/parents/{parent_id}/overview")
def overview(parent_id: str, request: Request, me: Me):
    store = _store(request)
    parent = _parent(store, me, parent_id)
    out: dict[str, Any] = {"parent": {"id": parent.id, "name": parent.name}}
    if _can_see(store, me, parent, "readings"):
        latest = {}
        for kind in ("bp", "glucose"):
            rows = store.list_readings(parent.id, type=kind, status="confirmed")
            latest[kind] = _reading(rows[-1]) if rows else None
        out["latest"] = latest
        out["limits"] = effective_thresholds(store.list_thresholds(parent.id))
    if _can_see(store, me, parent, "medicines"):
        out["today_doses"] = _today_doses(store, parent)
        out["pending_refills"] = [
            _refill(store, r) for r in store.list_refills(parent.id, status="pending_approval")
        ]
    since = utcnow() - timedelta(days=7)
    alerts = [
        a
        for a in reversed(store.list_alerts(parent.id, since=since))
        if not a.acknowledged_at and _alert_visible(store, me, parent, a)
    ]
    out["active_alerts"] = [_alert(store, a) for a in alerts]
    out["alerts_shared"] = _can_see(store, me, parent, "alerts")  # False: red alerts only
    return out


@router.get("/parents/{parent_id}/readings")
def readings(
    parent_id: str, request: Request, me: Me, range: Literal["7d", "14d", "30d", "90d"] = "14d"
) -> dict[str, Any]:
    store = _store(request)
    parent = _parent(store, me, parent_id)
    _require(store, me, parent, "readings")
    since = utcnow() - timedelta(days=RANGE_DAYS[range])
    rows = store.list_readings(parent.id, since=since, status="confirmed")
    return {
        "range": range,
        "now": _iso(utcnow()),
        "readings": [_reading(r) for r in rows],
        "limits": effective_thresholds(store.list_thresholds(parent.id)),
    }


@router.get("/alerts")
def alerts(
    request: Request, me: Me, parent_id: str | None = None, days: int = 30
) -> dict[str, Any]:
    store = _store(request)
    if parent_id:
        parents = [_parent(store, me, parent_id)]
    else:
        parents = store.list_members(me.family_id, role="parent")
    since = utcnow() - timedelta(days=max(1, min(days, 365)))
    rows = [
        a
        for p in parents
        for a in store.list_alerts(p.id, since=since)
        if _alert_visible(store, me, p, a)  # red always; amber only if shared
    ]
    rows.sort(key=lambda a: a.created_at, reverse=True)
    return {"alerts": [_alert(store, a) for a in rows]}


@router.post("/alerts/{alert_id}/acknowledge")
def acknowledge(alert_id: str, request: Request, me: Me):
    store = _store(request)
    try:
        alert = store.get_alert(alert_id)
    except NotFoundError as exc:
        raise HTTPException(404, "Alert not found.") from exc
    parent = _parent(store, me, alert.parent_id)
    if not _alert_visible(store, me, parent, alert):
        raise HTTPException(403, f"{parent.name} has not shared alerts with the family.")
    changed = alert.acknowledged_at is None
    alert = store.acknowledge_alert(alert_id, me.id)
    changed = changed and alert.acknowledged_by == me.id
    if changed:
        _audit(
            store,
            "member",
            "dashboard:acknowledge_alert",
            parent.id,
            {"alert_id": alert_id, "by": me.name},
            {"level": alert.level},
        )
    return {"changed": changed, "alert": _alert(store, alert)}


@router.get("/refills")
def refills(
    request: Request, me: Me, status: RefillStatus | None = None, parent_id: str | None = None
) -> dict[str, Any]:
    store = _store(request)
    if parent_id:
        parent = _parent(store, me, parent_id)
        _require(store, me, parent, "medicines")
        parents = [parent]
    else:
        parents = _visible_parents(store, me, "medicines")
    rows = [r for p in parents for r in store.list_refills(p.id, status=status)]
    rows.sort(key=lambda r: r.created_at, reverse=True)
    return {"refills": [_refill(store, r) for r in rows]}


def _load_refill(store: Store, me: Member, refill_id: str) -> tuple[Refill, Member]:
    try:
        refill = store.get_refill(refill_id)
        med = store.get_medicine(refill.medicine_id)
    except NotFoundError as exc:
        raise HTTPException(404, "Refill not found.") from exc
    parent = _parent(store, me, med.parent_id)
    _require(store, me, parent, "medicines")
    return refill, parent


def _decide(request: Request, me: Member, refill_id: str, approve: bool) -> dict[str, Any]:
    """Approve or reject once. Repeating the same decision returns it unchanged; the
    opposite decision after one was made is a 409."""
    store = _store(request)
    refill, parent = _load_refill(store, me, refill_id)
    same = APPROVED_STATES if approve else ("rejected",)
    target: RefillStatus = "approved" if approve else "rejected"
    if refill.status != "pending_approval":
        if refill.status in same:
            return {"changed": False, "refill": _refill(store, refill)}
        raise HTTPException(409, f"This refill was already {refill.status.replace('_', ' ')}.")
    try:
        refill = store.decide_refill(refill.id, target, me.id)
    except ConflictError as exc:  # another click won the race
        refill = store.get_refill(refill_id)
        if refill.status in same:
            return {"changed": False, "refill": _refill(store, refill)}
        raise HTTPException(409, f"This refill was already {refill.status}.") from exc

    med = store.get_medicine(refill.medicine_id)
    verb = "approve" if approve else "reject"
    _audit(
        store,
        "member",
        f"dashboard:{verb}_refill",
        parent.id,
        {"refill_id": refill.id, "by": me.name, "medicine": med.brand, "qty": refill.qty},
        {"status": refill.status},
    )
    if approve:
        ref = pharmacy.submit(refill, med)
        refill = store.set_refill_pharmacy(refill.id, "sent_to_pharmacy", ref)
        _audit(
            store,
            "system",
            "pharmacy:demo_order",
            parent.id,
            {"refill_id": refill.id, "pharmacy": DEMO_LABEL},
            {"status": refill.status, "pharmacy_ref": ref, "demo": True},
        )
        _family_message(
            store,
            parent,
            tpl.REFILL_APPROVED.format(brand=med.brand, qty=refill.qty),
            phrases.say("refill_approved", parent.language, brand=med.brand),
        )
        others = (
            f"{me.name} approved the refill for {med.brand} x{refill.qty}. Sent to the "
            "demo partner pharmacy (demo, no real order)."
        )
    else:
        _family_message(store, parent, tpl.REFILL_DECLINED.format(brand=med.brand))
        others = f"{me.name} did not approve the refill for {med.brand}."
    for child in store.list_members(parent.family_id, role="child"):
        if child.id != me.id:
            _family_message(store, child, others)
    _svc(request).flush_outbox()
    return {"changed": True, "refill": _refill(store, refill)}


@router.post("/refills/{refill_id}/approve")
def approve_refill(refill_id: str, request: Request, me: Me):
    """The ONLY way a refill gets approved: a family member's click on the dashboard."""
    return _decide(request, me, refill_id, approve=True)


@router.post("/refills/{refill_id}/reject")
def reject_refill(refill_id: str, request: Request, me: Me):
    return _decide(request, me, refill_id, approve=False)


def _today_doses(store: Store, parent: Member) -> list[dict[str, Any]]:
    """Today's schedule (Asia/Kolkata) from reminder times, with any recorded responses."""
    now = utcnow().astimezone(TZ)
    start = datetime.combine(now.date(), datetime.min.time(), TZ)
    events = store.list_dose_events(parent.id, since=start, until=start + timedelta(days=1))
    out = []
    for med in store.list_medicines(parent.id):
        if not med.confirmed:
            continue
        for hhmm in med.times:
            h, m = map(int, hhmm.split(":"))
            due = start.replace(hour=h, minute=m)
            event = next(
                (
                    e
                    for e in events
                    if e.medicine_id == med.id and abs((e.due_at - due).total_seconds()) < 3600
                ),
                None,
            )
            status = event.status if event else ("upcoming" if due > now else "no_reply")
            out.append({"medicine": med.brand, "time": hhmm, "due_at": _iso(due), "status": status})
    return sorted(out, key=lambda d: d["time"])


@router.get("/parents/{parent_id}/medicines")
def medicines(parent_id: str, request: Request, me: Me):
    store = _store(request)
    parent = _parent(store, me, parent_id)
    _require(store, me, parent, "medicines")
    week = store.list_dose_events(parent.id, since=utcnow() - timedelta(days=7), until=utcnow())
    meds = []
    for m in store.list_medicines(parent.id):
        mine = [e for e in week if e.medicine_id == m.id]
        meds.append(
            {
                "id": m.id,
                "brand": m.brand,
                "generic": m.generic,
                "strength": m.strength,
                "times": m.times,
                "stock_count": m.stock_count,
                "daily_dose": m.daily_dose,
                "days_left": m.stock_count // m.daily_dose if m.daily_dose else None,
                "confirmed": m.confirmed,
                "is_schedule_x": m.is_schedule_x,
                "week": {
                    "taken": sum(e.status == "taken" for e in mine),
                    "missed": sum(e.status == "missed" for e in mine),
                },
            }
        )
    return {"medicines": meds, "today": _today_doses(store, parent), "timezone": "Asia/Kolkata"}


@router.get("/parents/{parent_id}/thresholds")
def get_thresholds(parent_id: str, request: Request, me: Me):
    store = _store(request)
    parent = _parent(store, me, parent_id)
    _require(store, me, parent, "readings")
    rows = {t.rule: t for t in store.list_thresholds(parent.id)}
    values = effective_thresholds(list(rows.values()))
    return {
        "label": "Set by your doctor",
        "thresholds": [
            spec.model_dump()
            | {
                "value": values[rule],
                "default": DEFAULT_THRESHOLDS[rule],
                "set_by": rows[rule].set_by if rule in rows else "default",
            }
            for rule, spec in THRESHOLD_SPECS.items()
        ],
    }


class ThresholdUpdate(BaseModel):
    values: dict[str, float] = Field(min_length=1)
    # The family enters what the doctor set; they must say so (limits are not theirs to pick).
    doctor_confirmed: Literal[True]


@router.put("/parents/{parent_id}/thresholds")
def put_thresholds(parent_id: str, body: ThresholdUpdate, request: Request, me: Me):
    store = _store(request)
    parent = _parent(store, me, parent_id)
    _require(store, me, parent, "readings")
    current = effective_thresholds(store.list_thresholds(parent.id))
    problems = validate_threshold_update(current, body.values)
    if problems:
        raise HTTPException(422, problems)
    changed = {k: v for k, v in body.values.items() if current[k] != v}
    for rule, value in changed.items():
        store.set_threshold(parent.id, rule, value, set_by="doctor")
    if changed:
        _audit(
            store,
            "member",
            "dashboard:update_thresholds",
            parent.id,
            {"by": me.name, "values": changed, "doctor_confirmed": True},
            {"saved": sorted(changed)},
        )
    return get_thresholds(parent_id, request, me)


@router.get("/parents/{parent_id}/care-plan")
def get_care_plan(parent_id: str, request: Request, me: Me):
    """The doctor's plan for low sugar: sent to the parent word for word after a low reading
    (Khayal never gives its own advice)."""
    store = _store(request)
    parent = _parent(store, me, parent_id)
    _require(store, me, parent, "readings")
    return {
        "label": "Set by your doctor",
        "low_sugar_plan": store.get_low_sugar_plan(parent.id),
        "max_length": PLAN_MAX,
        "fallback": tpl.NO_PLAN,
    }


class CarePlanUpdate(BaseModel):
    low_sugar_plan: str | None = Field(default=None, max_length=PLAN_MAX)
    doctor_confirmed: Literal[True]  # the family copies the doctor's words, as for limits


@router.put("/parents/{parent_id}/care-plan")
def put_care_plan(parent_id: str, body: CarePlanUpdate, request: Request, me: Me):
    store = _store(request)
    parent = _parent(store, me, parent_id)
    _require(store, me, parent, "readings")
    try:  # checked at write time: no links, phone numbers or instructions to a model
        saved = store.set_low_sugar_plan(parent.id, (body.low_sugar_plan or "").strip() or None)
    except UnsafeTextError as exc:
        raise HTTPException(422, [str(exc)]) from exc
    _audit(
        store,
        "member",
        "dashboard:update_care_plan",
        parent.id,
        {"by": me.name, "doctor_confirmed": True},
        {"low_sugar_plan_set": saved is not None},
    )
    return get_care_plan(parent_id, request, me)


@router.get("/parents/{parent_id}/sharing")
def get_sharing(parent_id: str, request: Request, me: Me):
    store = _store(request)
    return _sharing(store, me, _parent(store, me, parent_id))


class SharingUpdate(BaseModel):
    readings: bool
    alerts: bool
    medicines: bool


@router.put("/parents/{parent_id}/sharing")
def put_sharing(parent_id: str, body: SharingUpdate, request: Request, me: Me):
    store = _store(request)
    parent = _parent(store, me, parent_id)
    if me.id != parent.id:
        raise HTTPException(403, f"Only {parent.name} can change what is shared (parent-first).")
    store.set_sharing(
        Sharing(parent_id=parent.id, **body.model_dump(), updated_by=me.id, updated_at=utcnow())
    )
    _audit(
        store,
        "member",
        "dashboard:update_sharing",
        parent.id,
        {"by": me.name, **body.model_dump()},
        {"saved": True},
    )
    return _sharing(store, me, parent)


@router.get("/audit")
def audit(
    request: Request, me: Me, parent_id: str | None = None, limit: int = 100
) -> dict[str, Any]:
    """What the AI did (MCP tool calls) and what people did, newest first. Children see
    the details of an entry only for categories the parent shares."""
    store = _store(request)
    parents = (
        [_parent(store, me, parent_id)]
        if parent_id
        else store.list_members(me.family_id, role="parent")
    )
    limit = max(1, min(limit, 500))
    entries = [e for p in parents for e in store.list_audit(parent_id=p.id, limit=limit)]
    entries.sort(key=lambda e: e.created_at, reverse=True)
    by_id = {p.id: p for p in parents}

    def row(e: AuditEntry) -> dict[str, Any]:
        category = TOOL_CATEGORY.get(e.tool)
        parent = by_id[e.parent_id]  # type: ignore[index]
        shown = category is None or _can_see(store, me, parent, category)
        if not shown and e.tool == "alert_family" and _consented(me, parent):
            shown = json.loads(e.result_json).get("level") == "red"  # red always reaches family
        result = json.loads(e.result_json) if shown else None
        return {
            "id": e.id,
            "at": _iso(e.created_at),
            "actor": e.actor,
            "tool": e.tool,
            "via": "mcp" if e.actor == "agent" else "dashboard",
            "args": json.loads(e.args_json) if shown else None,
            "result": result,
            "error": isinstance(result, dict) and "error" in result,
            "redacted": not shown,
        }

    rows = [row(e) for e in entries[:limit]]
    agent = [e for e in entries if e.actor == "agent"]
    return {
        "entries": rows,
        "stats": {
            "ai_tool_calls": len(agent),
            "ai_refused_calls": sum(r["error"] for r in rows if r["actor"] == "agent"),
            "ai_approve_calls": sum("approve" in e.tool for e in agent),
            "family_approvals": sum(e.tool == "dashboard:approve_refill" for e in entries),
        },
    }


@router.get("/mcp/tools")
def mcp_tools(request: Request) -> dict[str, Any]:
    """The tools the MCP server exposes to the AI (live), to show there is no approve tool."""
    from app.agent.rule_agent import ALLOWED_TOOLS

    try:
        tools, live = _svc(request).agent.ping(), True
    except Exception:
        tools, live = sorted(ALLOWED_TOOLS), False
    return {"tools": tools, "live": live, "has_approve_tool": any("approve" in t for t in tools)}


class LowStockBody(BaseModel):
    medicine_id: str


@router.post("/parents/{parent_id}/demo/low-stock")
def simulate_low_stock(parent_id: str, body: LowStockBody, request: Request, me: Me):
    """Demo button: the medicine runs low, so the AI requests a refill through the real MCP
    `request_refill` tool. The family then approves or rejects it here."""
    if get_settings().app_mode != "mock":  # demo control: mock mode only, like the simulator
        raise HTTPException(404, "Not Found")
    svc = _svc(request)
    store = svc.store
    parent = _parent(store, me, parent_id)
    _require(store, me, parent, "medicines")
    try:
        med = store.get_medicine(body.medicine_id)
    except NotFoundError:
        med = None
    if med is None or med.parent_id != parent.id:
        raise HTTPException(404, "Medicine not found.")
    stock = min(med.stock_count, med.daily_dose * 3)  # about three days left
    med = store.set_medicine_stock(med.id, stock)
    _audit(
        store,
        "system",
        "demo:simulate_low_stock",
        parent.id,
        {"by": me.name, "medicine": med.brand},
        {"stock_count": stock},
    )
    try:
        result = svc.agent.request_refill(parent, med)
    except ToolError as exc:  # the MCP server refused; shown on the dashboard
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"Could not reach the MCP server: {type(exc).__name__}") from exc
    _family_message(
        store,
        parent,
        tpl.LOW_STOCK_ASKED.format(brand=med.brand, stock=stock),
        phrases.say("low_stock", parent.language, brand=med.brand),
    )
    svc.flush_outbox()
    return {"requested": True, "tool_result": result}
