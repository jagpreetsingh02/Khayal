"""The durable inbox: persist before the ack, process from the inbox, recover, retry, replay
safely, keep per-parent order, blank and purge payloads."""

import asyncio
import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import clock
from app.channels.base import ChannelError
from app.channels.inbound import Inbound
from app.channels.service import INBOX_LEASE, INBOX_MAX_ATTEMPTS, KhayalService
from app.channels.telegram import TelegramChannel
from app.conversation import templates as tpl
from app.conversation.engine import MESSAGE_MARK, ConversationEngine
from app.main import create_app
from app.storage.base import ConflictError
from app.storage.models import Alert, DoseEvent, InboxMessage, Medicine
from app.storage.sqlite_store import SQLiteStore
from tests import test_telegram as tg_test
from tests.channel_helpers import family, service


class FakeNow:
    """The real clock (leases, retries, retention), under the test's control."""

    def __init__(self) -> None:
        self.now = datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw) -> None:
        self.now += timedelta(**kw)


@pytest.fixture
def now(monkeypatch) -> FakeNow:
    fake = FakeNow()
    monkeypatch.setattr(clock, "real_now", fake)
    return fake


class Crash(BaseException):
    """Stands in for the process dying: not caught by the service like an error is."""


def tg_family(store_path: str | None = None):
    """A parent on Telegram (FakeBot) and a child on the simulator."""
    store, parent, child = family("telegram", str(tg_test.USER), "simulator", "+919800000002")
    if store_path:  # same family, but in a file (a second "process" can open it)
        file_store = SQLiteStore(store_path)
        for fam in [store.get_family(parent.family_id)]:
            file_store.add_family(fam)
        file_store.add_member(parent)
        file_store.add_member(child)
        store = file_store
    bot = tg_test.FakeBot()
    tg = tg_test.channel(bot)
    return store, parent, child, bot, tg


def say(svc: KhayalService, uid: int, text: str) -> InboxMessage | None:
    return svc.accept("telegram", tg_test.update(uid, text=text), str(uid))


def replies(bot) -> list[str]:
    return [m["text"] for m in bot.sent]


def red_alerts(store, parent_id: str) -> list[Alert]:
    return [a for a in store.list_alerts(parent_id) if a.level == "red"]


def drain(inbound: Inbound, start: bool = True) -> None:
    async def main():
        if start:
            await inbound.start()
        await inbound.drain(timeout=20)

    asyncio.run(main())


# ---------- accept = persist, then ack ----------


def test_webhook_writes_the_inbox_before_answering_and_a_new_process_handles_it(
    tmp_path, monkeypatch
):
    """Kill the processor right after the 200: the message is in the inbox, and the next
    start (a new service and Inbound on the same database) processes it exactly once."""
    from app.config import get_settings

    monkeypatch.setenv("TELEGRAM_MODE", "webhook")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "s3cret")
    get_settings.cache_clear()
    db = str(tmp_path / "khayal.db")
    try:
        store, parent, _, bot, tg = tg_family(db)
        # Process 1 dies after answering: its worker never runs.
        monkeypatch.setattr(Inbound, "start", lambda self: asyncio.sleep(0))
        monkeypatch.setattr(Inbound, "drain", lambda self, timeout=30: asyncio.sleep(0))
        with TestClient(create_app(service(store, tg), tick_seconds=0)) as client:
            r = client.post(
                "/webhooks/telegram",
                json=tg_test.update(42, text="142/88"),
                headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"},
            )
            assert r.status_code == 200 and r.json() == {"ok": True}
        [row] = store.list_inbox()
        assert row.status == "pending" and row.external_id == "42"
        assert store.list_readings(parent.id) == [] and bot.sent == []
        store.close()

        # Process 2: a fresh store on the same file, a fresh Inbound.
        monkeypatch.undo()
        store2 = SQLiteStore(db)
        bot2 = tg_test.FakeBot()
        svc2 = service(store2, tg_test.channel(bot2))
        drain(Inbound(svc2))
        drain(Inbound(svc2))  # a third start finds nothing to do
        assert len(store2.list_readings(parent.id)) == 1
        assert sum(t.startswith("I read 142/88") for t in replies(bot2)) == 1
        [row] = store2.list_inbox()
        assert row.status == "done" and row.attempts == 1
    finally:
        get_settings.cache_clear()


def test_a_repeated_id_is_stored_once_and_processed_once():
    store, parent, _, bot, tg = tg_family()
    svc = service(store, tg)
    assert say(svc, 7, "142/88") is not None
    assert say(svc, 7, "142/88") is None  # Telegram / Twilio retry
    svc.run_inbox()
    assert say(svc, 7, "142/88") is None  # also after it was processed
    svc.run_inbox()
    assert len(store.list_inbox()) == 1
    assert len(store.list_readings(parent.id)) == 1
    assert sum(t.startswith("I read") for t in replies(bot)) == 1


def test_a_message_without_an_id_is_still_stored(caplog):
    store, parent, _, bot, tg = tg_family()
    svc = service(store, tg)
    event = tg_test.update(1, text="142/88")
    assert svc.accept("telegram", event, None) is not None
    assert svc.accept("telegram", event, None) is not None  # nothing to compare: kept
    assert "without an id" in caplog.text
    rows = store.list_inbox()
    assert len(rows) == 2 and all(r.external_id is None for r in rows)
    svc.run_inbox()
    assert {r.status for r in store.list_inbox()} == {"done"}


def test_a_failed_write_fails_the_webhook_so_the_channel_retries(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("TELEGRAM_MODE", "webhook")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "s3cret")
    get_settings.cache_clear()
    try:
        store, _, _, _, tg = tg_family()

        def broken(message):
            raise sqlite3.OperationalError("database is locked")

        app = create_app(service(store, tg), tick_seconds=0)
        with TestClient(app, raise_server_exceptions=False) as client:
            monkeypatch.setattr(store, "add_inbox", broken)
            r = client.post(
                "/webhooks/telegram",
                json=tg_test.update(5, text="142/88"),
                headers={"X-Telegram-Bot-Api-Secret-Token": "s3cret"},
            )
        assert r.status_code == 500
    finally:
        get_settings.cache_clear()


def test_poller_moves_its_offset_only_past_stored_updates(monkeypatch):
    store, parent, _, bot, tg = tg_family()
    svc = service(store, tg)
    calls = {"n": 0}
    real_add = store.add_inbox

    def flaky(message):
        calls["n"] += 1
        if calls["n"] == 2:
            raise sqlite3.OperationalError("disk I/O error")
        return real_add(message)

    monkeypatch.setattr(store, "add_inbox", flaky)
    bot.updates = [tg_test.update(10, text="142/88"), tg_test.update(11, text="1")]
    with pytest.raises(sqlite3.OperationalError):
        tg.poll_once(svc)
    assert tg.offset == 11  # update 11 was not stored: it is fetched again
    bot.updates = [tg_test.update(11, text="1")]
    tg.poll_once(svc)
    assert tg.offset == 12
    [reading] = store.list_readings(parent.id)
    assert reading.status == "confirmed"


# ---------- recovery ----------


def test_a_message_left_processing_goes_back_to_pending_when_its_lease_expires(now):
    store, parent, _, bot, tg = tg_family()
    svc = service(store, tg)
    say(svc, 1, "142/88")
    [claimed] = store.claim_inbox(now(), INBOX_LEASE)  # a worker took it, then died
    assert claimed.status == "processing"

    drain(Inbound(svc))  # lease still running: not ours to take
    assert store.get_inbox(claimed.id).status == "processing"
    assert store.list_readings(parent.id) == []

    now.advance(seconds=INBOX_LEASE.total_seconds() + 1)
    drain(Inbound(svc))
    row = store.get_inbox(claimed.id)
    assert row.status == "done" and row.attempts == 2
    assert len(store.list_readings(parent.id)) == 1
    assert sum(t.startswith("I read") for t in replies(bot)) == 1


def test_a_running_worker_renews_its_leases(now):
    store, *_ = tg_family()
    svc = service(store, tg_test.channel(tg_test.FakeBot()))
    say(svc, 1, "142/88")
    [row] = store.claim_inbox(now(), INBOX_LEASE)
    inbound = Inbound(svc)
    now.advance(seconds=50)
    inbound._maintain([row.id])  # the worker still holds it: lease extended
    now.advance(seconds=50)
    assert store.release_expired_inbox(now()) == 0
    now.advance(seconds=INBOX_LEASE.total_seconds())
    assert store.release_expired_inbox(now()) == 1


# ---------- failures ----------


class BrokenTelegram(TelegramChannel):
    """Every download fails (e.g. Telegram's file API is down)."""

    def receive(self, update):
        raise ChannelError("Telegram photo download failed: HTTP 502")


def test_failures_retry_with_backoff_then_fail_for_good_with_an_audit_entry(now):
    store, parent, _, bot, _ = tg_family()
    tg = BrokenTelegram(tg_test.TOKEN, client=tg_test.channel(bot)._client)
    svc = service(store, tg)
    say(svc, 3, "142/88")
    waits = []
    for attempt in range(1, INBOX_MAX_ATTEMPTS + 1):
        assert svc.run_inbox() == 1
        row = store.list_inbox()[0]
        assert row.attempts == attempt and row.last_error.startswith("ChannelError")
        if attempt < INBOX_MAX_ATTEMPTS:
            assert row.status == "pending"
            waits.append((row.next_attempt_at - now()).total_seconds())
            assert svc.run_inbox() == 0  # not due yet
            now.now = row.next_attempt_at
    assert waits == [5, 30, 120, 600]
    row = store.list_inbox()[0]
    assert row.status == "failed" and row.finished_at is not None
    assert row.payload is not None  # kept for inspection
    now.advance(days=1)
    assert svc.run_inbox() == 0  # never picked up again
    [audit] = store.list_audit(tool="inbound_failed")
    assert audit.parent_id == parent.id
    assert '"attempts": 5' in audit.result_json and row.id in audit.args_json
    assert "142/88" not in audit.args_json + audit.result_json


def test_errors_in_the_inbox_and_audit_log_carry_no_phone_numbers():
    from app.channels.service import scrub_error

    assert scrub_error("ChannelError: no route to +919800000001") == (
        "ChannelError: no route to [number]"
    )


# ---------- per-parent order ----------


class SlowFirst(TelegramChannel):
    """The first message takes a while to receive (a slow photo download)."""

    def receive(self, update):
        if update["update_id"] == 1:
            time.sleep(0.6)
        return super().receive(update)


def test_one_parents_messages_keep_their_order_through_the_worker():
    store, parent, _, bot, _ = tg_family()
    tg = SlowFirst(tg_test.TOKEN, client=tg_test.channel(bot)._client)
    svc = service(store, tg)

    async def main():
        inbound = Inbound(svc)
        await inbound.start()
        await inbound.accept("telegram", tg_test.update(1, text="142/88"), "1")
        await inbound.accept("telegram", tg_test.update(2, text="1"), "2")
        await inbound.drain(timeout=20)

    asyncio.run(main())
    texts = replies(bot)
    assert texts[0].startswith("I read 142/88") and texts[1] != tpl.NOTHING_TO_CONFIRM
    [reading] = store.list_readings(parent.id)
    assert reading.status == "confirmed"


def test_two_parents_run_in_parallel_through_the_worker():
    store, parent, child, bot, _ = tg_family()
    from app.storage.models import Member

    other = store.add_member(
        Member(
            family_id=parent.family_id,
            role="parent",
            name="Suresh",
            channel="telegram",
            channel_ref="555002",
            consent_status="granted",
        )
    )

    class Slow(TelegramChannel):
        def receive(self, update):
            time.sleep(0.6)
            return super().receive(update)

    svc = service(store, Slow(tg_test.TOKEN, client=tg_test.channel(bot)._client))
    second = tg_test.update(2, text="150/90")
    second["message"]["from"]["id"] = second["message"]["chat"]["id"] = 555002

    async def main():
        inbound = Inbound(svc)
        await inbound.start()
        start = time.monotonic()
        await inbound.accept("telegram", tg_test.update(1, text="142/88"), "1")
        await inbound.accept("telegram", second, "2")
        await inbound.drain(timeout=20)
        return time.monotonic() - start

    elapsed = asyncio.run(main())
    assert elapsed < 1.1, f"took {elapsed:.2f}s: parents were serialised"
    assert len(store.list_readings(parent.id)) == len(store.list_readings(other.id)) == 1


# ---------- privacy ----------


def test_payload_is_blanked_when_done_and_rows_are_purged_after_retention(now, monkeypatch):
    store, parent, _, bot, tg = tg_family()
    svc = service(store, tg)
    say(svc, 1, "142/88")
    svc.run_inbox()
    [row] = store.list_inbox()
    assert row.status == "done" and row.finished_at is not None
    assert row.payload is None and row.snapshot is None
    assert (row.channel, row.external_id) == ("telegram", "1")
    with store._lock:  # nothing of the message is left in the row
        raw = store._conn.execute("SELECT * FROM inbox").fetchone()
    assert str(tg_test.USER) not in str(tuple(raw)) and "142/88" not in str(tuple(raw))

    store.add_inbox(InboxMessage(channel="telegram", payload={"event": {}}, received_at=now()))
    monkeypatch.setenv("INBOX_RETENTION_DAYS", "3")
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        now.advance(days=3, hours=1)
        Inbound(svc)._maintain([], purge=True)
    finally:
        get_settings.cache_clear()
    rows = store.list_inbox()
    assert [r.status for r in rows] == ["pending"]  # unprocessed messages are never purged
    assert say(svc, 1, "142/88") is not None  # past retention the id is forgotten


def test_payloads_are_never_logged(caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    store, parent, _, bot, tg = tg_family()
    svc = service(store, tg)
    say(svc, 1, "142/88")
    say(svc, 1, "142/88")
    svc.accept("telegram", tg_test.update(2, text="150/95"), None)
    svc.run_inbox()
    assert "142/88" not in caplog.text and "150/95" not in caplog.text
    assert str(tg_test.USER) not in caplog.text


# ---------- replays ----------


def crash_once(monkeypatch, cls, name: str):
    """Make cls.name raise Crash the first time it is called (the process dies there)."""
    real = getattr(cls, name)
    state = {"done": False}

    def wrapper(self, *args, **kwargs):
        if not state["done"]:
            state["done"] = True
            raise Crash()
        return real(self, *args, **kwargs)

    monkeypatch.setattr(cls, name, wrapper)


def run_crashing(svc: KhayalService) -> None:
    [(row, ticket)] = svc.claim_inbox()
    with pytest.raises(Crash):
        svc.process(row, ticket)
    assert svc.store.get_inbox(row.id).status == "processing"


def restart(svc: KhayalService, now: FakeNow) -> None:
    now.advance(seconds=INBOX_LEASE.total_seconds() + 1)
    drain(Inbound(svc))


def test_replay_after_a_reading_was_stored_does_not_store_a_second_one(now, monkeypatch):
    store, parent, _, bot, tg = tg_family()
    svc = service(store, tg)
    say(svc, 1, "142/88")
    # Dies after the reading and the read-back state were written, before the reply.
    crash_once(monkeypatch, KhayalService, "deliver")
    run_crashing(svc)
    [first] = store.list_readings(parent.id)
    restart(svc, now)
    [again] = store.list_readings(parent.id)
    assert again.id == first.id and again.status == "unconfirmed"
    state = store.get_conversation_state(parent.id)
    assert state.state == "awaiting_confirmation" and state.payload["reading_id"] == first.id
    assert replies(bot) == [tpl.read_back("142/88")]
    say(svc, 2, "1")
    svc.run_inbox()
    [reading] = store.list_readings(parent.id)
    assert reading.status == "confirmed"


def test_replay_of_a_red_confirmation_does_not_alert_twice(now, monkeypatch):
    store, parent, child, bot, tg = tg_family()
    svc = service(store, tg)
    say(svc, 1, "sugar 45")
    svc.run_inbox()
    say(svc, 2, "1")
    # Dies after alert_family wrote the RED alert, before the conversation moved on.
    crash_once(monkeypatch, ConversationEngine, "_ask_symptoms")
    run_crashing(svc)
    assert len(red_alerts(store, parent.id)) == 1
    restart(svc, now)
    [alert] = red_alerts(store, parent.id)
    family_msgs = [m for m in store.list_outbox(alert_id=alert.id) if m.kind == "message"]
    assert len(family_msgs) == 1  # one child, one message
    feed = [i.text for i in svc.simulator.feed(child.channel_ref)]
    assert sum(t.startswith("RED alert") for t in feed) == 1
    assert len([m for m in store.list_outbox(alert_id=alert.id) if m.kind == "voice_call"]) <= 1
    assert store.get_conversation_state(parent.id).state == "awaiting_symptoms"
    texts = replies(bot)
    assert "I have told your family." in texts[-2] and texts[-1] == tpl.LOW_SUGAR_QUESTION
    [reading] = store.list_readings(parent.id)
    assert reading.status == "confirmed"


def test_replay_of_a_severe_repeat_answer_does_not_alert_twice(now, monkeypatch):
    """Severe BP: 1 -> warning signs? 2 -> repeat 5 min later -> 1 -> 2 = RED. The last "2"
    dies right after the RED alert; the replay sends nothing new to the family."""
    store, parent, child, bot, tg = tg_family()
    svc = service(store, tg)
    uid = iter(range(1, 100))
    for text in ("185/125", "1", "2"):
        say(svc, next(uid), text)
        svc.run_inbox()
    svc.skip_time(parent.id, 6)
    for text in ("186/124", "1"):
        say(svc, next(uid), text)
        svc.run_inbox()
    assert store.get_conversation_state(parent.id).state == "awaiting_symptoms"
    say(svc, next(uid), "2")
    crash_once(monkeypatch, KhayalService, "deliver")  # everything written, no reply yet
    run_crashing(svc)
    assert len(red_alerts(store, parent.id)) == 1
    restart(svc, now)
    [alert] = red_alerts(store, parent.id)
    assert alert.rule == "bp_severe_repeat"
    assert len([m for m in store.list_outbox(alert_id=alert.id) if m.kind == "message"]) == 1
    assert store.get_conversation_state(parent.id).state == "idle"
    audits = store.list_audit(tool="conversation:symptom_check", parent_id=parent.id)
    assert len(audits) == 2  # one per question asked, not one per attempt


def test_replay_of_a_dose_answer_does_not_use_stock_twice(now, monkeypatch):
    store, parent, _, bot, tg = tg_family()
    svc = service(store, tg)
    from app.storage.models import utcnow

    med = store.add_medicine(
        Medicine(
            parent_id=parent.id,
            brand="Telma 40",
            generic="telmisartan",
            strength="40 mg",
            times=["08:00"],
            stock_count=20,
            daily_dose=1,
            confirmed=True,
        )
    )
    store.add_dose_event(DoseEvent(medicine_id=med.id, due_at=utcnow() - timedelta(minutes=5)))
    say(svc, 1, "1")
    crash_once(monkeypatch, KhayalService, "deliver")  # after the dose was marked
    run_crashing(svc)
    assert store.get_medicine(med.id).stock_count == 19
    restart(svc, now)
    assert store.get_medicine(med.id).stock_count == 19
    assert replies(bot) == [tpl.DOSE_TAKEN.format(names="Telma 40")]


def test_replay_of_consent_yes_is_not_taken_as_a_dose_answer(now, monkeypatch):
    store, parent, _, bot, tg = tg_family()
    store.set_consent(parent.id, "pending")
    svc = service(store, tg)
    say(svc, 1, "1")
    crash_once(monkeypatch, KhayalService, "deliver")
    run_crashing(svc)
    assert store.get_member(parent.id).consent_status == "granted"
    restart(svc, now)
    assert replies(bot) == [tpl.CONSENT_THANKS]


def test_a_retry_after_newer_messages_does_not_roll_their_state_back(now, monkeypatch):
    """A message that failed with an error, retried after a later message from the same
    parent was handled, runs against the newer state (the saved start is stale)."""
    store, parent, _, bot, tg = tg_family()
    svc = service(store, tg)
    say(svc, 1, "142/88")
    crash_once(monkeypatch, KhayalService, "deliver")
    [(row, ticket)] = svc.claim_inbox()
    with pytest.raises(Crash):
        svc.process(row, ticket)
    store.fail_inbox(row.id, "boom", now() + timedelta(seconds=5), now())  # as if it errored
    say(svc, 2, "1")  # handled first: confirms the reading
    svc.run_inbox()
    now.advance(seconds=6)
    svc.run_inbox()  # the retry
    [reading] = store.list_readings(parent.id)
    assert reading.status == "confirmed"
    state = store.get_conversation_state(parent.id)
    assert state.state == "idle" and state.payload.get(MESSAGE_MARK) == row.id
    assert len(replies(bot)) == 1  # only the "1"'s answer: the stale retry adds nothing


# ---------- shutdown, health, storage ----------


def test_clean_shutdown_drains_everything_accepted():
    store, parent, _, bot, tg = tg_family()
    svc = service(store, tg)

    async def main():
        inbound = Inbound(svc, poll_every=60)  # only accept() wakes it
        await inbound.start()
        for uid, text in enumerate(["142/88", "1", "sugar 110", "1"], start=1):
            await inbound.accept("telegram", tg_test.update(uid, text=text), str(uid))
        await inbound.drain(timeout=20)

    asyncio.run(main())
    assert [r.status for r in store.list_inbox()] == ["done"] * 4
    assert {r.status for r in store.list_readings(parent.id)} == {"confirmed"}


def test_health_reports_the_inbox_and_is_degraded_when_a_message_waits(now):
    store, *_ = tg_family()
    svc = service(store, tg_test.channel(tg_test.FakeBot()))
    store.add_inbox(
        InboxMessage(
            channel="telegram",
            payload={"event": {}},
            received_at=now() - timedelta(minutes=6),
            next_attempt_at=now() + timedelta(hours=1),  # waiting for a retry
        )
    )
    with TestClient(create_app(svc, tick_seconds=0)) as client:
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "degraded" and body["database"] == "ok"
        assert body["inbox"]["pending"] == 1 and body["inbox"]["oldest_pending_s"] >= 360
        with store._lock, store._conn:
            store._conn.execute("UPDATE inbox SET status = 'done'")
        body = client.get("/health").json()
        assert body["status"] == "ok" and body["inbox"]["oldest_pending_s"] is None


def test_old_inbound_seen_rows_move_into_the_inbox_on_open(tmp_path):
    db = str(tmp_path / "old.db")
    store = SQLiteStore(db)
    with store._lock, store._conn:
        store._conn.execute(
            "INSERT INTO inbound_seen VALUES ('twilio', 'SM1', '2026-11-24T10:00:00.000000Z')"
        )
    store.close()
    store = SQLiteStore(db)
    [row] = store.list_inbox()
    assert (row.channel, row.external_id, row.status, row.payload) == ("twilio", "SM1", "done",
                                                                       None)  # fmt: skip
    assert not store.mark_inbound_seen("twilio", "SM1")  # still a known id
    with store._lock:
        assert store._conn.execute("SELECT COUNT(*) FROM inbound_seen").fetchone()[0] == 0
    store.close()
    assert len(SQLiteStore(db).list_inbox()) == 1  # opening again moves nothing twice


def test_the_store_refuses_a_second_alert_for_the_same_reading_and_rule():
    store, parent, *_ = tg_family()
    alert = Alert(parent_id=parent.id, level="red", reason="x", reading_id="rd_1", rule="r")
    store.add_alert(alert)
    with pytest.raises(ConflictError):
        store.add_alert(alert.model_copy(update={"id": "al_other"}))
    store.add_alert(Alert(parent_id=parent.id, level="amber", reason="no reading"))
    store.add_alert(Alert(parent_id=parent.id, level="amber", reason="no reading"))


def test_concurrent_claims_never_return_the_same_inbox_row(tmp_path):
    db = str(tmp_path / "claims.db")
    SQLiteStore(db).close()
    stores = [SQLiteStore(db) for _ in range(4)]
    for i in range(40):
        stores[0].add_inbox(
            InboxMessage(channel="telegram", external_id=str(i), payload={"event": {}},
                         received_at=datetime.now(UTC))
        )  # fmt: skip
    got: list[str] = []
    lock = threading.Lock()

    def claim(s):
        while rows := s.claim_inbox(datetime.now(UTC), INBOX_LEASE, limit=3):
            with lock:
                got.extend(r.id for r in rows)

    threads = [threading.Thread(target=claim, args=(s,)) for s in stores]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    assert len(got) == 40 and len(set(got)) == 40
