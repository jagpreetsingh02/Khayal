"""Clinical rules (2025 AHA/ACC severe BP, ADA Standards of Care 2026 hypoglycaemia).

Every path through the warning-signs question, the glucose floors, the 15-minute recheck,
the doctor's own low-sugar plan, and the wording of family alerts.
"""

import json
from datetime import timedelta

import pytest

from app.alerts.thresholds import (
    DEFAULT_THRESHOLDS,
    effective_thresholds,
    evaluate_glucose,
    validate_threshold_update,
)
from app.conversation import templates as tpl
from app.conversation.engine import AWAITING_REPEAT, AWAITING_SYMPTOMS, IDLE
from app.storage.models import Reading, Threshold, utcnow
from app.voice import phrases
from mcp_server.guardrails import blocked_terms
from mcp_server.summary import build_summary_text, filter_summary
from tests.test_conversation import World

HIGH = "synthetic_bp_high.jpg"  # 186/122, pulse 92
HIGH_VALUE = "186/122, pulse 92"


@pytest.fixture
def w():
    world = World()
    yield world
    world.store.close()


def symptom_audits(w) -> list[dict]:
    rows = w.store.list_audit(tool="conversation:symptom_check")
    return [json.loads(r.result_json) | {"actor": r.actor} for r in reversed(rows)]


# ---------- severe BP: one warning-signs question before anything else ----------


def test_first_severe_bp_asks_the_warning_signs_question_first(w):
    w.say(photo=HIGH)
    out = w.send("1")
    [ask] = w.to_parent(out)
    assert ask == tpl.SYMPTOM_CHECK_BP.format(value=HIGH_VALUE)
    for sign in ("chest pain", "breathlessness", "one side", "slurred speech", "severe headache"):
        assert sign in ask
    assert "confusion" in ask and "vision" in ask and ask.endswith("Reply 1 yes / 2 no")
    assert w.to_family(out) == [] and w.store.list_alerts(w.parent.id) == []
    assert w.state.state == AWAITING_SYMPTOMS


def test_yes_to_warning_signs_is_red_at_once_and_says_108(w):
    w.say(photo=HIGH)
    w.say("1")
    out = w.send("1")  # yes: warning signs
    [reply] = w.to_parent(out)
    assert reply == tpl.BP_CALL_NOW.format(family="I have told your family.")
    assert "108 or 112" in reply
    family = w.to_family(out)
    assert len(family) == 2
    for text in family:
        assert text.startswith("RED alert for Lakshmi: BP 186/122")
        assert "the limit their doctor set" in text and "warning signs were reported" in text
    [alert] = w.store.list_alerts(w.parent.id)
    assert (alert.level, alert.rule) == ("red", "bp_severe_symptoms")
    assert w.store.get_reading(alert.reading_id).symptoms == "yes"
    assert w.state.state == IDLE  # no repeat flow after an emergency
    w.clock.advance(20)
    assert w.engine.tick() == []  # and no amber "no repeat" later
    assert symptom_audits(w) == [{"answer": "yes", "actor": "member"}]


def test_no_keeps_the_rest_and_repeat_flow(w):
    w.say(photo=HIGH)
    w.say("1")
    assert w.say("2") == tpl.REPEAT_ASK
    assert w.state.state == AWAITING_REPEAT
    assert w.store.list_alerts(w.parent.id) == []
    [first] = [r for r in w.readings() if r.status == "confirmed"]
    assert first.symptoms == "no"


def test_no_reply_in_5_minutes_is_audited_and_carries_on_as_today(w):
    w.say(photo=HIGH)
    w.say("1")
    w.clock.advance(4)
    assert w.engine.tick() == []  # still waiting for the answer
    w.clock.advance(1.5)
    out = w.engine.tick()
    assert w.to_parent(out) == [tpl.REPEAT_ASK_NO_REPLY]
    assert w.to_family(out) == []
    assert w.state.state == AWAITING_REPEAT
    [audit] = symptom_audits(w)
    assert audit == {"answer": "no_reply", "note": "no reply within 5 minutes", "actor": "system"}
    # the repeat window still counts from the first reading: 15 min without one = amber
    w.clock.advance(10)
    out = w.engine.tick()
    assert any(t.startswith("AMBER alert") for t in w.to_family(out))


def test_anything_but_yes_or_no_gets_the_question_again(w):
    w.say(photo=HIGH)
    w.say("1")
    assert w.say(photo=HIGH) == tpl.SYMPTOM_PLEASE_ANSWER
    assert w.say("what should I do") == tpl.SYMPTOM_PLEASE_ANSWER
    assert w.state.state == AWAITING_SYMPTOMS
    assert len(w.readings()) == 1  # the photo was not read


def test_repeat_is_asked_again_and_yes_at_the_repeat_is_an_emergency(w):
    w.say(photo=HIGH)
    w.say("1")
    w.say("2")
    w.clock.advance(6)
    w.say(photo=HIGH)
    assert w.say("1") == tpl.SYMPTOM_CHECK_BP_REPEAT.format(value=HIGH_VALUE)
    out = w.send("1")
    assert w.to_parent(out) == [tpl.BP_CALL_NOW.format(family="I have told your family.")]
    [alert] = w.store.list_alerts(w.parent.id)
    assert alert.rule == "bp_severe_symptoms"


def test_asymptomatic_repeat_alerts_family_with_doctor_today_wording(w):
    w.say(photo=HIGH)
    w.say("1")
    w.say("2")
    w.clock.advance(6)
    w.say(photo=HIGH)
    w.say("1")
    out = w.send("2")
    family = w.to_family(out)
    assert len(family) == 2
    for text in family:
        assert "No warning signs were reported." in text
        assert text.endswith("Urgent: contact their doctor today.")
        assert "108" not in text and "112" not in text and "ambulance" not in text.lower()
    [alert] = w.store.list_alerts(w.parent.id)
    assert (alert.level, alert.rule) == ("red", "bp_severe_repeat")


def test_unanswered_question_at_the_repeat_still_alerts_after_5_minutes(w):
    w.say(photo=HIGH)
    w.say("1")
    w.say("2")
    w.clock.advance(6)
    w.say(photo=HIGH)
    w.say("1")  # repeat confirmed -> question; no answer
    w.clock.advance(5.5)
    out = w.engine.tick()
    family = w.to_family(out)
    assert family and all("The warning-signs question was not answered." in t for t in family)
    assert [a.rule for a in w.store.list_alerts(w.parent.id)] == ["bp_severe_repeat"]


def test_symptom_answer_cannot_come_from_a_model():
    """alert_family has no symptoms argument: only the parent's reply (server-side) sets it."""
    from app.storage.base import Store

    assert hasattr(Store, "set_reading_symptoms")
    from tests.test_mcp_tools import World as ToolWorld

    tw = ToolWorld()
    r = tw.confirmed_reading(type="bp", systolic=186, diastolic=122)
    from fastmcp.exceptions import ToolError

    with pytest.raises(ToolError, match="symptoms"):
        tw.call("alert_family", {"level": "red", "reading_id": r.id, "symptoms": "yes"})
    with pytest.raises(ToolError, match="Waiting for the repeat"):
        tw.call("alert_family", {"level": "red", "reading_id": r.id})


def test_voice_versions_of_the_questions_exist_in_every_language():
    for key in ("symptom_check_bp", "symptom_check_low", "call_108", "rest_repeat"):
        for lang in ("en", "hi", "ta"):
            voice = phrases.say(key, lang)
            assert voice.lang == lang and voice.text
    assert "108" in phrases.say("call_108", "hi").text


def test_voice_note_yes_answers_the_question(w):
    from app.conversation.messages import Incoming

    w.say(photo=HIGH)
    w.say("1")
    out = w.engine.handle_message(
        Incoming(channel="simulator", sender_ref=w.parent.channel_ref, text="हाँ", via_voice=True)
    )
    [reply] = [o for o in out if o.member_id == w.parent.id]
    assert reply.text.startswith("Please call 108 or 112 now")
    assert reply.voice is not None and "108" in reply.voice.text


# ---------- glucose: fixed floors, recheck, the doctor's plan ----------


def test_glucose_floors_cannot_be_loosened_by_a_setting():
    loose = [
        Threshold(parent_id="p", rule="glucose_red_below", value=40, set_by="doctor"),
        Threshold(parent_id="p", rule="glucose_amber_below", value=50, set_by="doctor"),
    ]
    t = effective_thresholds(loose)
    assert t["glucose_red_below"] == 54 and t["glucose_amber_below"] == 70
    assert evaluate_glucose(53, t).level == "red"
    assert evaluate_glucose(69, t).level == "amber"
    # the dashboard refuses such values outright
    assert validate_threshold_update(DEFAULT_THRESHOLDS, {"glucose_red_below": 50})
    assert validate_threshold_update(DEFAULT_THRESHOLDS, {"glucose_amber_below": 65})


def test_stricter_glucose_limits_are_kept():
    strict = [Threshold(parent_id="p", rule="glucose_amber_below", value=80, set_by="doctor")]
    t = effective_thresholds(strict)
    assert t["glucose_amber_below"] == 80 and evaluate_glucose(75, t).level == "amber"
    assert validate_threshold_update(DEFAULT_THRESHOLDS, {"glucose_red_below": 60}) == []


def test_low_glucose_sends_the_doctors_plan_and_asks_for_a_recheck(w):
    plan = "Take 3 glucose tablets or half a glass of juice, then check again."
    w.store.set_low_sugar_plan(w.parent.id, plan)
    w.say("sugar 62")
    out = w.send("1")
    first, question = w.to_parent(out)
    assert first.startswith(tpl.FAMILY_TOLD.format(value="sugar 62 mg/dL"))
    assert f"Your doctor's plan: {plan}" in first and tpl.RECHECK_ASK in first
    assert question == tpl.LOW_SUGAR_QUESTION
    [alert] = w.store.list_alerts(w.parent.id)
    assert (alert.level, alert.rule) == ("amber", "glucose_amber_below")
    assert all("A recheck in 15 minutes was requested." in t for t in w.to_family(out))


def test_without_a_plan_the_parent_is_told_to_follow_the_doctors_plan(w):
    w.say("sugar 62")
    first, _ = w.to_parent(w.send("1"))
    assert "Please follow your doctor's plan for low sugar." in first
    for word in ("juice", "tablet", "eat", "drink", "glucose gel"):
        assert word not in first.lower()  # never Khayal's own advice


def test_no_recheck_in_15_minutes_alerts_the_family(w):
    w.say("sugar 62")
    w.say("1")
    w.say("2")  # no fainting / confusion
    w.clock.advance(14)
    assert w.engine.tick() == []
    w.clock.advance(2)
    out = w.engine.tick()
    assert w.to_parent(out) == [tpl.NO_RECHECK_FAMILY_TOLD.format(value="sugar 62 mg/dL")]
    family = w.to_family(out)
    assert len(family) == 2 and all("No recheck came within 15 minutes." in t for t in family)
    assert [a.rule for a in w.store.list_alerts(w.parent.id)] == [
        "glucose_amber_below",
        "glucose_low_no_recheck",
    ]
    assert w.engine.tick() == []  # once


def test_a_recheck_in_time_cancels_the_family_alert(w):
    w.say("sugar 62")
    w.say("1")
    w.say("2")
    w.clock.advance(12)
    w.say("sugar 98")
    assert w.say("1") == tpl.RECHECK_OK.format(value="sugar 98 mg/dL")
    w.clock.advance(10)
    assert w.engine.tick() == []
    assert [a.rule for a in w.store.list_alerts(w.parent.id)] == ["glucose_amber_below"]


def test_fainting_or_needing_help_is_red_whatever_the_number(w):
    w.store.set_low_sugar_plan(w.parent.id, "Take 3 glucose tablets.")
    w.say("sugar 66")
    w.say("1")
    out = w.send("1")  # yes: fainted / confused / needed help
    [reply] = w.to_parent(out)
    assert reply.startswith("Please ask someone near you to help now, and call 108/112")
    assert "Your doctor's plan: Take 3 glucose tablets." in reply
    family = w.to_family(out)
    assert len(family) == 2 and all(t.startswith("RED alert for Lakshmi") for t in family)
    assert all("fainting, confusion, or needing someone's help was reported" in t for t in family)
    assert sorted(a.rule for a in w.store.list_alerts(w.parent.id)) == [
        "glucose_amber_below",
        "glucose_low_symptoms",
    ]
    w.clock.advance(20)
    assert w.engine.tick() == []  # red already sent: no extra "no recheck" amber


def test_red_low_glucose_also_asks_the_question(w):
    w.say("sugar 48")
    out = w.send("1")
    first, question = w.to_parent(out)
    assert "below the limit your doctor set" in first and question == tpl.LOW_SUGAR_QUESTION
    assert w.state.state == AWAITING_SYMPTOMS
    w.clock.advance(6)
    assert w.engine.tick() == []  # no answer: nothing more, but it is audited
    assert symptom_audits(w)[0]["answer"] == "no_reply"


def test_a_new_reading_while_the_low_question_is_open_is_handled(w):
    w.say("sugar 62")
    w.say("1")
    assert w.say("sugar 90").startswith("I read sugar 90 mg/dL")


def test_meter_lo_is_red_and_hi_is_amber(w):
    from tests.test_mcp_tools import World as ToolWorld

    tw = ToolWorld()
    lo = tw.call("log_reading", {"type": "glucose", "values": {"display": "LO"}})
    assert lo["read_back"].startswith("I read sugar LO (below 20 mg/dL)")
    tw.store.set_reading_status(lo["reading_id"], "confirmed")
    assert tw.call("alert_family", {"level": "red", "reading_id": lo["reading_id"]})["sent"]
    hi = tw.call("log_reading", {"type": "glucose", "values": {"display": "HI"}})
    tw.store.set_reading_status(hi["reading_id"], "confirmed")
    out = tw.call("alert_family", {"level": "amber", "reading_id": hi["reading_id"]})
    assert out["sent"] and "Glucose HI" in out["message"]


# ---------- doctor-set BP limits and alert wording ----------


def test_bp_limits_are_doctor_configurable_and_used(w):
    w.store.set_threshold(w.parent.id, "bp_severe_systolic", 170, set_by="doctor")
    w.say("bp 172/95")
    assert w.say("1").startswith(tpl.SYMPTOM_CHECK_BP.format(value="172/95")[:20])
    assert w.state.state == AWAITING_SYMPTOMS


@pytest.mark.parametrize(
    "setup",
    ["glucose_red", "glucose_amber", "glucose_high", "bp_weekly", "bp_no_repeat", "doses"],
)
def test_every_alert_text_says_above_or_below_the_limit_her_doctor_set(setup):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.storage.models import DoseEvent
    from tests.test_mcp_tools import World as ToolWorld

    tw = ToolWorld()
    tw.store._conn.execute("UPDATE members SET pronoun = 'she' WHERE id = ?", (tw.parent.id,))
    if setup.startswith("glucose"):
        value = {"glucose_red": 45, "glucose_amber": 62, "glucose_high": 300}[setup]
        r = tw.confirmed_reading(type="glucose", glucose_mgdl=value)
        out = tw.call("alert_family", {"level": "amber", "reading_id": r.id})
    elif setup == "bp_weekly":
        for days in (3, 2, 1):
            tw.confirmed_reading(
                type="bp", systolic=150, diastolic=92, created_at=utcnow() - timedelta(days=days)
            )
        r = tw.confirmed_reading(type="bp", systolic=150, diastolic=92)
        out = tw.call("alert_family", {"level": "amber", "reading_id": r.id})
    elif setup == "bp_no_repeat":
        r = tw.confirmed_reading(
            type="bp", systolic=186, diastolic=122, created_at=utcnow() - timedelta(minutes=16)
        )
        out = tw.call("alert_family", {"level": "amber", "reading_id": r.id})
    else:
        start = datetime.now(ZoneInfo("Asia/Kolkata")).replace(hour=0, minute=30)
        for h in (0, 1):
            tw.store.add_dose_event(
                DoseEvent(
                    medicine_id=tw.telma.id, due_at=start + timedelta(hours=h), status="missed"
                )
            )
        out = tw.call("alert_family", {"level": "amber"})
    assert out["sent"], out
    assert "the limit her doctor set" in out["message"]
    assert blocked_terms(out["message"]) == []


# ---------- weekly summary: home average vs 135/85, descriptive only ----------


def _bp(s, d, days_ago=1) -> Reading:
    return Reading(
        parent_id="p",
        type="bp",
        systolic=s,
        diastolic=d,
        status="confirmed",
        created_at=utcnow() - timedelta(days=days_ago),
    )


def test_weekly_summary_compares_the_home_average_with_135_85(w):
    end = utcnow()
    t = effective_thresholds([])
    above = build_summary_text(
        w.parent, [_bp(140, 86), _bp(136, 84, 2)], [], [], t, end - timedelta(days=7), end
    )
    assert "Home BP average: 138/85, at or above the 135/85 reference for home readings." in above
    below = build_summary_text(
        w.parent, [_bp(128, 80), _bp(124, 78, 2)], [], [], t, end - timedelta(days=7), end
    )
    assert "Home BP average: 126/79, below the 135/85 reference" in below
    for text in (above, below):
        assert filter_summary(text) == (text, [])  # passes the diagnosis / dosing filter
