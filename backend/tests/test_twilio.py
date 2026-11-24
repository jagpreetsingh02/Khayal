"""Twilio WhatsApp adapter with mocked HTTP only (no live Twilio calls)."""

import base64
import json
from urllib.parse import parse_qsl, urlencode

import httpx
import pytest
from fastapi.testclient import TestClient

from app.channels.base import ChannelError
from app.channels.twilio_whatsapp import (
    TwilioWhatsAppChannel,
    strip_prefix,
    template_variables,
    twilio_signature,
)
from app.conversation.messages import Outgoing
from app.main import create_app
from app.reader.labels import SAMPLES_DIR
from tests.channel_helpers import family, service

SID, TOKEN = "AC_test_sid", "test_token"
PARENT = "+919811111111"
CHILD = "+919822222222"
MEDIA = "https://api.twilio.com/2010-04-01/Accounts/AC_test_sid/Messages/MM1/Media/ME1"
PHOTO = (SAMPLES_DIR / "synthetic_glucose_mgdl.jpg").read_bytes()


class FakeTwilio:
    def __init__(self, media_404s: int = 0):
        self.messages: list[dict] = []
        self.media_404s = media_404s
        self.cdn_auth_headers: list[str | None] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "mms.twiliocdn.com":  # redirect target on another host
            self.cdn_auth_headers.append(request.headers.get("Authorization"))
            return httpx.Response(200, content=PHOTO)
        expected = "Basic " + base64.b64encode(f"{SID}:{TOKEN}".encode()).decode()
        if request.headers.get("Authorization") != expected:
            return httpx.Response(401)
        if request.url.path.endswith("/Messages.json"):
            self.messages.append(dict(parse_qsl(request.content.decode())))
            return httpx.Response(201, json={"sid": f"SM{len(self.messages)}"})
        if "/Media/" in request.url.path:
            if self.media_404s:
                self.media_404s -= 1
                return httpx.Response(404)
            return httpx.Response(307, headers={"Location": "https://mms.twiliocdn.com/x/y"})
        return httpx.Response(404)


class Clock:
    def __init__(self):
        self.t = 1000.0
        self.slept: list[float] = []

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def channel(fake, template_sid="", clock=None):
    clock = clock or Clock()
    return TwilioWhatsAppChannel(
        SID,
        TOKEN,
        "whatsapp:+14155238886",
        template_sid=template_sid,
        client=httpx.Client(transport=httpx.MockTransport(fake), follow_redirects=True),
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )


def form(**kw) -> dict[str, str]:
    base = {"From": f"whatsapp:{PARENT}", "To": "whatsapp:+14155238886", "NumMedia": "0"}
    return base | kw


def test_signature_matches_twilio_algorithm_and_rejects_tampering():
    params = {"Body": "hi", "From": "whatsapp:+1", "NumMedia": "0"}
    url = "https://example.ngrok.app/webhooks/twilio"
    sig = twilio_signature(TOKEN, url, params)
    # independent re-computation of: base64(hmac_sha1(token, url + sorted k+v))
    import hashlib
    import hmac

    raw = url + "Bodyhi" + "Fromwhatsapp:+1" + "NumMedia0"
    assert (
        sig
        == base64.b64encode(hmac.new(b"test_token", raw.encode(), hashlib.sha1).digest()).decode()
    )
    ch = channel(FakeTwilio())
    assert ch.valid_signature(url, params, sig)
    assert not ch.valid_signature(url, params | {"Body": "hi!"}, sig)
    assert not ch.valid_signature(url + "x", params, sig)
    assert not ch.valid_signature(url, params, None)


def test_sender_mapping_strips_whatsapp_prefix():
    assert strip_prefix("whatsapp:+919800000001") == "+919800000001"
    inc = channel(FakeTwilio()).receive(form(Body=" 142/88 "))
    assert inc.channel == "twilio" and inc.sender_ref == PARENT and inc.text == "142/88"


def test_media_download_uses_basic_auth_and_drops_it_on_cdn_redirect():
    fake = FakeTwilio()
    inc = channel(fake).receive(form(NumMedia="1", MediaUrl0=MEDIA, MediaContentType0="image/jpeg"))
    assert inc.image.data == PHOTO
    assert fake.cdn_auth_headers == [None]  # credentials never sent to the CDN host


def test_media_404_is_retried_then_succeeds():
    fake = FakeTwilio(media_404s=1)
    clock = Clock()
    inc = channel(fake, clock=clock).receive(
        form(NumMedia="1", MediaUrl0=MEDIA, MediaContentType0="image/jpeg")
    )
    assert inc.image.data == PHOTO and clock.slept == [1.0]
    with pytest.raises(ChannelError, match="HTTP 404"):
        channel(FakeTwilio(media_404s=5)).receive(
            form(NumMedia="1", MediaUrl0=MEDIA, MediaContentType0="image/jpeg")
        )


def test_non_image_media_and_http_urls_are_refused():
    ch = channel(FakeTwilio())
    assert ch.receive(form(NumMedia="1", MediaUrl0=MEDIA, MediaContentType0="audio/ogg")) is None
    with pytest.raises(ChannelError, match="https"):
        ch.receive(form(NumMedia="1", MediaUrl0="http://x/y", MediaContentType0="image/png"))


def test_rate_limit_one_message_per_3_seconds():
    fake, clock = FakeTwilio(), Clock()
    ch = channel(fake, clock=clock)
    ch.receive(form(Body="hi"))  # opens the 24 h window for the parent
    for i in range(3):
        ch.send(Outgoing(channel="twilio", to_ref=PARENT, member_id="m", text=f"msg {i}"))
    assert [m["Body"] for m in fake.messages] == ["msg 0", "msg 1", "msg 2"]
    assert clock.slept == [3.0, 3.0]
    assert fake.messages[0]["To"] == f"whatsapp:{PARENT}"
    assert fake.messages[0]["From"] == "whatsapp:+14155238886"


def test_outside_24h_window_uses_the_sandbox_template_or_is_not_sent():
    fake = FakeTwilio()
    alert = Outgoing(channel="twilio", to_ref=CHILD, member_id="c", text="RED alert for Lakshmi: x")
    assert channel(fake).deliver(alert) is None and fake.messages == []
    channel(fake, template_sid="HXabc").deliver(alert)
    [sent] = fake.messages
    assert "Body" not in sent and sent["ContentSid"] == "HXabc"
    assert json.loads(sent["ContentVariables"])["1"] == "RED alert for Lakshmi"
    assert template_variables("Saved")["1"] == "Khayal update"


def test_webhook_rejects_bad_signature_and_handles_good_one(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("PUBLIC_BASE_URL", "https://demo.ngrok.app")
    get_settings.cache_clear()
    try:
        fake = FakeTwilio()
        store, parent, child = family("twilio", PARENT, "simulator", "+919800000002")
        tw = channel(fake)
        svc = service(store, tw)
        with TestClient(create_app(svc, tick_seconds=0)) as client:

            def post(params, sig):
                return client.post(
                    "/webhooks/twilio",
                    content=urlencode(params),
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded",
                        "X-Twilio-Signature": sig,
                    },
                )

            url = "https://demo.ngrok.app/webhooks/twilio"
            params = form(Body="sugar 45")
            assert post(params, "wrong").status_code == 403
            r = post(params, twilio_signature(TOKEN, url, params))
            assert r.status_code == 200 and r.text.endswith("<Response></Response>")
            params = form(Body="1")
            post(params, twilio_signature(TOKEN, url, params))
        bodies = [m["Body"] for m in fake.messages]
        assert bodies[0].startswith("I read sugar 45 mg/dL")
        assert "I have told your family." in bodies[1]
        assert svc.simulator.feed(child.channel_ref)[-1].text.startswith("RED alert for Lakshmi")
    finally:
        get_settings.cache_clear()


def test_missing_credentials_fail_clearly():
    with pytest.raises(ChannelError, match="TWILIO_ACCOUNT_SID"):
        TwilioWhatsAppChannel("", "", "whatsapp:+1")
