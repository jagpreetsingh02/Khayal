"""Alert thresholds (brief §6.3). Pure functions: no I/O.

Defaults are starting points, editable per parent (stored in `thresholds`, set_by=doctor)
and always shown to families as "set by your doctor". Reasons describe numbers against
limits only; they never interpret, diagnose or advise on medicines.
"""

from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from app.storage.models import DoseEvent, Reading, Threshold

Level = Literal["none", "amber", "red"]
LEVEL_RANK = {"none": 0, "amber": 1, "red": 2}

DEFAULT_THRESHOLDS: dict[str, float] = {
    "glucose_red_below": 54,  # ADA level 2
    "glucose_amber_below": 70,  # ADA level 1 (54-69)
    "glucose_amber_above": 250,  # illustrative, clinician-set
    "bp_severe_systolic": 180,  # AHA/ACC 2025 severe, needs repeat to go red
    "bp_severe_diastolic": 120,
    "bp_limit_systolic": 140,  # "above the limit" for the weekly count
    "bp_limit_diastolic": 90,
    "bp_weekly_over_limit_count": 3,
    "missed_doses_per_day": 2,
}

# Repeat-BP rule (§6.3): a first reading at/above the severe limit alerts nobody yet; the
# parent is asked to rest and repeat. A confirming repeat 5-60 min later is red. If no repeat
# is confirmed within 15 minutes, the family gets an amber "high reading, no repeat taken".
BP_REPEAT_MIN = timedelta(minutes=5)
BP_REPEAT_WAIT = timedelta(minutes=15)
BP_REPEAT_MAX = timedelta(hours=1)
EMERGENCY_LINE = "If you feel unwell, call 108/112."


def reading_time(ts: datetime, tz: str = "Asia/Kolkata") -> str:
    """When a reading was taken, for family texts: "2:14 PM, 24 Nov" (local time). Alerts
    may be delivered late (outbox retries), so the text must say when, not imply "now"."""
    local = ts.astimezone(ZoneInfo(tz))
    hour = local.hour % 12 or 12
    return f"{hour}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'}, {local.day} {local:%b}"


class Evaluation(BaseModel):
    level: Level = "none"
    rule: str | None = None
    # Family-facing text (sent by alert_family): numbers against limits, no advice to the
    # parent. Parent wording lives in app/conversation/templates.py.
    reason: str = "Within the limits set by the doctor."
    ask_repeat: bool = False  # first severe BP: no alert yet, ask for a repeat reading


def effective_thresholds(overrides: list[Threshold]) -> dict[str, float]:
    """Defaults overlaid with the parent's own (doctor-set) values. Unknown rules ignored."""
    values = dict(DEFAULT_THRESHOLDS)
    for t in overrides:
        if t.rule in values:
            values[t.rule] = t.value
    return values


def is_known_rule(rule: str) -> bool:
    return rule in DEFAULT_THRESHOLDS


def evaluate_glucose(mgdl: float, t: dict[str, float], at: datetime | None = None) -> Evaluation:
    v = f"Glucose {mgdl:g} mg/dL" + (f" at {reading_time(at)}" if at else "")
    if mgdl < t["glucose_red_below"]:
        return Evaluation(
            level="red",
            rule="glucose_red_below",
            reason=f"{v} is below the limit set by the doctor "
            f"({t['glucose_red_below']:g}). {EMERGENCY_LINE}",
        )
    if mgdl < t["glucose_amber_below"]:
        return Evaluation(
            level="amber",
            rule="glucose_amber_below",
            reason=f"{v} is below the limit set by the doctor ({t['glucose_amber_below']:g}).",
        )
    if mgdl > t["glucose_amber_above"]:
        return Evaluation(
            level="amber",
            rule="glucose_amber_above",
            reason=f"{v} is above the limit set by the doctor ({t['glucose_amber_above']:g}).",
        )
    return Evaluation()


def is_severe_bp(r: Reading, t: dict[str, float]) -> bool:
    return (r.systolic or 0) >= t["bp_severe_systolic"] or (r.diastolic or 0) >= t[
        "bp_severe_diastolic"
    ]


def is_over_limit_bp(r: Reading, t: dict[str, float]) -> bool:
    return (r.systolic or 0) >= t["bp_limit_systolic"] or (r.diastolic or 0) >= t[
        "bp_limit_diastolic"
    ]


def evaluate_bp(reading: Reading, history: list[Reading], t: dict[str, float]) -> Evaluation:
    """`history` = the parent's confirmed BP readings (any order; this reading excluded)."""
    v = f"BP {reading.systolic}/{reading.diastolic} at {reading_time(reading.created_at)}"
    severe = f"{t['bp_severe_systolic']:g}/{t['bp_severe_diastolic']:g}"
    if is_severe_bp(reading, t):
        earlier_severe = [
            h
            for h in history
            if is_severe_bp(h, t)
            and BP_REPEAT_MIN <= reading.created_at - h.created_at <= BP_REPEAT_MAX
        ]
        if earlier_severe:
            return Evaluation(
                level="red",
                rule="bp_severe_repeat",
                reason=f"Repeat {v} is still at or above the limit set by the doctor "
                f"({severe}). {EMERGENCY_LINE}",
            )
        return Evaluation(
            level="none",
            rule="bp_severe_first",
            ask_repeat=True,
            reason=f"{v} is at or above the limit set by the doctor ({severe}); "
            "waiting for a repeat reading before alerting the family.",
        )

    if is_over_limit_bp(reading, t):
        week_start = reading.created_at - timedelta(days=7)
        over = [
            h
            for h in history
            if week_start <= h.created_at < reading.created_at and is_over_limit_bp(h, t)
        ]
        count = len(over) + 1
        if count >= t["bp_weekly_over_limit_count"]:
            limit = f"{t['bp_limit_systolic']:g}/{t['bp_limit_diastolic']:g}"
            return Evaluation(
                level="amber",
                rule="bp_weekly_over_limit_count",
                reason=f"{count} BP readings in the last 7 days were at or above the limit "
                f"set by the doctor ({limit}); latest {v}.",
            )
    return Evaluation()


def evaluate_bp_no_repeat(
    first: Reading,
    later: list[Reading],
    now: datetime,
    t: dict[str, float],
    tz: str = "Asia/Kolkata",
) -> Evaluation:
    """Amber when a first severe BP got no confirmed repeat within BP_REPEAT_WAIT.

    `later` = the parent's confirmed BP readings taken after `first` (any order).
    """
    if first.type != "bp" or not is_severe_bp(first, t):
        return Evaluation()
    deadline = first.created_at + BP_REPEAT_WAIT
    if now < deadline:
        return Evaluation(
            rule="bp_severe_first",
            ask_repeat=True,
            reason="Waiting for the repeat reading (the family is told only after "
            f"{BP_REPEAT_WAIT.seconds // 60} minutes without one).",
        )
    if any(first.created_at < r.created_at <= deadline for r in later if r.id != first.id):
        return Evaluation()  # a repeat was taken; it is evaluated on its own
    severe = f"{t['bp_severe_systolic']:g}/{t['bp_severe_diastolic']:g}"
    at = reading_time(first.created_at, tz)
    return Evaluation(
        level="amber",
        rule="bp_severe_no_repeat",
        reason=f"BP {first.systolic}/{first.diastolic} at {at} was at or above the limit set "
        f"by the doctor ({severe}). No repeat reading was taken within "
        f"{BP_REPEAT_WAIT.seconds // 60} minutes.",
    )


def evaluate_reading(reading: Reading, history: list[Reading], t: dict[str, float]) -> Evaluation:
    if reading.type == "glucose" and reading.glucose_mgdl is not None:
        return evaluate_glucose(reading.glucose_mgdl, t, reading.created_at)
    if reading.type == "bp":
        bp_history = [h for h in history if h.type == "bp" and h.id != reading.id]
        return evaluate_bp(reading, bp_history, t)
    return Evaluation()


def evaluate_missed_doses(
    events: list[DoseEvent], day: date, t: dict[str, float], tz: str = "Asia/Kolkata"
) -> Evaluation:
    """Amber when the parent missed `missed_doses_per_day` or more doses on `day` (local)."""
    zone = ZoneInfo(tz)
    missed = [e for e in events if e.status == "missed" and e.due_at.astimezone(zone).date() == day]
    if len(missed) >= t["missed_doses_per_day"]:
        return Evaluation(
            level="amber",
            rule="missed_doses_per_day",
            reason=f"{len(missed)} medicine reminders were marked missed on {day:%d %b}.",
        )
    return Evaluation()


def local_today(tz: str = "Asia/Kolkata", now: datetime | None = None) -> date:
    from app.storage.models import utcnow  # app clock (includes the demo offset)

    return (now or utcnow()).astimezone(ZoneInfo(tz)).date()


# --- Editing limits on the dashboard ("set by your doctor") ---
# Each editable limit: label, unit and the range we accept. The ranges only catch typing
# mistakes (e.g. 14 instead of 140); the values themselves are the doctor's decision.
class ThresholdSpec(BaseModel):
    rule: str
    label: str
    unit: str
    min: float
    max: float
    integer: bool = False


THRESHOLD_SPECS: dict[str, ThresholdSpec] = {
    s.rule: s
    for s in (
        ThresholdSpec(
            rule="bp_limit_systolic",
            label="BP limit, systolic",
            unit="mmHg",
            min=110,
            max=180,
            integer=True,
        ),
        ThresholdSpec(
            rule="bp_limit_diastolic",
            label="BP limit, diastolic",
            unit="mmHg",
            min=60,
            max=110,
            integer=True,
        ),
        ThresholdSpec(
            rule="bp_severe_systolic",
            label="Severe BP, systolic (repeat needed)",
            unit="mmHg",
            min=150,
            max=220,
            integer=True,
        ),
        ThresholdSpec(
            rule="bp_severe_diastolic",
            label="Severe BP, diastolic (repeat needed)",
            unit="mmHg",
            min=90,
            max=140,
            integer=True,
        ),
        ThresholdSpec(
            rule="bp_weekly_over_limit_count",
            label="BP readings over the limit in 7 days",
            unit="readings",
            min=1,
            max=14,
            integer=True,
        ),
        ThresholdSpec(
            rule="glucose_red_below", label="Glucose red below", unit="mg/dL", min=40, max=80
        ),
        ThresholdSpec(
            rule="glucose_amber_below", label="Glucose amber below", unit="mg/dL", min=50, max=110
        ),
        ThresholdSpec(
            rule="glucose_amber_above", label="Glucose amber above", unit="mg/dL", min=140, max=400
        ),
        ThresholdSpec(
            rule="missed_doses_per_day",
            label="Missed doses in a day",
            unit="doses",
            min=1,
            max=10,
            integer=True,
        ),
    )
}
assert set(THRESHOLD_SPECS) == set(DEFAULT_THRESHOLDS)


def validate_threshold_update(current: dict[str, float], changes: dict[str, float]) -> list[str]:
    """Problems with applying `changes` on top of `current` effective limits ([] = fine)."""
    problems: list[str] = []
    for rule, value in changes.items():
        spec = THRESHOLD_SPECS.get(rule)
        if spec is None:
            problems.append(f"Unknown limit {rule!r}.")
            continue
        if not spec.min <= value <= spec.max:
            problems.append(
                f"{spec.label} must be between {spec.min:g} and {spec.max:g} {spec.unit}."
            )
        elif spec.integer and value != int(value):
            problems.append(f"{spec.label} must be a whole number.")
    if problems:
        return problems
    v = current | changes
    pairs = (
        ("glucose_red_below", "glucose_amber_below"),
        ("glucose_amber_below", "glucose_amber_above"),
        ("bp_limit_systolic", "bp_severe_systolic"),
        ("bp_limit_diastolic", "bp_severe_diastolic"),
    )
    for low, high in pairs:
        if v[low] >= v[high]:
            problems.append(
                f"{THRESHOLD_SPECS[low].label} ({v[low]:g}) must be lower than "
                f"{THRESHOLD_SPECS[high].label} ({v[high]:g})."
            )
    return problems
