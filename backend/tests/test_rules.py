from datetime import UTC, date, datetime, timedelta

import pytest

from app.alerts.thresholds import (
    DEFAULT_THRESHOLDS,
    effective_thresholds,
    evaluate_bp_no_repeat,
    evaluate_glucose,
    evaluate_missed_doses,
    evaluate_reading,
)
from app.reader.rules import check_bp, check_glucose, check_plausibility, to_mgdl
from app.storage.models import DoseEvent, Reading, Threshold

T = DEFAULT_THRESHOLDS
NOW = datetime(2026, 11, 22, 4, 0, tzinfo=UTC)


def bp(s, d, minutes_ago=0, **kw):
    return Reading(
        parent_id="p",
        type="bp",
        systolic=s,
        diastolic=d,
        status="confirmed",
        created_at=NOW - timedelta(minutes=minutes_ago),
        **kw,
    )


# ---------- plausibility (§6.1) ----------


@pytest.mark.parametrize("value,ok", [(19, False), (20, True), (600, True), (601, False)])
def test_glucose_range(value, ok):
    assert (check_glucose(value) == []) is ok


@pytest.mark.parametrize(
    "s,d,pulse,ok",
    [
        (120, 80, 72, True),
        (69, 50, None, False),  # systolic too low
        (251, 100, None, False),  # systolic too high
        (120, 39, None, False),  # diastolic too low
        (200, 151, None, False),  # diastolic too high
        (90, 90, None, False),  # systolic must exceed diastolic
        (80, 95, None, False),  # swapped digits
        (120, 80, 29, False),  # pulse too low
        (120, 80, 201, False),  # pulse too high
        (70, 40, 30, True),  # inclusive bounds
    ],
)
def test_bp_rules(s, d, pulse, ok):
    assert (check_bp(s, d, pulse) == []) is ok


def test_missing_values_are_problems():
    assert check_bp(None, 80, None)
    assert check_plausibility("glucose").ok is False
    assert check_plausibility("weight").ok is False


def test_mmol_conversion():
    assert to_mgdl(7.0, "mmol/L") == 126.0
    assert to_mgdl(126, "mg/dL") == 126.0
    assert to_mgdl(126, None) == 126.0


def test_history_deviation_flags_but_does_not_reject():
    history = [bp(130, 82, m) for m in (60, 120, 180)]
    usual = check_plausibility("bp", systolic=134, diastolic=84, history=history)
    assert usual.ok and usual.flags == []
    odd = check_plausibility("bp", systolic=210, diastolic=84, history=history)
    assert odd.ok and odd.flags and "systolic" in odd.flags[0]
    # Not enough history -> no flag
    assert check_plausibility("bp", systolic=210, diastolic=84, history=history[:2]).flags == []


def test_glucose_history_ratio():
    history = [Reading(parent_id="p", type="glucose", glucose_mgdl=v) for v in (110, 120, 130)]
    assert check_plausibility("glucose", glucose_mgdl=180, history=history).flags == []
    assert check_plausibility("glucose", glucose_mgdl=400, history=history).flags


# ---------- thresholds (§6.3) ----------


@pytest.mark.parametrize(
    "mgdl,level",
    [
        (40, "red"),
        (53.9, "red"),
        (54, "amber"),
        (69, "amber"),
        (70, "none"),
        (180, "none"),
        (250, "none"),
        (251, "amber"),
    ],
)
def test_glucose_thresholds(mgdl, level):
    assert evaluate_glucose(mgdl, T).level == level


def test_red_glucose_mentions_emergency_numbers():
    assert "108/112" in evaluate_glucose(45, T).reason


def test_first_severe_bp_alerts_nobody_and_asks_for_repeat():
    ev = evaluate_reading(bp(182, 96), [], T)
    assert ev.level == "none" and ev.ask_repeat and ev.rule == "bp_severe_first"
    assert evaluate_reading(bp(150, 121), [], T).ask_repeat  # diastolic alone counts


def test_repeat_severe_bp_after_5_minutes_is_red():
    first = bp(184, 98, minutes_ago=6)
    ev = evaluate_reading(bp(181, 97), [first], T)
    assert ev.level == "red" and "108/112" in ev.reason


def test_repeat_too_soon_or_too_late_is_a_new_first_reading():
    for earlier in (bp(184, 98, minutes_ago=2), bp(184, 98, minutes_ago=120)):
        ev = evaluate_reading(bp(181, 97), [earlier], T)
        assert ev.level == "none" and ev.ask_repeat


def test_no_repeat_within_15_minutes_is_amber_with_family_wording():
    first = bp(186, 122, minutes_ago=16)
    ev = evaluate_bp_no_repeat(first, [], NOW, T)
    assert ev.level == "amber" and ev.rule == "bp_severe_no_repeat"
    assert "No repeat reading was taken within 15 minutes" in ev.reason
    assert "rest" not in ev.reason.lower() and "please" not in ev.reason.lower()


def test_no_repeat_rule_waits_15_minutes_and_respects_a_repeat():
    first = bp(186, 122, minutes_ago=10)
    waiting = evaluate_bp_no_repeat(first, [], NOW, T)
    assert waiting.level == "none" and waiting.ask_repeat
    first = bp(186, 122, minutes_ago=16)
    repeat = bp(150, 90, minutes_ago=8)
    assert evaluate_bp_no_repeat(first, [repeat], NOW, T).level == "none"
    assert evaluate_bp_no_repeat(bp(150, 90, minutes_ago=30), [], NOW, T).level == "none"


def test_repeat_that_comes_down_is_not_red():
    first = bp(184, 98, minutes_ago=6)
    assert evaluate_reading(bp(150, 88), [first], T).level == "none"


def test_three_over_limit_readings_in_a_week_is_amber():
    history = [bp(150, 85, minutes_ago=60 * 24 * d) for d in (1, 3)]
    ev = evaluate_reading(bp(145, 88), history, T)
    assert ev.level == "amber" and ev.rule == "bp_weekly_over_limit_count"
    # older than a week does not count
    old = [bp(150, 85, minutes_ago=60 * 24 * d) for d in (8, 9)]
    assert evaluate_reading(bp(145, 88), old, T).level == "none"
    # a normal reading never triggers the weekly rule
    assert evaluate_reading(bp(120, 78), history, T).level == "none"


def test_thresholds_editable_per_parent():
    custom = effective_thresholds(
        [
            Threshold(parent_id="p", rule="bp_severe_systolic", value=170, set_by="doctor"),
            Threshold(parent_id="p", rule="not_a_rule", value=1),
        ]
    )
    assert custom["bp_severe_systolic"] == 170
    assert "not_a_rule" not in custom
    assert evaluate_reading(bp(172, 90), [], custom).ask_repeat
    assert evaluate_reading(bp(172, 90), [], T).level == "none"  # over limit once: no alert
    assert DEFAULT_THRESHOLDS["bp_severe_systolic"] == 180  # defaults untouched


def test_missed_doses_rule_uses_local_day():
    # 20:00 UTC on 21 Nov is 01:30 IST on 22 Nov
    late = datetime(2026, 11, 21, 20, 0, tzinfo=UTC)
    events = [
        DoseEvent(medicine_id="m", due_at=late, status="missed"),
        DoseEvent(medicine_id="m", due_at=late + timedelta(hours=10), status="missed"),
        DoseEvent(medicine_id="m", due_at=late + timedelta(hours=12), status="taken"),
    ]
    assert evaluate_missed_doses(events, date(2026, 11, 22), T).level == "amber"
    assert evaluate_missed_doses(events[:1], date(2026, 11, 22), T).level == "none"
    assert evaluate_missed_doses(events, date(2026, 11, 21), T).level == "none"
