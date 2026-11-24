"""Channel webhooks: Twilio WhatsApp (always signature-checked) and optional Telegram.
Both verify, record the message id, answer immediately and process in the background."""

import hmac
import logging
from urllib.parse import parse_qsl

from fastapi import APIRouter, HTTPException, Request, Response

from app.channels.telegram import update_id
from app.config import get_settings

log = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])

EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


def _channel(request: Request, name: str):
    channel = request.app.state.service.channels.get(name)
    if channel is None:
        raise HTTPException(404, f"{name} channel is not enabled (CHANNEL in .env)")
    return channel


@router.post("/twilio")
async def twilio(request: Request) -> Response:
    channel = _channel(request, "twilio")
    # Twilio posts application/x-www-form-urlencoded; parsed here without extra packages.
    form = dict(parse_qsl((await request.body()).decode("utf-8"), keep_blank_values=True))
    base = get_settings().public_base_url.rstrip("/")
    url = f"{base}{request.url.path}" if base else str(request.url)
    if request.url.query and base:
        url += f"?{request.url.query}"
    if not channel.valid_signature(url, form, request.headers.get("X-Twilio-Signature")):
        raise HTTPException(403, "Invalid Twilio signature")
    # Record MessageSid, then answer at once and process in the background (see
    # app/channels/inbound.py, including the crash-after-ack gap). A repeated MessageSid
    # (Twilio retry) gets the same empty answer and is not processed again.
    await request.app.state.inbound.accept("twilio", form, form.get("MessageSid") or None)
    # Replies go out through the REST API (rate-limited), so the TwiML stays empty.
    return Response(EMPTY_TWIML, media_type="text/xml")


@router.post("/telegram")
async def telegram(request: Request) -> dict[str, bool]:
    settings = get_settings()
    secret = settings.telegram_webhook_secret
    if settings.telegram_mode != "webhook" or not secret:
        raise HTTPException(404, "Telegram webhook mode is off")
    got = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not hmac.compare_digest(got, secret):
        raise HTTPException(403, "Invalid Telegram secret token")
    _channel(request, "telegram")
    update = await request.json()
    if not isinstance(update, dict):
        raise HTTPException(400, "Expected a Telegram update object")
    # Same as Twilio: record update_id, answer at once, process in the background.
    await request.app.state.inbound.accept("telegram", update, update_id(update))
    return {"ok": True}
