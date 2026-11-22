import asyncio
import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from app.alerts.notifier import OutboxNotifier
from app.storage.models import DoseEvent, Family, Medicine, Member, Reading, utcnow
from app.storage.sqlite_store import SQLiteStore
from mcp_server import summary as summary_mod
from mcp_server.guardrails import blocked_terms
from mcp_server.identity import sender_meta
from mcp_server.server import create_server

PARENT_REF = "+919800000001"
OTHER_PARENT_REF = "+919800000009"
CHILD_REF = "+919800000002"
EXPECTED_TOOLS = {
    "log_reading",
    "get_history",
    "get_meds",
    "create_reminder",
    "alert_family",
    "request_refill",
    "weekly_summary",
}


class World:
    def __init__(self):
        self.store = SQLiteStore(":memory:")
        self.outbox = OutboxNotifier()
        self.server = create_server(self.store, self.outbox)
        fam = self.store.add_family(Family(name="Sharma"))
        self.parent = self.store.add_member(
            Member(
                family_id=fam.id,
                role="parent",
                name="Kamla",
                channel="simulator",
                channel_ref=PARENT_REF,
                consent_status="granted",
            )
        )
        self.children = [
            self.store.add_member(
                Member(family_id=fam.id, role="child", name=n, channel="simulator", channel_ref=ref)
            )
            for n, ref in (("Ravi", CHILD_REF), ("Priya", "+919800000003"))
        ]
        other_fam = self.store.add_family(Family(name="Iyer"))
        self.other_parent = self.store.add_member(
            Member(
                family_id=other_fam.id,
                role="parent",
                name="Lakshmi",
                channel="simulator",
                channel_ref=OTHER_PARENT_REF,
            )
        )
        self.telma = self.store.add_medicine(
            Medicine(
                parent_id=self.parent.id,
                brand="Telma 40",
                generic="Telmisartan",
                strength="40 mg",
                times=["08:00"],
                stock_count=5,
                daily_dose=1,
                confirmed=True,
            )
        )
        self.unconfirmed_med = self.store.add_medicine(
            Medicine(
                parent_id=self.parent.id,
                brand="Glycomet 500",
                generic="Metformin",
                strength="500 mg",
                stock_count=20,
                daily_dose=2,
                confirmed=False,
            )
        )
        self.schedule_x = self.store.add_medicine(
            Medicine(
                parent_id=self.parent.id,
                brand="SchedX",
                generic="example",
                strength="1 mg",
                stock_count=2,
                daily_dose=1,
                is_schedule_x=True,
                confirmed=True,
            )
        )
        self.other_med = self.store.add_medicine(
            Medicine(
                parent_id=self.other_parent.id,
                brand="Amlong 5",
                generic="Amlodipine",
                strength="5 mg",
                daily_dose=1,
                confirmed=True,
            )
        )

    def call(self, tool, args=None, sender=PARENT_REF, channel="simulator"):
        async def run():
            async with Client(self.server) as c:
                meta = sender_meta(channel, sender) if sender else None
                result = await c.call_tool(tool, args or {}, meta=meta)
                return result.structured_content

        return asyncio.run(run())

    def confirmed_reading(self, **kw) -> Reading:
        r = self.store.add_reading(Reading(parent_id=self.parent.id, **kw))
        return self.store.set_reading_status(r.id, "confirmed")


@pytest.fixture
def w():
    world = World()
    yield world
    world.store.close()


def tool_names(server) -> set[str]:
    async def run():
        async with Client(server) as c:
            return {t.name: t for t in await c.list_tools()}

    return asyncio.run(run())


# ---------- tool surface ----------


def test_exposes_exactly_the_spec_tools(w):
    assert set(tool_names(w.server)) == EXPECTED_TOOLS


def test_no_approve_refill_or_confirm_tool(w):
    names = set(tool_names(w.server))
    assert "approve_refill" not in names
    assert not any("approve" in n or "confirm" in n for n in names)


def test_no_tool_accepts_a_parent_or_member_id(w):
    for tool in tool_names(w.server).values():
        props = set(tool.input_schema.get("properties", {}))
        assert not props & {"parent_id", "member_id", "family_id", "sender", "ctx"}, tool.name


# ---------- identity (parent_id from server context) ----------


def test_unknown_or_missing_sender_is_refused(w):
    with pytest.raises(ToolError, match="Unknown sender"):
        w.call("get_meds", sender=None)
    with pytest.raises(ToolError, match="Unknown sender"):
        w.call("get_meds", sender="+910000000000")
    with pytest.raises(ToolError, match="Unknown sender"):
        w.call("get_meds", channel="telegram")  # same number, different channel


def test_child_sender_cannot_act_as_parent(w):
    with pytest.raises(ToolError, match="not a parent"):
        w.call("get_meds", sender=CHILD_REF)


def test_extra_parent_id_argument_is_rejected(w):
    with pytest.raises(ToolError):
        w.call("get_meds", {"parent_id": w.other_parent.id})


def test_parent_sees_only_own_data(w):
    meds = w.call("get_meds")["medicines"]
    assert w.other_med.id not in {m["id"] for m in meds}
    with pytest.raises(ToolError, match="not found"):
        w.call("request_refill", {"med_id": w.other_med.id, "qty": 10})
    other_r = w.store.add_reading(
        Reading(parent_id=w.other_parent.id, type="glucose", glucose_mgdl=40, status="confirmed")
    )
    with pytest.raises(ToolError, match="not found"):
        w.call("alert_family", {"level": "red", "reason": "low", "reading_id": other_r.id})


# ---------- log_reading ----------


def test_log_reading_stores_unconfirmed_with_read_back(w):
    out = w.call(
        "log_reading",
        {
            "type": "bp",
            "values": {"systolic": 142, "diastolic": 88, "pulse": 76},
            "confidence": 0.93,
            "image_ref": "img_1",
        },
    )
    assert out["stored"] and out["status"] == "unconfirmed"
    assert out["read_back"].startswith("I read 142/88")
    assert "Reply 1 yes / 2 no" in out["read_back"]
    stored = w.store.get_reading(out["reading_id"])
    assert stored.parent_id == w.parent.id and stored.status == "unconfirmed"
    assert stored.confidence == 0.93 and stored.image_ref == "img_1"


def test_log_reading_converts_mmol(w):
    out = w.call(
        "log_reading",
        {"type": "glucose", "values": {"glucose_value": 7.0, "glucose_unit": "mmol/L"}},
    )
    assert w.store.get_reading(out["reading_id"]).glucose_mgdl == 126.0


def test_log_reading_rejects_implausible_without_storing(w):
    out = w.call("log_reading", {"type": "bp", "values": {"systolic": 80, "diastolic": 95}})
    assert out["stored"] is False and out["problems"] and "ask_retake" in out
    out = w.call("log_reading", {"type": "glucose", "values": {"glucose_value": 900}})
    assert out["stored"] is False
    assert w.store.list_readings(w.parent.id) == []


def test_log_reading_flags_unusual_vs_history(w):
    for s in (128, 132, 130):
        w.confirmed_reading(type="bp", systolic=s, diastolic=82)
    out = w.call("log_reading", {"type": "bp", "values": {"systolic": 215, "diastolic": 84}})
    assert out["stored"] and out["unusual_vs_history"]


# ---------- get_history / get_meds ----------


def test_get_history_returns_confirmed_only_in_range(w):
    w.confirmed_reading(type="bp", systolic=130, diastolic=80)
    w.confirmed_reading(type="glucose", glucose_mgdl=110, created_at=utcnow() - timedelta(days=10))
    w.store.add_reading(Reading(parent_id=w.parent.id, type="bp", systolic=150, diastolic=90))
    week = w.call("get_history", {"range": "7d"})
    assert week["count"] == 1 and week["readings"][0]["systolic"] == 130
    assert w.call("get_history", {"range": "30d"})["count"] == 2
    assert w.call("get_history", {"range": "30d", "type": "glucose"})["count"] == 1
    assert "+05:30" in week["readings"][0]["time"]


def test_get_meds_lists_days_left(w):
    meds = {m["brand"]: m for m in w.call("get_meds")["medicines"]}
    assert meds["Telma 40"]["days_left"] == 5
    assert meds["Glycomet 500"]["days_left"] == 10


# ---------- create_reminder ----------


def test_create_reminder_for_confirmed_medicine(w):
    out = w.call("create_reminder", {"med_id": w.telma.id, "times": ["20:00", "08:00", "08:00"]})
    assert out["times"] == ["08:00", "20:00"] and out["timezone"] == "Asia/Kolkata"
    assert w.store.get_medicine(w.telma.id).times == ["08:00", "20:00"]


def test_create_reminder_refuses_unconfirmed_medicine(w):
    with pytest.raises(ToolError, match="confirmed"):
        w.call("create_reminder", {"med_id": w.unconfirmed_med.id, "times": ["08:00"]})


@pytest.mark.parametrize("times", [["8am"], ["24:00"], ["07:60"], []])
def test_create_reminder_validates_times(w, times):
    with pytest.raises(ToolError):
        w.call("create_reminder", {"med_id": w.telma.id, "times": times})


# ---------- alert_family ----------


def test_no_alert_before_parent_confirms(w):
    out = w.call("log_reading", {"type": "glucose", "values": {"glucose_value": 45}})
    with pytest.raises(ToolError, match="confirms"):
        w.call(
            "alert_family",
            {"level": "red", "reason": "low glucose", "reading_id": out["reading_id"]},
        )
    assert w.outbox.outbox == []


def test_red_glucose_alert_notifies_every_child(w):
    r = w.confirmed_reading(type="glucose", glucose_mgdl=45)
    out = w.call("alert_family", {"level": "red", "reason": "glucose 45", "reading_id": r.id})
    assert out["sent"] and out["level"] == "red"
    assert set(out["notified_member_ids"]) == {c.id for c in w.children}
    assert all("108/112" in m.text for m in w.outbox.outbox)
    assert out["voice_call"] is False  # VOICE=none in mock mode
    assert w.store.list_alerts(w.parent.id)[0].reading_id == r.id


def test_red_triggers_voice_call_when_enabled(w, monkeypatch):
    monkeypatch.setenv("VOICE", "elevenlabs")
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        r = w.confirmed_reading(type="glucose", glucose_mgdl=45)
        out = w.call("alert_family", {"level": "red", "reason": "glucose 45", "reading_id": r.id})
        assert out["voice_call"] is True
        assert {m.kind for m in w.outbox.outbox} == {"message", "voice_call"}
    finally:
        monkeypatch.delenv("VOICE")
        get_settings.cache_clear()


def test_alert_refused_when_no_limit_crossed(w):
    r = w.confirmed_reading(type="glucose", glucose_mgdl=110)
    with pytest.raises(ToolError, match="No limit"):
        w.call("alert_family", {"level": "amber", "reason": "check", "reading_id": r.id})


def test_llm_cannot_escalate_amber_to_red(w):
    r = w.confirmed_reading(type="glucose", glucose_mgdl=60)
    with pytest.raises(ToolError, match="evaluated amber"):
        w.call("alert_family", {"level": "red", "reason": "glucose 60", "reading_id": r.id})


def test_first_severe_bp_is_amber_then_repeat_goes_red(w):
    first = w.confirmed_reading(
        type="bp", systolic=184, diastolic=98, created_at=utcnow() - timedelta(minutes=6)
    )
    out = w.call("alert_family", {"level": "amber", "reason": "bp", "reading_id": first.id})
    assert out["level"] == "amber" and out["ask_repeat"] is True
    repeat = w.confirmed_reading(type="bp", systolic=182, diastolic=97)
    # even if the agent asks for amber, a red evaluation is sent as red
    out = w.call("alert_family", {"level": "amber", "reason": "bp", "reading_id": repeat.id})
    assert out["level"] == "red"


def test_amber_is_rate_capped_but_red_is_not(w):
    for _ in range(3):
        r = w.confirmed_reading(type="glucose", glucose_mgdl=60)
        assert w.call("alert_family", {"level": "amber", "reason": "g", "reading_id": r.id})["sent"]
    r = w.confirmed_reading(type="glucose", glucose_mgdl=60)
    capped = w.call("alert_family", {"level": "amber", "reason": "g", "reading_id": r.id})
    assert capped["sent"] is False and capped["suppressed"] is True
    red = w.confirmed_reading(type="glucose", glucose_mgdl=40)
    assert w.call("alert_family", {"level": "red", "reason": "g", "reading_id": red.id})["sent"]


def test_same_reading_is_not_alerted_twice(w):
    r = w.confirmed_reading(type="glucose", glucose_mgdl=40)
    first = w.call("alert_family", {"level": "red", "reason": "g", "reading_id": r.id})
    again = w.call("alert_family", {"level": "red", "reason": "g", "reading_id": r.id})
    assert again == {"sent": False, "already_sent": True, "alert_id": first["alert_id"]}


def test_alert_reason_with_diagnosis_words_is_refused(w):
    r = w.confirmed_reading(type="glucose", glucose_mgdl=40)
    with pytest.raises(ToolError, match="without diagnosis"):
        w.call(
            "alert_family",
            {"level": "red", "reason": "Her diabetes is worsening", "reading_id": r.id},
        )


def test_family_never_sees_llm_reason_text(w):
    r = w.confirmed_reading(type="glucose", glucose_mgdl=40)
    w.call("alert_family", {"level": "red", "reason": "SECRET-AGENT-NOTE", "reading_id": r.id})
    assert all("SECRET-AGENT-NOTE" not in m.text for m in w.outbox.outbox)


def test_missed_doses_alert_without_reading(w):
    with pytest.raises(ToolError, match="No limit"):
        w.call("alert_family", {"level": "amber", "reason": "missed"})
    now_ist = datetime.now(ZoneInfo("Asia/Kolkata"))
    start = now_ist.replace(hour=0, minute=30, second=0, microsecond=0)
    for h in (0, 1):
        w.store.add_dose_event(
            DoseEvent(medicine_id=w.telma.id, due_at=start + timedelta(hours=h), status="missed")
        )
    out = w.call("alert_family", {"level": "amber", "reason": "missed"})
    assert out["sent"] and out["rule"] == "missed_doses_per_day"
    with pytest.raises(ToolError):
        w.call("alert_family", {"level": "red", "reason": "missed"})


def test_per_parent_threshold_changes_alerting(w):
    r = w.confirmed_reading(type="glucose", glucose_mgdl=230)
    with pytest.raises(ToolError, match="No limit"):
        w.call("alert_family", {"level": "amber", "reason": "high", "reading_id": r.id})
    w.store.set_threshold(w.parent.id, "glucose_amber_above", 220)
    assert w.call("alert_family", {"level": "amber", "reason": "high", "reading_id": r.id})["sent"]


# ---------- request_refill ----------


def test_request_refill_creates_pending_approval_only(w):
    out = w.call("request_refill", {"med_id": w.telma.id, "qty": 30})
    assert out["status"] == "pending_approval"
    refill = w.store.get_refill(out["refill_id"])
    assert refill.status == "pending_approval" and refill.requested_by == "agent"
    assert refill.decided_by_member_id is None


def test_refill_quantity_cap(w):
    with pytest.raises(ToolError, match="one month"):
        w.call("request_refill", {"med_id": w.telma.id, "qty": 31})
    # cap scales with daily dose: 2/day -> 60
    assert w.call("request_refill", {"med_id": w.unconfirmed_med.id, "qty": 60})
    with pytest.raises(ToolError):
        w.call("request_refill", {"med_id": w.telma.id, "qty": 0})


def test_refill_schedule_x_excluded(w):
    with pytest.raises(ToolError, match="Schedule X"):
        w.call("request_refill", {"med_id": w.schedule_x.id, "qty": 1})
    assert w.store.list_refills(w.parent.id) == []


def test_refill_no_duplicate_pending(w):
    w.call("request_refill", {"med_id": w.telma.id, "qty": 10})
    with pytest.raises(ToolError, match="already waiting"):
        w.call("request_refill", {"med_id": w.telma.id, "qty": 10})


# ---------- weekly_summary ----------


def test_weekly_summary_is_descriptive(w):
    for s, d in ((128, 80), (146, 92), (150, 94)):
        w.confirmed_reading(type="bp", systolic=s, diastolic=d)
    w.confirmed_reading(type="glucose", glucose_mgdl=96)
    w.confirmed_reading(type="glucose", glucose_mgdl=260)
    out = w.call("weekly_summary")
    text = out["text"]
    assert out["filtered"] is False
    assert "BP: 3 readings, from 128/80 to 150/94. 2 of 3 at or above the limit" in text
    assert "Glucose: 2 readings, from 96 to 260 mg/dL" in text
    assert blocked_terms(text) == []


def test_weekly_summary_filter_blocks_diagnosis_and_dosing(w, monkeypatch):
    monkeypatch.setattr(
        "mcp_server.server.build_summary_text",
        lambda *a, **k: "Her diabetes is worsening; increase the dose to 1000 mg.",
    )
    out = w.call("weekly_summary")
    assert out["filtered"] is True and out["text"] == summary_mod.FALLBACK_TEXT


@pytest.mark.parametrize(
    "text",
    [
        "Possible diagnosis",
        "BP is improving",
        "hypertension",
        "take 2 doses",
        "reduce Telma",
        "skip the evening tablet",
        "40 mg",
        "you should rest",
        "stop taking it",
    ],
)
def test_blocked_terms(text):
    assert blocked_terms(text)


@pytest.mark.parametrize(
    "text", ["Glucose 96 mg/dL", "3 of 7 morning readings were above the limit your doctor set"]
)
def test_allowed_descriptive_text(text):
    assert blocked_terms(text) == []


# ---------- audit ----------


def test_every_tool_call_is_audited_including_failures(w):
    w.call("get_meds")
    w.call("log_reading", {"type": "glucose", "values": {"glucose_value": 110}})
    with pytest.raises(ToolError):
        w.call("request_refill", {"med_id": w.schedule_x.id, "qty": 1})
    entries = w.store.list_audit()
    assert [e.tool for e in entries] == ["request_refill", "log_reading", "get_meds"]
    assert all(e.actor == "agent" and e.parent_id == w.parent.id for e in entries)
    assert "Schedule X" in json.loads(entries[0].result_json)["error"]
    assert json.loads(entries[1].args_json)["type"] == "glucose"


def test_unknown_sender_call_is_still_audited(w):
    with pytest.raises(ToolError):
        w.call("get_meds", sender="+910000000000")
    [entry] = w.store.list_audit()
    assert entry.parent_id is None and "Unknown sender" in entry.result_json


def test_timestamps_roundtrip_as_utc(w):
    r = w.confirmed_reading(type="bp", systolic=120, diastolic=80)
    assert r.created_at.tzinfo is not None
    assert r.created_at.utcoffset() == UTC.utcoffset(None)
