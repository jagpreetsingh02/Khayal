"""Security hardening (H2): signed sessions, logout, login rate limits, one-time sign-in
codes (and their outbox messages), MCP shared-secret auth, the startup config check,
security headers, and alert texts that say when the reading was taken."""

import asyncio
import json
import logging
import re
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from fastmcp.utilities.tests import run_server_async

from app import clock
from app.agent.rule_agent import RuleAgent
from app.alerts.notifier import StoreNotifier
from app.alerts.thresholds import DEFAULT_THRESHOLDS, evaluate_glucose, evaluate_reading
from app.api.auth import LoginLimits, sign_session
from app.api.login_codes import CODE_TTL, issue_code
from app.channels.base import Channel, ChannelError
from app.channels.service import KhayalService
from app.channels.simulator import SimulatorChannel
from app.config import Settings, get_settings
from app.demo import CHILDREN, PARENT_REF, load_sample
from app.main import create_app
from app.reader.mock_reader import MockReader
from app.startup_checks import config_problems, enforce
from app.storage.models import REDACTED_TEXT, LoginCode, Reading
from app.storage.sqlite_store import SQLiteStore
from mcp_server.server import create_server
from tests.channel_helpers import family, service
from tests.test_dashboard_api import TOKEN, seed_demo

CODE = re.compile(r"\b(\d{6})\b")
SECRET = "test-session-secret-" + "s" * 20


class App:
    def __init__(self, client: TestClient, store: SQLiteStore, svc: KhayalService) -> None:
        self.c, self.store, self.svc = client, store, svc
        self.parent = store.find_member_by_channel("simulator", PARENT_REF)
        self.ravi = store.find_member_by_channel("simulator", CHILDREN[0][1])

    def demo_login(self, member) -> str:
        r = self.c.post("/api/login", json={"token": TOKEN, "member_id": member.id})
        assert r.status_code == 200, r.text
        return r.json()["session"]

    def request_code(self, member_id: str):
        return self.c.post("/api/login/code/request", json={"member_id": member_id})

    def verify(self, member_id: str, code: str):
        return self.c.post("/api/login/code/verify", json={"member_id": member_id, "code": code})

    def last_code(self, member) -> str:
        feed = self.svc.simulator.feed(member.channel_ref)
        texts = [i.text for i in feed if i.direction == "out"]
        codes = [m.group(1) for t in texts if "sign-in code" in t for m in [CODE.search(t)] if m]
        assert codes, texts
        return codes[-1]

    def reset_ip_limits(self) -> None:
        self.c.app.state.login_limits = LoginLimits()


def make_app(monkeypatch, mode: str = "mock"):
    monkeypatch.setenv("DASHBOARD_DEMO_TOKEN", TOKEN)
    monkeypatch.setenv("APP_MODE", mode)
    monkeypatch.setenv("SESSION_SECRET", SECRET)
    get_settings.cache_clear()
    store = SQLiteStore(":memory:")
    seed_demo.seed(store)
    server = create_server(store, StoreNotifier(lambda: store))
    svc = KhayalService(
        store, MockReader(), RuleAgent(store, server), [SimulatorChannel(load_sample)]
    )
    return store, svc


@pytest.fixture
def app(monkeypatch):
    store, svc = make_app(monkeypatch)
    with TestClient(create_app(svc, tick_seconds=0)) as client:
        yield App(client, store, svc)
    store.close()
    get_settings.cache_clear()


@pytest.fixture
def dev_app(monkeypatch):
    store, svc = make_app(monkeypatch, mode="dev")
    with TestClient(create_app(svc, tick_seconds=0)) as client:
        yield App(client, store, svc)
    store.close()
    get_settings.cache_clear()


def bearer(session: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {session}"}


# ---------- sessions ----------


def test_session_shows_its_expiry_in_me(app):
    me = app.c.get("/api/me", headers=bearer(app.demo_login(app.ravi))).json()
    issued = datetime.fromisoformat(me["session"]["issued_at"])
    expires = datetime.fromisoformat(me["session"]["expires_at"])
    assert expires - issued == timedelta(hours=12)  # SESSION_TTL_HOURS default


def test_expired_session_is_rejected(app):
    old, _ = sign_session(app.ravi.id, now=datetime.now(UTC).timestamp() - 13 * 3600)
    assert app.c.get("/api/me", headers=bearer(old)).status_code == 401
    fresh, _ = sign_session(app.ravi.id)
    assert app.c.get("/api/me", headers=bearer(fresh)).status_code == 200


def test_session_ttl_comes_from_settings(app, monkeypatch):
    monkeypatch.setenv("SESSION_TTL_HOURS", "1")
    get_settings.cache_clear()
    two_hours_ago = datetime.now(UTC).timestamp() - 2 * 3600
    session, _ = sign_session(app.ravi.id, now=two_hours_ago)
    assert app.c.get("/api/me", headers=bearer(session)).status_code == 401


def test_tampered_session_is_rejected(app):
    session = app.demo_login(app.ravi)
    body, _, sig = session.partition(".")
    swapped = body.replace(body[5], "A" if body[5] != "A" else "B")
    assert app.c.get("/api/me", headers=bearer(f"{swapped}.{sig}")).status_code == 401
    # Another member's payload with Ravi's signature: rejected too.
    priya = app.store.find_member_by_channel("simulator", CHILDREN[1][1])
    other = sign_session(priya.id)[0].partition(".")[0]
    assert app.c.get("/api/me", headers=bearer(f"{other}.{sig}")).status_code == 401
    for junk in ("", "abc", "a.b", "....", f"{app.ravi.id}.deadbeef"):
        assert app.c.get("/api/me", headers=bearer(junk)).status_code == 401


def test_session_signed_with_another_secret_is_rejected(app, monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "someone-elses-secret-" + "x" * 20)
    get_settings.cache_clear()
    forged, _ = sign_session(app.ravi.id)
    monkeypatch.setenv("SESSION_SECRET", SECRET)
    get_settings.cache_clear()
    assert app.c.get("/api/me", headers=bearer(forged)).status_code == 401


def test_logout_ends_the_session_and_is_audited(app):
    session = app.demo_login(app.ravi)
    other_device = app.demo_login(app.ravi)
    assert app.c.post("/api/logout", headers=bearer(session)).json() == {"ok": True}
    assert app.c.get("/api/me", headers=bearer(session)).status_code == 401
    assert app.c.get("/api/me", headers=bearer(other_device)).status_code == 401  # all devices
    assert app.c.post("/api/logout").status_code == 401  # needs a session
    again = app.demo_login(app.ravi)
    assert app.c.get("/api/me", headers=bearer(again)).status_code == 200
    [entry] = app.store.list_audit(tool="dashboard:logout")
    assert entry.actor == "member" and json.loads(entry.args_json)["member_id"] == app.ravi.id
    assert entry.parent_id == app.parent.id  # shows in this family's log
    assert len(app.store.list_audit(tool="dashboard:login")) == 3


# ---------- login rate limit ----------


def test_five_failed_logins_per_ip_then_429(app):
    for _ in range(3):
        bad = app.c.post("/api/login", json={"token": "wrong", "member_id": app.ravi.id})
        assert bad.status_code == 401
    for _ in range(2):
        assert app.c.post("/api/login/members", json={"token": "wrong"}).status_code == 401
    blocked = app.c.post("/api/login", json={"token": TOKEN, "member_id": app.ravi.id})
    assert blocked.status_code == 429
    assert 1 <= int(blocked.headers["Retry-After"]) <= 300
    assert app.c.post("/api/login/members", json={"token": TOKEN}).status_code == 429
    assert app.verify(app.ravi.id, "123456").status_code == 429  # code verify too
    app.reset_ip_limits()  # (a new window)
    assert app.c.post("/api/login/members", json={"token": TOKEN}).status_code == 200


# ---------- one-time sign-in codes ----------


def test_code_sign_in_end_to_end(app):
    r = app.request_code(app.ravi.id)
    assert r.status_code == 200 and r.json()["ok"] is True
    code = app.last_code(app.ravi)  # delivered to Ravi's own chat (simulator)
    signed = app.verify(app.ravi.id, code)
    assert signed.status_code == 200, signed.text
    body = signed.json()
    assert body["member"]["id"] == app.ravi.id and body["expires_at"]
    assert app.c.get("/api/me", headers=bearer(body["session"])).status_code == 200
    assert app.verify(app.ravi.id, code).status_code == 401  # single use
    # Stored as a hash only; the delivered message's text is blanked.
    assert all(code not in r.code_hash for r in _codes(app.store))
    [row] = [o for o in app.store.list_outbox() if o.sensitive]
    assert row.delivered_at and row.text == REDACTED_TEXT
    _assert_no_code_in_audit(app.store, code)


def test_wrong_code_fails_and_is_audited_without_the_code(app):
    app.request_code(app.ravi.id)
    code = app.last_code(app.ravi)
    wrong = f"{(int(code) + 1) % 10**6:06d}"
    r = app.verify(app.ravi.id, wrong)
    assert r.status_code == 401
    [failed] = app.store.list_audit(tool="dashboard:login_failed")
    assert failed.actor == "unknown" and json.loads(failed.args_json)["method"] == "code"
    assert app.verify(app.ravi.id, code).status_code == 200  # the right one still works
    _assert_no_code_in_audit(app.store, code)
    _assert_no_code_in_audit(app.store, wrong)


def test_code_expires_after_5_minutes(app):
    app.request_code(app.ravi.id)
    code = app.last_code(app.ravi)
    clock.set_offset(CODE_TTL + timedelta(seconds=1))
    try:
        assert app.verify(app.ravi.id, code).status_code == 401
    finally:
        clock.set_offset(timedelta(0))


def test_sixth_try_fails_even_with_the_right_code(app):
    app.request_code(app.ravi.id)
    code = app.last_code(app.ravi)
    wrong = f"{(int(code) + 1) % 10**6:06d}"
    for _ in range(5):
        assert app.verify(app.ravi.id, wrong).status_code == 401
    app.reset_ip_limits()  # isolate the per-code limit from the per-IP one
    assert app.verify(app.ravi.id, code).status_code == 401


def test_new_request_invalidates_the_old_code(app):
    app.request_code(app.ravi.id)
    first = app.last_code(app.ravi)
    app.request_code(app.ravi.id)
    second = app.last_code(app.ravi)
    if first != second:  # 1 in a million they match
        assert app.verify(app.ravi.id, first).status_code == 401
    assert app.verify(app.ravi.id, second).status_code == 200


def test_unknown_member_gets_the_same_response(app):
    known = app.request_code(app.ravi.id)
    unknown = app.request_code("mem_doesnotexist")
    assert (known.status_code, known.json()) == (unknown.status_code, unknown.json())
    assert app.verify("mem_doesnotexist", "123456").status_code == 401
    assert app.verify(app.ravi.id, "not-a-code").status_code == 401


def test_code_requests_limited_to_3_per_member_per_15_minutes(app):
    for _ in range(3):
        assert app.request_code(app.ravi.id).status_code == 200
    r = app.request_code(app.ravi.id)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0
    for _ in range(3):  # unknown ids are limited the same way (no member discovery)
        assert app.request_code("mem_nobody").status_code == 200
    assert app.request_code("mem_nobody").status_code == 429
    assert app.request_code(app.parent.id).status_code == 200  # per member


def test_parent_can_sign_in_by_code_too(app):
    app.request_code(app.parent.id)
    r = app.verify(app.parent.id, app.last_code(app.parent))
    assert r.status_code == 200 and r.json()["member"]["role"] == "parent"


# ---------- login-code messages in the outbox ----------


class RejectingChannel(Channel):
    """Telegram-named channel that always fails, echoing the message body in the error."""

    name = "telegram"

    def __init__(self) -> None:
        self.calls = 0

    def receive(self, event):
        return None

    def send(self, msg) -> None:
        self.calls += 1
        raise ChannelError(f"rejected: {msg.text}")


def test_code_message_never_retried_past_expiry_and_kept_out_of_logs(caplog):
    store, _parent, child = family("simulator", "+1", "telegram", "777001")
    channel = RejectingChannel()
    svc = service(store, channel)
    t0 = datetime(2026, 11, 24, 9, 0, tzinfo=UTC)
    clock.set_offset(t0 - datetime.now(UTC))
    try:
        issue_code(store, child)
        [row] = store.list_outbox()
        code = CODE.search(row.text).group(1)
        caplog.set_level(logging.DEBUG)
        for seconds in range(0, 900, 10):  # every 10 s for 15 minutes
            svc.flush_outbox(now=t0 + timedelta(seconds=seconds))
    finally:
        clock.set_offset(timedelta(0))
    # 30 s, 1 m, 2 m: attempts at 0, 30, 90, 210 s; the next (510 s) is past the expiry.
    assert channel.calls == 4
    row = store.get_outbox(row.id)
    assert row.gave_up_at and not row.delivered_at and row.text == REDACTED_TEXT
    [failed] = store.list_audit(tool="delivery_failed")
    assert json.loads(failed.result_json)["gave_up"] is True
    _assert_no_code_in_audit(store, code)
    assert code not in caplog.text


def test_code_message_not_sent_once_expired():
    store, _parent, child = family("simulator", "+1", "telegram", "777001")
    channel = RejectingChannel()
    svc = service(store, channel)
    issue_code(store, child)
    svc.flush_outbox(now=datetime.now(UTC) + CODE_TTL + timedelta(seconds=1))
    assert channel.calls == 0
    [row] = store.list_outbox()
    assert row.gave_up_at and row.last_error == "expired" and row.text == REDACTED_TEXT


def test_new_code_drops_the_old_undelivered_message():
    store, _parent, child = family("simulator", "+1", "telegram", "777001")
    issue_code(store, child)
    issue_code(store, child)
    first, second = store.list_outbox()
    assert first.gave_up_at and first.text == REDACTED_TEXT
    assert not second.gave_up_at and CODE.search(second.text)


# ---------- dev mode: no demo token ----------


def test_demo_token_login_refused_in_dev_mode(dev_app):
    hint = dev_app.c.get("/api/login/hint").json()
    assert hint["demo_login"] is False and hint["code_login"] is True
    r = dev_app.c.post("/api/login", json={"token": TOKEN, "member_id": dev_app.ravi.id})
    assert r.status_code == 403
    assert dev_app.c.post("/api/login/members", json={"token": TOKEN}).status_code == 403
    dev_app.request_code(dev_app.ravi.id)  # code login works
    assert dev_app.verify(dev_app.ravi.id, dev_app.last_code(dev_app.ravi)).status_code == 200


def test_mock_mode_offers_both_logins(app):
    hint = app.c.get("/api/login/hint").json()
    assert hint == {"mode": "mock", "demo_login": True, "code_login": True,
                    "default_token": False}  # fmt: skip


# ---------- security headers, docs ----------


def test_security_headers_on_api_and_dashboard(app):
    for path in ("/api/login/hint", "/dashboard/", "/health"):
        h = app.c.get(path).headers
        assert h["X-Content-Type-Options"] == "nosniff", path
        assert h["X-Frame-Options"] == "DENY" and h["Referrer-Policy"] == "no-referrer"
        csp = h["Content-Security-Policy"]
        assert "script-src 'self';" in csp and "unsafe-inline" not in csp, path
        assert "frame-ancestors 'none'" in csp
    assert app.c.get("/api/login/hint").headers["Cache-Control"] == "no-store"
    assert "unsafe-inline" in app.c.get("/simulator").headers["Content-Security-Policy"]


def test_docs_only_in_mock_mode(app, dev_app):
    assert app.c.get("/openapi.json").status_code == 200
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert dev_app.c.get(path).status_code == 404, path


# ---------- MCP server auth ----------

MCP_SECRET = "mcp-test-secret-" + "m" * 24


def _over_http(check):
    store, parent, _child = family("simulator", "+919800000001", "simulator", "+2")
    server = create_server(store, StoreNotifier(lambda: store), secret=MCP_SECRET)

    async def main():
        async with run_server_async(server) as url:
            return await asyncio.to_thread(check, store, parent, url)

    return asyncio.run(main())


def test_mcp_rejects_calls_without_or_with_a_wrong_bearer():
    def check(store, parent, url):
        for secret in ("", "wrong-secret"):
            with pytest.raises(Exception):  # noqa: B017 - 401 surfaces as a client error
                RuleAgent(store, url, secret=secret).call_tool(parent, "get_meds", {})
        raw = httpx.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        wrong = httpx.post(
            url,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"Authorization": "Bearer wrong-secret"},
        )
        return raw.status_code, wrong.status_code, store.list_audit()

    raw, wrong, audit = _over_http(check)
    assert raw == 401 and wrong == 401
    assert audit == []  # no tool ran


def test_mcp_accepts_the_right_bearer():
    def check(store, parent, url):
        agent = RuleAgent(store, url, secret=MCP_SECRET)
        return agent.ping(), agent.call_tool(parent, "get_meds", {}), store.list_audit()

    tools, meds, audit = _over_http(check)
    assert "log_reading" in tools and "approve_refill" not in tools
    assert "medicines" in meds
    assert audit[0].tool == "get_meds" and audit[0].parent_id  # identity from _meta


def test_mcp_server_main_refuses_without_a_secret(monkeypatch):
    from mcp_server import server as mcp_module

    get_settings.cache_clear()
    with pytest.raises(SystemExit, match="MCP_SHARED_SECRET is empty"):
        mcp_module.main()


# ---------- startup config check ----------


def test_startup_check_lists_every_problem():
    s = Settings(
        _env_file=None,
        app_mode="aws",
        channel="twilio",
        session_secret="",
        mcp_shared_secret="short",
        public_base_url="",
        twilio_auth_token="",
    )
    errors, warnings = config_problems(s)
    text = "\n".join(errors)
    assert len(errors) == 4
    for needle in ("SESSION_SECRET is not set", "MCP_SHARED_SECRET is shorter",
                   "PUBLIC_BASE_URL", "TWILIO_AUTH_TOKEN"):  # fmt: skip
        assert needle in text
    assert any("DASHBOARD_DEMO_TOKEN" in w for w in warnings)  # only a warning
    with pytest.raises(SystemExit) as exc:
        enforce(s)
    message = str(exc.value)
    assert "4 problems" in message and all(e in message for e in errors)


def test_startup_check_telegram_webhook_needs_secret_and_mock_never_blocks():
    long = "x" * 40
    s = Settings(_env_file=None, app_mode="dev", channel="telegram", telegram_mode="webhook",
                 session_secret=long, mcp_shared_secret=long)  # fmt: skip
    errors, _ = config_problems(s)
    assert errors == ["CHANNEL=telegram with TELEGRAM_MODE=webhook needs TELEGRAM_WEBHOOK_SECRET."]
    ok = Settings(_env_file=None, app_mode="dev", channel="telegram", telegram_mode="polling",
                  session_secret=long, mcp_shared_secret=long)  # fmt: skip
    assert config_problems(ok)[0] == []
    mock = Settings(_env_file=None, app_mode="mock", channel="twilio")
    errors, warnings = config_problems(mock)
    assert errors == [] and any("SESSION_SECRET is empty" in w for w in warnings)
    enforce(mock)  # does not raise


def test_real_app_refuses_to_start_with_unsafe_config(monkeypatch):
    monkeypatch.setenv("APP_MODE", "aws")
    get_settings.cache_clear()
    try:
        with pytest.raises(SystemExit, match="SESSION_SECRET"):
            create_app()
    finally:
        get_settings.cache_clear()


# ---------- alert texts say when the reading was taken ----------


def test_red_alert_text_says_when_the_reading_was_taken():
    t = DEFAULT_THRESHOLDS
    taken = datetime(2026, 11, 24, 8, 44, tzinfo=UTC)  # 2:14 PM IST
    first = Reading(parent_id="p", type="bp", systolic=192, diastolic=124,
                    status="confirmed", created_at=taken - timedelta(minutes=6))  # fmt: skip
    repeat = Reading(parent_id="p", type="bp", systolic=198, diastolic=127,
                     status="confirmed", created_at=taken)  # fmt: skip
    red = evaluate_reading(repeat, [first], t)
    assert red.level == "red" and "BP 198/127 at 2:14 PM, 24 Nov" in red.reason
    low = evaluate_glucose(45, t, taken + timedelta(hours=10))  # 00:14 IST next day
    assert low.level == "red" and "Glucose 45 mg/dL at 12:14 AM, 25 Nov" in low.reason


# ---------- helpers ----------


def _codes(store) -> list[LoginCode]:
    return store._query(LoginCode, "SELECT * FROM login_codes")  # test peeks at the table


def _assert_no_code_in_audit(store, code: str) -> None:
    for e in store.list_audit(limit=1000):
        assert code not in e.args_json and code not in e.result_json, e.tool
