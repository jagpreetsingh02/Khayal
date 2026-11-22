"""Telegram adapter against a mocked Bot API (no network)."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.channels.base import ChannelError
from app.channels.telegram import TelegramChannel
from app.conversation import templates as tpl
from app.conversation.messages import Outgoing
from app.main import create_app
from app.reader.labels import SAMPLES_DIR
from tests.channel_helpers import family, service

TOKEN = "123:SECRET-TOKEN"
USER = 555001


class FakeBot:
    def __init__(self):
        self.sent: list[dict] = []
        self.updates: list[dict] = []
        self.files = {"big": SAMPLES_DIR / "synthetic_bp_high.jpg"}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        assert f"/bot{TOKEN}/" in path or f"/file/bot{TOKEN}/" in path
        if path.startswith("/file/"):
            name = path.rsplit("/", 1)[1]
            return httpx.Response(200, content=self.files[name].read_bytes())
        method = path.rsplit("/", 1)[1]
        body = json.loads(request.content or b"{}")
        if method == "sendMessage":
            self.sent.append(body)
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
        if method == "getFile":
            fid = body["file_id"]
            return httpx.Response(
                200, json={"ok": True, "result": {"file_id": fid, "file_path": f"photos/{fid}"}}
            )
        if method == "getUpdates":
            out, self.updates = self.updates, []
            return httpx.Response(200, json={"ok": True, "result": out})
        return httpx.Response(200, json={"ok": True, "result": True})


def channel(bot: FakeBot) -> TelegramChannel:
    return TelegramChannel(TOKEN, client=httpx.Client(transport=httpx.MockTransport(bot)))


def update(uid: int, **message) -> dict:
    msg = {"message_id": uid, "from": {"id": USER}, "chat": {"id": USER, "type": "private"}}
    return {"update_id": uid, "message": msg | message}


def test_text_and_photo_become_incoming():
    bot = FakeBot()
    ch = channel(bot)
    inc = ch.receive(update(1, text="142/88"))
    assert inc.channel == "telegram" and inc.sender_ref == str(USER) and inc.text == "142/88"
    photos = [
        {"file_id": "small", "width": 90, "height": 60},
        {"file_id": "big", "width": 1280, "height": 960},
    ]
    inc = ch.receive(update(2, photo=photos, caption="strip"))
    assert inc.image.data == (SAMPLES_DIR / "synthetic_bp_high.jpg").read_bytes()
    assert inc.text == "strip"


def test_ignored_updates_and_id_command():
    bot = FakeBot()
    ch = channel(bot)
    assert ch.receive(update(1, sticker={"file_id": "x"})) is None
    group = update(2, text="hi")
    group["message"]["chat"]["type"] = "group"
    assert ch.receive(group) is None
    assert ch.receive({"update_id": 3, "edited_message": {}}) is None
    assert ch.receive(update(4, text="/id")) is None
    assert bot.sent == [{"chat_id": USER, "text": f"Your Telegram id: {USER}"}]
    assert ch.receive(update(5, text="/start")).text == "hello"


def test_send_uses_chat_id():
    bot = FakeBot()
    channel(bot).send(Outgoing(channel="telegram", to_ref=str(USER), member_id="m", text="hi"))
    assert bot.sent == [{"chat_id": str(USER), "text": "hi"}]


def test_token_never_appears_in_errors():
    def boom(request):
        raise httpx.ConnectError("cannot connect to " + str(request.url))

    ch = TelegramChannel(TOKEN, client=httpx.Client(transport=httpx.MockTransport(boom)))
    with pytest.raises(ChannelError) as err:
        ch.send(Outgoing(channel="telegram", to_ref="1", member_id=None, text="x"))
    assert "SECRET" not in str(err.value) and "SECRET" not in repr(err.value.__cause__)

    def not_ok(request):
        return httpx.Response(401, json={"ok": False, "description": "Unauthorized"})

    ch = TelegramChannel(TOKEN, client=httpx.Client(transport=httpx.MockTransport(not_ok)))
    with pytest.raises(ChannelError, match="Unauthorized"):
        ch.poll_once(None)


def test_missing_token_fails_clearly():
    with pytest.raises(ChannelError, match="TELEGRAM_BOT_TOKEN"):
        TelegramChannel("")


def test_polling_runs_the_conversation_and_alerts_family_on_simulator():
    bot = FakeBot()
    store, parent, child = family("telegram", str(USER), "simulator", "+919800000002")
    tg = channel(bot)
    svc = service(store, tg)
    bot.updates = [update(10, text="sugar 45")]
    assert tg.poll_once(svc) == 1
    assert bot.sent[-1]["text"].startswith("I read sugar 45 mg/dL")
    bot.updates = [update(11, text="1")]
    tg.poll_once(svc)
    assert tg.offset == 12
    assert "I have told your family." in bot.sent[-1]["text"]
    [alert] = [i.text for i in svc.simulator.feed(child.channel_ref)]
    assert alert.startswith("RED alert for Kamla")


def test_unknown_telegram_user_is_told_to_register():
    bot = FakeBot()
    store, *_ = family("simulator", "+1", "simulator", "+2")
    tg = channel(bot)
    bot.updates = [update(1, text="hello")]
    tg.poll_once(service(store, tg))
    assert bot.sent[-1]["text"] == tpl.UNKNOWN_SENDER


def test_webhook_mode_checks_the_secret(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("TELEGRAM_MODE", "webhook")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "s3cret")
    get_settings.cache_clear()
    try:
        bot = FakeBot()
        store, *_ = family("telegram", str(USER), "simulator", "+2")
        tg = channel(bot)
        with TestClient(create_app(service(store, tg), tick_seconds=0)) as client:
            r = client.post("/webhooks/telegram", json=update(1, text="help"))
            assert r.status_code == 403
            headers = {"X-Telegram-Bot-Api-Secret-Token": "s3cret"}
            r = client.post("/webhooks/telegram", json=update(2, text="help"), headers=headers)
            assert r.status_code == 200
        assert bot.sent[-1]["text"] == tpl.HELP
    finally:
        get_settings.cache_clear()
