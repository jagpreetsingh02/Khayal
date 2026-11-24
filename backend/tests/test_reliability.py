"""Reliability (hardening H1): outbox retries, inbound dedup, per-parent ordering."""

import threading
import time
from datetime import timedelta
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from app.api.dashboard import _alert, _delivery
from app.channels.base import Channel, ChannelError
from app.channels.service import MAX_ATTEMPTS, KhayalService, retry_delay
from app.channels.simulator import SimulatorChannel, SimulatorEvent
from app.channels.turns import Turns
from app.conversation import templates as tpl
from app.demo import load_sample
from app.main import create_app
from app.reader.mock_reader import MockReader
from app.storage.models import Family, Member, OutboxMessage, utcnow
from app.storage.sqlite_store import SQLiteStore
from tests import test_telegram as tg_test
from tests import test_twilio as tw_test
from tests.channel_helpers import family, service

PARENT, CHILD = "+919800000001", "777001"


class FlakyChannel(Channel):
    """A Telegram-named channel whose send() fails `failures` times, then works."""

    name = "telegram"

    def __init__(self, failures: int) -> None:
        self.failures, self.calls, self.sent = failures, 0, []

    def receive(self, event):
        return None

    def send(self, msg) -> None:
        self.calls += 1
        if self.failures:
            self.failures -= 1
            raise ChannelError("network down")
        self.sent.append(msg)


def sim_event(ref: str, text: str | None = None, sample: str | None = None) -> SimulatorEvent:
    return SimulatorEvent(sender_ref=ref, text=text, sample=sample)


def failures_audited(store) -> list:
    return store.list_audit(tool="delivery_failed")


# ---------- outbox: retries ----------


def test_retry_schedule():
    assert [retry_delay(n).total_seconds() for n in range(1, 8)] == [
        30,
        60,
        120,
        300,
        600,
        600,
        600,
    ]


def test_red_alert_is_delivered_on_the_third_attempt():
    store, parent, child = family("simulator", PARENT, "telegram", CHILD)
    flaky = FlakyChannel(failures=2)
    svc = service(store, flaky)
    svc.simulator._load_sample = load_sample
    svc.handle("simulator", sim_event(PARENT, "sugar 45"))
    svc.handle("simulator", sim_event(PARENT, "1"))  # confirmed: RED alert, first send fails

    [alert] = store.list_alerts(parent.id)
    assert alert.level == "red"
    [row] = store.list_outbox(alert_id=alert.id)
    assert row.attempts == 1 and row.delivered_at is None and row.level == "red"
    assert row.last_error and "network down" in row.last_error
    assert _delivery(store, alert.id) == "pending"  # dashboard: "Not delivered yet"
    assert _alert(store, alert)["delivery"] == "pending"
    t0 = row.next_attempt_at - timedelta(seconds=30)

    assert svc.flush_outbox(now=t0 + timedelta(seconds=10)) == []  # not due yet: no attempt
    assert flaky.calls == 1

    svc.flush_outbox(now=t0 + timedelta(seconds=31))  # attempt 2 fails, retry after 1 min
    row = store.get_outbox(row.id)
    assert row.attempts == 2 and row.delivered_at is None
    assert row.next_attempt_at == t0 + timedelta(seconds=31 + 60)

    [sent] = svc.flush_outbox(now=t0 + timedelta(seconds=31 + 61))
    row = store.get_outbox(row.id)
    assert row.attempts == 3 and row.delivered_at is not None and row.gave_up_at is None
    assert sent.text.startswith("RED alert for Lakshmi") and flaky.sent == [sent]
    assert _delivery(store, alert.id) == "delivered"
    # every red failure is audited (two here), and nothing is sent twice
    audited = failures_audited(store)
    assert len(audited) == 2 and all(a.actor == "system" for a in audited)
    assert all(a.parent_id == parent.id for a in audited)
    assert svc.flush_outbox(now=t0 + timedelta(hours=1)) == [] and flaky.calls == 3


def test_non_red_message_stops_after_8_attempts_and_is_audited(caplog):
    store, parent, child = family("simulator", PARENT, "telegram", CHILD)
    flaky = FlakyChannel(failures=1000)
    svc = service(store, flaky)
    row = store.add_outbox(OutboxMessage(member_id=child.id, text="Refill request for Lakshmi"))
    now = utcnow()
    for _ in range(MAX_ATTEMPTS + 4):
        svc.flush_outbox(now=now)
        now += timedelta(minutes=11)
    row = store.get_outbox(row.id)
    assert flaky.calls == MAX_ATTEMPTS == 8
    assert row.attempts == 8 and row.gave_up_at is not None and row.delivered_at is None
    [entry] = failures_audited(store)  # only the final failure of a non-red message
    assert '"gave_up": true' in entry.result_json and parent.id == entry.parent_id
    assert any(r.levelname == "ERROR" and "giving up" in r.getMessage() for r in caplog.records)


def test_red_alert_messages_never_give_up():
    store, parent, child = family("simulator", PARENT, "telegram", CHILD)
    flaky = FlakyChannel(failures=1000)
    svc = service(store, flaky)
    row = store.add_outbox(
        OutboxMessage(member_id=child.id, text="RED alert for Lakshmi: x", level="red")
    )
    now = utcnow()
    for _ in range(12):
        svc.flush_outbox(now=now)
        now += timedelta(minutes=11)
    row = store.get_outbox(row.id)
    assert row.attempts == 12 and row.gave_up_at is None and row.next_attempt_at is not None
    assert len(failures_audited(store)) == 12  # every red failure


def test_missing_channel_counts_as_a_failure_not_a_delivery():
    store, parent, child = family("simulator", PARENT, "twilio", "+919822222222")
    svc = service(store)  # no twilio channel configured
    row = store.add_outbox(OutboxMessage(member_id=child.id, text="hello"))
    assert svc.flush_outbox() == []
    row = store.get_outbox(row.id)
    assert row.delivered_at is None and "no twilio channel" in row.last_error


def test_weekly_summary_goes_through_the_outbox():
    store, parent, child = family("simulator", PARENT, "simulator", "+919800000002")
    svc = service(store)
    svc.handle("simulator", sim_event(PARENT, "142/88"))
    svc.handle("simulator", sim_event(PARENT, "1"))
    svc.send_weekly_summary(parent.id)
    [row] = [m for m in store.list_outbox() if m.text.startswith("Weekly summary")]
    assert row.member_id == child.id and row.delivered_at is not None
    assert svc.simulator.feed(child.channel_ref)[-1].text.startswith("Weekly summary")


# ---------- outbox: atomic claims ----------


@pytest.mark.parametrize("separate_connections", [False, True])
def test_concurrent_claims_never_return_the_same_row(tmp_path, separate_connections):
    path = str(tmp_path / "claims.db")
    first = SQLiteStore(path)
    fam = first.add_family(Family(name="F"))
    member = first.add_member(
        Member(family_id=fam.id, role="child", name="C", channel="simulator", channel_ref="+1")
    )
    ids = {
        first.add_outbox(OutboxMessage(member_id=member.id, text=f"m{i}")).id for i in range(300)
    }
    # separate connections behave like two processes sharing the database file
    stores = [first, SQLiteStore(path) if separate_connections else first]
    now, got = utcnow(), [[], []]
    barrier = threading.Barrier(2)

    def worker(i: int) -> None:
        barrier.wait()
        while rows := stores[i].claim_outbox(now, timedelta(minutes=5), limit=7):
            got[i] += [r.id for r in rows]

    threads = [threading.Thread(target=worker, args=(i,)) for i in (0, 1)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not set(got[0]) & set(got[1])
    assert len(got[0]) + len(got[1]) == 300 and set(got[0]) | set(got[1]) == ids
    # leased rows are not claimed again until the lease ends
    assert first.claim_outbox(now + timedelta(minutes=1), timedelta(minutes=5)) == []
    assert len(first.claim_outbox(now + timedelta(minutes=6), timedelta(minutes=5), 500)) == 300


# ---------- inbound dedup ----------


def test_mark_inbound_seen_is_true_only_the_first_time():
    store = SQLiteStore(":memory:")
    assert store.mark_inbound_seen("twilio", "SM1")
    assert not store.mark_inbound_seen("twilio", "SM1")
    assert store.mark_inbound_seen("telegram", "SM1")  # per channel


def test_repeated_twilio_message_sid_is_processed_once(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("PUBLIC_BASE_URL", "https://demo.ngrok.app")
    get_settings.cache_clear()
    try:
        fake = tw_test.FakeTwilio()
        store, parent, child = family("twilio", tw_test.PARENT, "simulator", "+919800000002")
        svc = service(store, tw_test.channel(fake))
        url = "https://demo.ngrok.app/webhooks/twilio"
        with TestClient(create_app(svc, tick_seconds=0)) as client:

            def post(params):
                sig = tw_test.twilio_signature(tw_test.TOKEN, url, params)
                return client.post(
                    "/webhooks/twilio",
                    content=urlencode(params),
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded",
                        "X-Twilio-Signature": sig,
                    },
                )

            params = tw_test.form(Body="142/88", MessageSid="SM0001")
            first, again = post(params), post(params)
            assert first.status_code == again.status_code == 200
            assert first.text == again.text and again.text.endswith("<Response></Response>")
        assert len(store.list_readings(parent.id)) == 1
        assert [m["Body"] for m in fake.messages if m["Body"].startswith("I read")] == [
            fake.messages[0]["Body"]
        ]
    finally:
        get_settings.cache_clear()


def test_repeated_telegram_update_id_is_processed_once_webhook(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("TELEGRAM_MODE", "webhook")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "s3cret")
    get_settings.cache_clear()
    try:
        bot = tg_test.FakeBot()
        store, parent, _ = family("telegram", str(tg_test.USER), "simulator", "+2")
        with TestClient(create_app(service(store, tg_test.channel(bot)), tick_seconds=0)) as c:
            headers = {"X-Telegram-Bot-Api-Secret-Token": "s3cret"}
            for _ in range(2):
                r = c.post(
                    "/webhooks/telegram", json=tg_test.update(42, text="142/88"), headers=headers
                )
                assert r.status_code == 200 and r.json() == {"ok": True}
        assert len(store.list_readings(parent.id)) == 1
        assert sum(m["text"].startswith("I read") for m in bot.sent) == 1
    finally:
        get_settings.cache_clear()


def test_repeated_telegram_update_id_is_processed_once_polling():
    bot = tg_test.FakeBot()
    store, parent, _ = family("telegram", str(tg_test.USER), "simulator", "+2")
    tg = tg_test.channel(bot)
    svc = service(store, tg)
    bot.updates = [tg_test.update(7, text="142/88")]
    tg.poll_once(svc)
    tg.offset = None  # e.g. a restart: Telegram hands the same update out again
    bot.updates = [tg_test.update(7, text="142/88")]
    tg.poll_once(svc)
    assert len(store.list_readings(parent.id)) == 1
    assert sum(m["text"].startswith("I read") for m in bot.sent) == 1


# ---------- per-parent ordering, parallel parents ----------


class SlowReader(MockReader):
    def read_meter(self, image):
        time.sleep(1.0)
        return super().read_meter(image)


def two_parents():
    store = SQLiteStore(":memory:")
    refs = ["+919800000101", "+919800000201"]
    for i, ref in enumerate(refs):
        fam = store.add_family(Family(name=f"Family {'AB'[i]}"))
        store.add_member(
            Member(
                family_id=fam.id,
                role="parent",
                name=f"Parent {'AB'[i]}",
                channel="simulator",
                channel_ref=ref,
                consent_status="granted",
            )
        )
    from app.agent.rule_agent import RuleAgent
    from app.alerts.notifier import StoreNotifier
    from mcp_server.server import create_server

    server = create_server(store, StoreNotifier(lambda: store))
    svc = KhayalService(
        store, SlowReader(), RuleAgent(store, server), [SimulatorChannel(load_sample)]
    )
    return store, svc, refs


def run_all(*jobs) -> None:
    threads = [threading.Thread(target=j) for j in jobs]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)


def test_photos_from_two_parents_are_read_in_parallel():
    store, svc, refs = two_parents()
    photo = "synthetic_bp_normal.jpg"
    start = time.monotonic()
    run_all(*[lambda r=r: svc.handle("simulator", sim_event(r, sample=photo)) for r in refs])
    elapsed = time.monotonic() - start
    assert elapsed < 1.6, f"took {elapsed:.2f}s: parents were serialised"
    for ref in refs:
        [reply] = [i.text for i in svc.simulator.feed(ref) if i.direction == "out"]
        assert reply.startswith("I read")


def test_one_parents_messages_are_processed_in_arrival_order():
    store, svc, refs = two_parents()
    ref = refs[0]
    photo = sim_event(ref, sample="synthetic_bp_normal.jpg")
    yes = sim_event(ref, "1")
    # Places in line are taken on arrival; the second message reaches a worker first.
    first, second = svc.reserve("simulator", photo), svc.reserve("simulator", yes)
    t2 = threading.Thread(target=svc.handle, args=("simulator", yes, second))
    t2.start()
    time.sleep(0.2)  # "1" is waiting for its turn, not answered out of order
    assert [i for i in svc.simulator.feed(ref) if i.direction == "out"] == []
    svc.handle("simulator", photo, first)
    t2.join(timeout=10)
    replies = [i.text for i in svc.simulator.feed(ref) if i.direction == "out"]
    assert replies[0].startswith("I read") and replies[1] != tpl.NOTHING_TO_CONFIRM
    parent = store.find_member_by_channel("simulator", ref)
    [reading] = store.list_readings(parent.id)
    assert reading.status == "confirmed"


def test_ticker_skips_a_busy_parent_without_waiting():
    store, svc, refs = two_parents()
    busy = store.find_member_by_channel("simulator", refs[0])
    with svc.turns.hold(busy.id):
        start = time.monotonic()
        svc.tick()
        svc.run_scheduler()
        assert time.monotonic() - start < 0.5


def test_turns_are_fifo_per_key_and_independent_across_keys():
    turns, order = Turns(), []
    tickets = [turns.reserve("a") for _ in range(5)]

    def run(t):
        with turns.turn(t):
            order.append(t.number)

    run_all(*[lambda t=t: run(t) for t in reversed(tickets)])
    assert order == [0, 1, 2, 3, 4]
    with turns.hold("a"), turns.try_hold("a") as got_a, turns.try_hold("b") as got_b:
        assert not got_a and got_b
    assert turns.wait_idle(1) and not turns.busy()
