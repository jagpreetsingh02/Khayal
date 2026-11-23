"""Scheduler (brief §5.7): reminders, follow-ups, missed doses, low stock, weekly summary,
and the simulator's demo clock ("advance time"). Mock providers only, no network."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app import clock
from app.agent.rule_agent import RuleAgent
from app.alerts.notifier import StoreNotifier
from app.channels.service import KhayalService
from app.channels.simulator import SimulatorChannel
from app.conversation.messages import Incoming
from app.demo import load_sample
from app.main import create_app
from app.reader.mock_reader import MockReader
from app.storage.models import Family, Medicine, Member, Reading, Sharing
from app.storage.sqlite_store import SQLiteStore
from app.voice.calls import MockCallProvider
from app.voice.stt import MockSTT
from app.voice.tts import MockTTS
from mcp_server.server import create_server

IST = ZoneInfo("Asia/Kolkata")
PARENT = "+919800000001"
CHILD = "+919800000002"


_STORE = []  # the current World's store (the shared clock lives there)


def set_clock(hhmm: str, day: int = 0) -> datetime:
    """Put the app clock (this process and the MCP server's view) at today's IST hh:mm."""
    h, m = map(int, hhmm.split(":"))
    today = datetime.now(IST).date() + timedelta(days=day)
    target = datetime(today.year, today.month, today.day, h, m, tzinfo=IST)
    clock.set_shared(_STORE[-1], target - clock.real_now())
    return target


class World:
    def __init__(self, language: str = "hi"):
        self.store = SQLiteStore(":memory:")
        _STORE.append(self.store)
        fam = self.store.add_family(Family(name="Sharma"))
        self.parent = self.store.add_member(
            Member(
                family_id=fam.id,
                role="parent",
                name="Kamla",
                channel="simulator",
                channel_ref=PARENT,
                language=language,
                consent_status="granted",
            )
        )
        self.child = self.store.add_member(
            Member(
                family_id=fam.id,
                role="child",
                name="Ravi",
                channel="simulator",
                channel_ref=CHILD,
                consent_status="granted",
            )
        )
        self.telma = self.store.add_medicine(
            Medicine(
                parent_id=self.parent.id,
                brand="Telma 40",
                generic="Telmisartan",
                strength="40 mg",
                times=["08:00"],
                stock_count=20,
                daily_dose=1,
                confirmed=True,
            )
        )
        self.glycomet = self.store.add_medicine(
            Medicine(
                parent_id=self.parent.id,
                brand="Glycomet 500",
                generic="Metformin",
                strength="500 mg",
                times=["08:00", "20:00"],
                stock_count=40,
                daily_dose=2,
                confirmed=True,
            )
        )
        self.server = create_server(self.store, StoreNotifier(lambda: self.store))
        self.sim = SimulatorChannel(load_sample)
        self.svc = KhayalService(
            self.store,
            MockReader(),
            RuleAgent(self.store, self.server),
            [self.sim],
            tts=MockTTS(),
            stt=MockSTT(),
            calls=MockCallProvider(self.sim),
        )

    def run(self):
        return self.svc.run_scheduler()

    def feed(self, ref: str):
        return [i for i in self.sim.feed(ref) if i.direction == "out"]

    def texts(self, ref: str) -> list[str]:
        return [i.text for i in self.feed(ref)]

    def say(self, text: str):
        return self.svc.handle("simulator", _Event(PARENT, text))

    def tools(self) -> list[tuple[str, str]]:
        return [(e.actor, e.tool) for e in self.store.list_audit()]


class _Event:
    """A minimal simulator event (text only)."""

    def __init__(self, ref: str, text: str):
        self.sender_ref, self.text, self.sample, self.image_b64 = ref, text, None, None
        self.filename = self.client_id = self.audio_b64 = self.audio_mime = None
        self.voice_sample = None


@pytest.fixture
def w():
    world = World()
    yield world
    clock.set_offset(timedelta(0))
    world.store.close()


# ---------- reminders ----------


def test_reminder_at_medicine_time_with_a_hindi_voice_note(w):
    set_clock("08:00")
    w.run()
    [reminder] = w.feed(PARENT)
    assert (
        reminder.text == "8:00 AM: time for Glycomet 500 and Telma 40. Reply 1 when taken, "
        "2 if skipped."
    )
    assert reminder.voice and reminder.voice_mock and reminder.voice_lang == "hi"
    assert "लेने का समय" in reminder.voice_text and "सुबह 8 बजे" in reminder.voice_text
    events = w.store.list_dose_events(w.parent.id, since=set_clock("00:00"))
    assert {e.medicine_id for e in events} == {w.telma.id, w.glycomet.id}
    assert all(e.status == "pending" for e in events)


def test_reminder_is_sent_once(w):
    set_clock("08:00")
    w.run()
    set_clock("08:10")
    w.run()
    assert len(w.feed(PARENT)) == 1


def test_no_reminder_before_time_or_long_after(w):
    set_clock("07:59")
    w.run()
    set_clock("08:31")  # missed window (e.g. the server was down): no late reminder
    w.run()
    assert w.feed(PARENT) == []


def test_no_reminders_for_paused_parent(w):
    w.store.set_consent(w.parent.id, "paused")
    set_clock("08:00")
    w.run()
    assert w.feed(PARENT) == [] and w.store.list_dose_events(w.parent.id) == []


def test_unconfirmed_medicine_gets_no_reminder(w):
    w.store.add_medicine(
        Medicine(parent_id=w.parent.id, brand="X", generic="x", strength="1", times=["09:00"])
    )
    set_clock("09:00")
    w.run()
    assert w.feed(PARENT) == []


# ---------- taken / follow-up / missed ----------


def test_taken_reply_marks_dose_and_uses_stock(w):
    set_clock("08:00")
    w.run()
    set_clock("08:05")
    w.say("1")
    assert w.texts(PARENT)[-1] == "Noted: Glycomet 500 and Telma 40 taken. Thank you."
    assert w.store.get_medicine(w.telma.id).stock_count == 19
    assert w.store.get_medicine(w.glycomet.id).stock_count == 39  # 2/day over 2 times
    set_clock("08:31")
    w.run()
    assert len(w.feed(PARENT)) == 2  # no follow-up after a "taken" reply


def test_one_gentle_follow_up_after_30_minutes(w):
    set_clock("08:00")
    w.run()
    set_clock("08:30")
    w.run()
    set_clock("08:45")
    w.run()
    texts = w.texts(PARENT)
    assert len(texts) == 2
    assert (
        texts[1] == "Did you take Glycomet 500 and Telma 40 (8:00 AM)? Reply 1 if you have "
        "taken it."
    )
    events = w.store.list_dose_events(w.parent.id, since=set_clock("00:00"))
    assert all(e.status == "missed" and e.followed_up_at for e in events)


def test_late_taken_reply_after_follow_up_counts(w):
    set_clock("08:00")
    w.run()
    set_clock("08:30")
    w.run()
    set_clock("08:40")
    w.say("1")
    events = w.store.list_dose_events(w.parent.id, since=set_clock("00:00"))
    assert {e.status for e in events} == {"taken"}


def test_two_missed_doses_send_one_amber_alert(w):
    set_clock("08:00")
    w.run()
    set_clock("08:30")
    w.run()  # Telma + Glycomet missed -> 2 today
    amber = [t for t in w.texts(CHILD) if t.startswith("AMBER alert")]
    assert amber == [
        "AMBER alert for Kamla: 2 medicine reminders were marked missed on "
        + set_clock("08:30").strftime("%d %b")
        + "."
    ]
    set_clock("20:00")
    w.run()
    set_clock("20:30")
    w.run()  # a third missed dose: no second alert today
    assert len([t for t in w.texts(CHILD) if t.startswith("AMBER")]) == 1
    assert not any(c.kind == "voice_call" for c in w.feed(CHILD))  # amber never rings


def test_single_missed_dose_alerts_nobody_and_calls_no_tool(w):
    w.store.set_medicine_times(w.glycomet.id, ["20:00"])
    set_clock("08:00")
    w.run()
    set_clock("08:30")
    w.run()
    assert w.texts(CHILD) == []
    assert ("agent", "alert_family") not in w.tools()


# ---------- low stock ----------


def test_low_stock_requests_refill_through_mcp_never_approves(w):
    w.store.set_medicine_stock(w.telma.id, 5)  # 5 days left
    set_clock("10:00")
    w.run()
    [refill] = w.store.list_refills(w.parent.id)
    assert refill.status == "pending_approval" and refill.qty == 30
    assert ("agent", "request_refill") in w.tools()
    assert not any("approve" in tool for _, tool in w.tools())
    assert any("Refill request for Kamla: Telma 40 x30" in t for t in w.texts(CHILD))
    parent_msg = w.feed(PARENT)[-1]
    assert "running low" in parent_msg.text and parent_msg.voice
    set_clock("10:05")
    w.run()  # pending already: no second request
    assert len(w.store.list_refills(w.parent.id)) == 1


def test_low_stock_refusal_is_tried_once_a_day(w):
    w.store.set_medicine_stock(w.telma.id, 3)
    w.store.set_sharing(Sharing(parent_id=w.parent.id, medicines=False))  # server refuses
    for hhmm in ("10:00", "10:05", "11:00"):
        set_clock(hhmm)
        w.run()
    assert w.store.list_refills(w.parent.id) == []
    assert w.tools().count(("agent", "request_refill")) == 1


# ---------- weekly summary ----------


def _next_sunday_offset() -> int:
    return (6 - datetime.now(IST).weekday()) % 7


def test_weekly_summary_goes_to_family_on_sunday_evening_once(w):
    day = _next_sunday_offset()
    t = set_clock("09:00", day)
    w.store.add_reading(
        Reading(
            parent_id=w.parent.id,
            type="bp",
            systolic=132,
            diastolic=84,
            status="confirmed",
            created_at=t - timedelta(days=1),
        )
    )
    set_clock("18:55", day)
    w.run()
    assert not any(x.startswith("Weekly summary") for x in w.texts(CHILD))
    set_clock("19:00", day)
    w.run()
    set_clock("19:30", day)
    w.run()
    summaries = [x for x in w.texts(CHILD) if x.startswith("Weekly summary for Kamla")]
    assert len(summaries) == 1
    assert ("agent", "weekly_summary") in w.tools()


def test_weekly_summary_respects_sharing(w):
    w.store.set_sharing(Sharing(parent_id=w.parent.id, readings=False))
    set_clock("19:00", _next_sunday_offset())
    w.run()
    assert not any(x.startswith("Weekly summary") for x in w.texts(CHILD))


# ---------- demo clock ----------


def test_advance_time_runs_reminder_then_follow_up(w):
    set_clock("07:50")
    minutes = w.svc.minutes_to_next_reminder()
    assert 10 < minutes <= 12
    w.svc.advance_time(minutes)
    assert "time for Glycomet 500 and Telma 40" in w.texts(PARENT)[-1]
    w.svc.advance_time(30)
    assert w.texts(PARENT)[-1].startswith("Did you take")
    assert w.store.get_kv(clock.OFFSET_KEY)  # shared with the MCP server process


def test_clock_offset_is_shared_through_the_store(w):
    clock.set_offset(timedelta(0))
    clock.advance(w.store, timedelta(hours=2))
    clock.set_offset(timedelta(0))  # e.g. the other process, before syncing
    assert clock.sync_from(w.store) == timedelta(hours=2)
    clock.reset(w.store)
    assert clock.sync_from(w.store) == timedelta(0)


def test_simulator_advance_endpoint(w):
    set_clock("07:58")
    # The offset is negative when the test runs after 07:58 IST, so check it moved forward.
    before = round(clock.offset().total_seconds() / 60)
    with TestClient(create_app(w.svc, tick_seconds=0)) as client:
        w.store.find_member_by_channel("simulator", PARENT)  # demo family exists already
        r = client.post("/simulator/api/advance", json={"to": "next_reminder"})
        assert r.status_code == 200, r.text
        state = client.get("/simulator/api/state").json()
        texts = [i["text"] for i in state["parent"]["feed"] if i["direction"] == "out"]
        assert any("time for" in t for t in texts)
        assert any("later (demo clock)" in (i["text"] or "") for i in state["parent"]["feed"])
        assert state["clock"]["offset_minutes"] >= before + 1
        assert client.post("/simulator/api/advance", json={}).status_code == 422


def test_engine_reminder_needs_consent(w):
    w.store.set_consent(w.parent.id, "revoked")
    assert w.svc.engine.dose_reminder(w.parent.id, [w.telma.id], "08:00") is None


def test_incoming_is_not_confused_by_voice_flag_default(w):
    assert Incoming(channel="simulator", sender_ref=PARENT, text="1").via_voice is False
