"""Web simulator routes (/simulator): a WhatsApp-style parent chat, the children's phones,
and a live "What the AI did" panel built from audit_log. Plain HTML + JS, no build step."""

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from app.channels.base import ChannelError
from app.channels.service import CareCircleService
from app.channels.simulator import SimulatorEvent
from app.config import get_settings
from app.demo import ensure_demo_family, load_sample, sample_photos, seed_fresh_family
from app.reader.labels import SAMPLES_DIR

PAGE = Path(__file__).parent / "static" / "simulator.html"

router = APIRouter(prefix="/simulator", tags=["simulator"])


def _service(request: Request) -> CareCircleService:
    return request.app.state.service


class SkipRequest(BaseModel):
    minutes: float = Field(default=5, gt=0, le=120)


@router.get("", response_class=HTMLResponse, include_in_schema=False)
def page() -> HTMLResponse:
    return HTMLResponse(PAGE.read_text(encoding="utf-8"))


@router.get("/samples")
def samples() -> list[dict[str, str]]:
    return sample_photos()


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
        "info": {
            "app_mode": settings.app_mode,
            "reader": svc.reader.name,
            "agent": "rule-based",
            "mcp_transport": svc.agent.transport,
            "mcp_url": svc.mcp_url,
            "channels": sorted(svc.channels),
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


@router.post("/api/reset")
def reset(request: Request) -> dict[str, Any]:
    """Wipe the demo database and start a fresh family (consent pending)."""
    svc = _service(request)
    if get_settings().app_mode == "aws":
        raise HTTPException(403, "Reset is disabled in aws mode")
    svc.store.reset()
    svc.simulator.clear()
    parent = seed_fresh_family(svc.store)
    return {"parent_id": parent.id}
