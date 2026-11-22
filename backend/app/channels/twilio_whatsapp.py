"""Twilio WhatsApp channel (CHANNEL=twilio): Sandbox for dev.

- Inbound: POST /webhooks/twilio (form-encoded). Every request must carry a valid
  X-Twilio-Signature (HMAC-SHA1 of the public URL + sorted form fields, keyed with the
  auth token); set PUBLIC_BASE_URL to the URL Twilio calls (e.g. your ngrok https URL).
- Sender mapping: `From=whatsapp:+9198...` -> channel_ref "+9198..." (channel "twilio").
- Photos: MediaUrl0. Twilio can enforce HTTP Basic auth on media URLs and then redirects
  to a CDN on another host; we always send Basic auth (account SID + token) and httpx
  drops the header on the cross-host redirect. A 404 right after the webhook can mean the
  media is not there yet, so it is retried briefly. Not verified live yet.
- Outbound: REST Messages API, at most one message per 3 seconds (Sandbox limit), sent
  from a background queue so the webhook answers fast.
- 24-hour window: free-form text only to numbers that wrote to us in the last 24 h.
  Anything proactive outside it (alerts to children who never wrote) must use a
  pre-approved template: the Sandbox only allows its fixed templates, so set
  TWILIO_TEMPLATE_CONTENT_SID to one of them (Console > Messaging > Content Template
  Builder); without it such messages are not sent (logged instead).
"""

import base64
import hashlib
import hmac
import json
import logging
import queue
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.channels.base import Channel, ChannelError
from app.config import Settings
from app.conversation.messages import Incoming, Outgoing
from app.reader.schema import ImageInput
from app.storage.models import utcnow

log = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

API = "https://api.twilio.com/2010-04-01"
PREFIX = "whatsapp:"
MIN_INTERVAL = 3.0  # seconds between messages (Sandbox)
WINDOW = timedelta(hours=24)
MAX_MEDIA_BYTES = 16 * 1024 * 1024
MEDIA_404_RETRIES = 2


def twilio_signature(auth_token: str, url: str, params: dict[str, str]) -> str:
    """Twilio's request signature: base64(HMAC-SHA1(token, url + k1 + v1 + k2 + v2 ...))."""
    payload = url + "".join(k + params[k] for k in sorted(params))
    digest = hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def strip_prefix(address: str) -> str:
    return address[len(PREFIX) :] if address.startswith(PREFIX) else address


class TwilioWhatsAppChannel(Channel):
    name = "twilio"

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        from_number: str,
        *,
        template_sid: str = "",
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        background: bool = True,
    ) -> None:
        if not account_sid or not auth_token:
            raise ChannelError("CHANNEL=twilio needs TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN")
        self._sid = account_sid
        self._token = auth_token
        self.from_number = from_number if from_number.startswith(PREFIX) else PREFIX + from_number
        self.template_sid = template_sid
        self._client = client or httpx.Client(timeout=30, follow_redirects=True)
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_sent: float | None = None
        self._last_inbound: dict[str, datetime] = {}  # number -> last message from them
        self._queue: queue.Queue[Outgoing] = queue.Queue()
        self._background = background
        self._worker: threading.Thread | None = None

    @classmethod
    def from_settings(cls, s: Settings) -> "TwilioWhatsAppChannel":
        return cls(
            s.twilio_account_sid,
            s.twilio_auth_token,
            s.twilio_whatsapp_from,
            template_sid=s.twilio_template_content_sid,
        )

    # --- inbound ---

    def valid_signature(self, url: str, params: dict[str, str], signature: str | None) -> bool:
        if not signature:
            return False
        expected = twilio_signature(self._token, url, params)
        return hmac.compare_digest(expected, signature)

    def _download(self, url: str) -> bytes:
        if not url.startswith("https://"):
            raise ChannelError("Media URL must be https")
        for attempt in range(MEDIA_404_RETRIES + 1):
            try:
                r = self._client.get(url, auth=(self._sid, self._token))
            except httpx.HTTPError as exc:
                raise ChannelError(f"Media download failed: {type(exc).__name__}") from None
            if r.status_code == 404 and attempt < MEDIA_404_RETRIES:
                self._sleep(1.0 + attempt)  # media may not have propagated yet
                continue
            if r.status_code != 200:
                raise ChannelError(f"Media download failed: HTTP {r.status_code}")
            if len(r.content) > MAX_MEDIA_BYTES:
                raise ChannelError("Media larger than 16 MB")
            return r.content
        raise ChannelError("Media download failed")  # pragma: no cover

    def receive(self, form: dict[str, str]) -> Incoming | None:
        sender = strip_prefix(form.get("From", ""))
        if not sender:
            return None
        self._last_inbound[sender] = utcnow()
        text = (form.get("Body") or "").strip() or None
        image = None
        if int(form.get("NumMedia") or 0) > 0:
            ctype = form.get("MediaContentType0", "")
            if ctype.startswith("image/") and form.get("MediaUrl0"):
                image = ImageInput(data=self._download(form["MediaUrl0"]), filename=None)
        if text is None and image is None:
            return None  # e.g. a voice note (Phase 5) or a document
        return Incoming(channel="twilio", sender_ref=sender, text=text, image=image)

    # --- outbound ---

    def in_window(self, number: str, now: datetime | None = None) -> bool:
        last = self._last_inbound.get(number)
        return last is not None and (now or utcnow()) - last < WINDOW

    def send(self, msg: Outgoing) -> None:
        self._queue.put(msg)
        if self._background and (self._worker is None or not self._worker.is_alive()):
            self._worker = threading.Thread(target=self._run, name="twilio-sender", daemon=True)
            self._worker.start()

    def _run(self) -> None:
        while True:
            msg = self._queue.get()
            try:
                self.deliver(msg)
            except Exception:
                log.exception("Twilio send to %s failed", msg.to_ref)

    def drain(self) -> None:
        """Send everything queued, in this thread (tests, background=False)."""
        while not self._queue.empty():
            self.deliver(self._queue.get_nowait())

    def _throttle(self) -> None:
        if self._last_sent is not None:
            wait = MIN_INTERVAL - (self._monotonic() - self._last_sent)
            if wait > 0:
                self._sleep(wait)
        self._last_sent = self._monotonic()

    def deliver(self, msg: Outgoing) -> dict[str, Any] | None:
        data: dict[str, str] = {"From": self.from_number, "To": PREFIX + msg.to_ref}
        if self.in_window(msg.to_ref):
            data["Body"] = msg.text
        elif self.template_sid:
            data["ContentSid"] = self.template_sid
            data["ContentVariables"] = json.dumps(template_variables(msg.text))
        else:
            log.warning(
                "not sent to %s: outside the 24 h window and no TWILIO_TEMPLATE_CONTENT_SID",
                msg.to_ref,
            )
            return None
        self._throttle()
        try:
            r = self._client.post(
                f"{API}/Accounts/{self._sid}/Messages.json",
                data=data,
                auth=(self._sid, self._token),
            )
        except httpx.HTTPError as exc:
            raise ChannelError(f"Twilio send failed: {type(exc).__name__}") from None
        if r.status_code >= 300:
            raise ChannelError(f"Twilio send failed: HTTP {r.status_code} {r.text[:200]}")
        return r.json()


def template_variables(text: str, now: datetime | None = None) -> dict[str, str]:
    """Fill a two-variable Sandbox template: {{1}} = what (short), {{2}} = local time."""
    first = text.split(":", 1)[0] if text.startswith(("RED", "AMBER")) else "Care Circle update"
    local = (now or utcnow()).astimezone(ZoneInfo("Asia/Kolkata"))
    return {"1": first[:60], "2": local.strftime("%d %b %H:%M")}
