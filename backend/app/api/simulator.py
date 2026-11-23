"""Web simulator routes (/simulator): a WhatsApp-style parent chat, the children's phones,
and a live "What the AI did" panel built from audit_log. Plain HTML + JS, no build step."""

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from app import clock
from app.channels.base import ChannelError
from app.channels.service import KhayalService
from app.channels.simulator import VOICE_SAMPLES, SimulatorEvent
from app.config import get_settings
from app.demo import ensure_demo_family, load_sample, sample_photos, seed_fresh_family
from app.reader.labels import SAMPLES_DIR
from app.storage.models import utcnow

PAGE = Path(__file__).parent / "static" / "simulator.html"

router = APIRouter(prefix="/simulator", tags=["simulator"])


def _service(request: Request) -> KhayalService:
    return request.app.state.service


class SkipRequest(BaseModel):
    minutes: float = Field(default=5, gt=0, le=120)


class AdvanceRequest(BaseModel):
    minutes: float | None = Field(default=None, gt=0, le=7 * 24 * 60)
    to: str | None = None  # "next_reminder"


@router.get("", response_class=HTMLResponse, include_in_schema=False)
def page() -> HTMLResponse:
    return HTMLResponse(PAGE.read_text(encoding="utf-8"))


@router.get("/samples")
def samples() -> list[dict[str, str]]:
    return sample_photos()


@router.get("/voice-samples")
def voice_samples() -> list[dict[str, str]]:
    """Mock-mode voice notes (what the parent 'says')."""
    return [{"name": k, "transcript": v} for k, v in VOICE_SAMPLES.items()]


@router.get("/samples/{name}", include_in_schema=False)
def sample_file(name: str) -> FileResponse:
    if load_sample(name) is None:
        raise HTTPException(404, "Unknown sample")
    return FileResponse(SAMPLES_DIR / name)


def _audit_row(entry) -> dict[str, Any]:
    def load(text: str) -> Any:
        try:
            return json.loads(text)
        except ValueError:
            return text

    result = load(entry.result_json)
    return {
        "id": entry.id,
        "at": entry.created_at.isoformat(),
        "actor": entry.actor,
        "tool": entry.tool,
        "args": load(entry.args_json),
        "result": result,
        "error": isinstance(result, dict) and "error" in result,
    }


@router.get("/api/state")
def state(request: Request) -> dict[str, Any]:
    svc = _service(request)
    parent = ensure_demo_family(svc.store)
    family = svc.store.list_members(parent.family_id)

    def member(m) -> dict[str, Any]:
        return {
            "id": m.id,
            "name": m.name,
            "role": m.role,
            "ref": m.channel_ref,
            "channel": m.channel,
            "consent": m.consent_status,
            "feed": [i.model_dump() for i in svc.simulator.feed(m.channel_ref)],
        }

    conv = svc.store.get_conversation_state(parent.id)
    settings = get_settings()
    return {
        "parent": member(parent),
        "children": [member(m) for m in family if m.role == "child"],
        "conversation": {"state": conv.state, "payload": conv.payload},
        "audit": [_audit_row(a) for a in svc.store.list_audit(limit=40)],
        "clock": {
            "now": utcnow().isoformat(),
            "offset_minutes": round(clock.offset().total_seconds() / 60),
            "next_reminder_minutes": svc.minutes_to_next_reminder(),
        },
        "info": {
            "app_mode": settings.app_mode,
            "reader": svc.reader.name,
            "agent": "rule-based",
            "mcp_transport": svc.agent.transport,
            "mcp_url": svc.mcp_url,
            "channels": sorted(svc.channels),
            "voice": svc.tts.name if svc.tts else "off",
            "stt": svc.stt.name if svc.stt else "off",
            "calls": svc.calls.name if svc.calls else "off",
        },
    }


@router.get("/api/mcp")
def mcp_status(request: Request) -> dict[str, Any]:
    """Can the agent reach the MCP server? Lists the tools it exposes."""
    try:
        tools = _service(request).agent.ping()
    except Exception as exc:  # shown on the page, so the demo never fails silently
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]}
    return {"ok": True, "tools": tools}


@router.post("/api/send")
def send(event: SimulatorEvent, request: Request) -> dict[str, Any]:
    svc = _service(request)
    try:
        out = svc.handle("simulator", event)
    except ChannelError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"sent": len(out)}


@router.post("/api/skip")
def skip(body: SkipRequest, request: Request) -> dict[str, Any]:
    svc = _service(request)
    parent = ensure_demo_family(svc.store)
    for m in svc.store.list_members(parent.family_id):
        svc.simulator.note(m.channel_ref, f"⏩ {body.minutes:g} minutes later")
    out = svc.skip_time(parent.id, body.minutes)
    return {"sent": len(out)}


@router.post("/api/advance")
def advance(body: AdvanceRequest, request: Request) -> dict[str, Any]:
    """Demo clock: jump forward (e.g. to the next medicine reminder). Reminders, follow-ups,
    missed-dose alerts, low stock and the weekly summary all run as time passes."""
    svc = _service(request)
    if get_settings().app_mode == "aws":
        raise HTTPException(403, "The demo clock is disabled in aws mode")
    minutes = body.minutes
    if body.to == "next_reminder":
        minutes = svc.minutes_to_next_reminder()
        if minutes is None:
            raise HTTPException(409, "No medicine reminders are set.")
    if not minutes:
        raise HTTPException(422, "Give minutes or to=next_reminder.")
    parent = ensure_demo_family(svc.store)
    label = f"⏩ {_duration(minutes)} later (demo clock)"
    for m in svc.store.list_members(parent.family_id):
        svc.simulator.note(m.channel_ref, label)
    out = svc.advance_time(minutes)
    return {"sent": len(out), "now": utcnow().isoformat(), "minutes": minutes}


def _duration(minutes: float) -> str:
    h, m = divmod(round(minutes), 60)
    if h and m:
        return f"{h} h {m} min"
    return f"{h} h" if h else f"{m} min"


@router.post("/api/weekly-summary")
def weekly_summary(request: Request) -> dict[str, Any]:
    """Demo: send this week's summary to the family now (normally Sunday 19:00 IST)."""
    svc = _service(request)
    parent = ensure_demo_family(svc.store)
    out = svc.send_weekly_summary(parent.id)
    return {"sent": len(out)}


@router.post("/api/reset")
def reset(request: Request) -> dict[str, Any]:
    """Wipe the demo database and start a fresh family (consent pending)."""
    svc = _service(request)
    if get_settings().app_mode == "aws":
        raise HTTPException(403, "Reset is disabled in aws mode")
    svc.store.reset()
    clock.reset(svc.store)
    svc.simulator.clear()
    parent = seed_fresh_family(svc.store)
    return {"parent_id": parent.id}
