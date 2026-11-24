"""Alert thresholds (brief §6.3). Pure functions: no I/O.

Defaults are starting points, editable per parent (stored in `thresholds`, set_by=doctor)
and always shown to families as "set by your doctor". Reasons describe numbers against
limits only; they never interpret, diagnose or advise on medicines.
"""

from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from app.storage.models import AlertLevel, DoseEvent, Reading, Threshold

Level = Literal["none", "amber", "red"]
LEVEL_RANK = {"none": 0, "amber": 1, "red": 2}


def alert_level(level: Level) -> AlertLevel | None:
    """The level an Alert row stores, or None when no limit was crossed."""
    if level == "red":
        return "red"
    if level == "amber":
        return "amber"
    return None


DEFAULT_THRESHOLDS: dict[str, float] = {
    "glucose_red_below": 54,  # ADA Standards of Care 2026: level 2 hypoglycaemia
    "glucose_amber_below": 70,  # ADA level 1 (54-69)
    "glucose_amber_above": 250,  # illustrative, clinician-set
    "bp_severe_systolic": 180,  # 2025 AHA/ACC severe: symptom check, then repeat
    "bp_severe_diastolic": 120,
    "bp_limit_systolic": 140,  # "above the limit" for the weekly count
    "bp_limit_diastolic": 90,
    "bp_weekly_over_limit_count": 3,
    "missed_doses_per_day": 2,
}

# Fixed glucose floors (ADA 2026). A doctor-set limit can only make them stricter (higher):
# a stored value below the floor is ignored, so no setting can silence a level-2 low.
GLUCOSE_FLOORS = {"glucose_red_below": 54.0, "glucose_amber_below": 70.0}
# 2025 AHA/ACC: home readings averaging 135/85 or more correspond to 140/90 in clinic. The
# weekly summary compares the home average with it, descriptively.
HOME_BP_REFERENCE = (135, 85)

# Severe BP (§6.3, 2025 AHA/ACC). At a confirmed reading at/above the severe limit the parent
# is first asked one yes/no warning-signs question. "Yes" = RED at once (and the parent is
# told to call 108/112). "No" (or no reply in SYMPTOM_WAIT) = rest 5 minutes and repeat;
# the question is asked again at the repeat. A confirmed repeat 5-60 minutes later that is
# still severe = RED "urgent: contact the doctor today". No repeat within 15 minutes = amber.
BP_REPEAT_MIN = timedelta(minutes=5)
BP_REPEAT_WAIT = timedelta(minutes=15)
BP_REPEAT_MAX = timedelta(hours=1)
SYMPTOM_WAIT = timedelta(minutes=5)
# Low sugar (ADA 2026): < 54 RED, 54-69 AMBER + recheck in 15 minutes; no recheck = amber to
# family. Fainting / confusion / needing help (asked after any low reading) = RED.
GLUCOSE_RECHECK_WAIT = timedelta(minutes=15)
EMERGENCY_LINE = "If you feel unwell, call 108/112."  # parent wording (older family texts)

BP_WARNING_SIGNS = (
    "chest pain, breathlessness, weakness or numbness on one side or slurred speech, "
    "severe headache, confusion, or sudden vision problems"
)
LOW_SUGAR_SIGNS = "fainting, confusion, or needing someone's help"


def reading_time(ts: datetime, tz: str = "Asia/Kolkata") -> str:
    """When a reading was taken, for family texts: "2:14 PM, 24 Nov" (local time). Alerts
    may be delivered late (outbox retries), so the text must say when, not imply "now"."""
    local = ts.astimezone(ZoneInfo(tz))
    hour = local.hour % 12 or 12
    return f"{hour}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'}, {local.day} {local:%b}"


class Evaluation(BaseModel):
    level: Level = "none"
    rule: str | None = None
    # Family-facing text (sent by alert_family): numbers against the limits the parent's
    # doctor set, no advice to the parent. Parent wording lives in conversation/templates.py.
    reason: str = "Within the limits the doctor set."
    ask_repeat: bool = False  # first severe BP: no alert yet, ask for a repeat reading


def effective_thresholds(overrides: list[Threshold]) -> dict[str, float]:
    """Defaults overlaid with the parent's own (doctor-set) values, then the glucose floors
    (a setting can only make them stricter). Unknown rules ignored."""
    values = dict(DEFAULT_THRESHOLDS)
    for t in overrides:
        if t.rule in values:
            values[t.rule] = t.value
    for rule, floor in GLUCOSE_FLOORS.items():
        values[rule] = max(values[rule], floor)
    return values


def is_known_rule(rule: str) -> bool:
    return rule in DEFAULT_THRESHOLDS


def is_low_glucose(r: Reading, t: dict[str, float]) -> bool:
    return (
        r.type == "glucose"
        and r.glucose_mgdl is not None
        and (r.glucose_mgdl < t["glucose_amber_below"])
    )


def glucose_words(mgdl: float, display: str | None = None) -> str:
    if display == "LO":
        return "Glucose LO (below what the meter can show)"
    if display == "HI":
        return "Glucose HI (above what the meter can show)"
    return f"Glucose {mgdl:g} mg/dL"


def evaluate_glucose(
    mgdl: float,
    t: dict[str, float],
    at: datetime | None = None,
    *,
    symptoms: str | None = None,
    display: str | None = None,
    whose: str = "their",
) -> Evaluation:
    v = glucose_words(mgdl, display) + (f" at {reading_time(at)}" if at else "")
    limit = f"the limit {whose} doctor set"
    if mgdl < t["glucose_amber_below"] and symptoms == "yes":
        return Evaluation(
            level="red",
            rule="glucose_low_symptoms",
            reason=f"{v} is below {limit} ({t['glucose_amber_below']:g}), and {LOW_SUGAR_SIGNS} "
            "was reported. Please call now.",
        )
    if mgdl < t["glucose_red_below"]:
        return Evaluation(
            level="red",
            rule="glucose_red_below",
            reason=f"{v} is below {limit} ({t['glucose_red_below']:g}). Please call now.",
        )
    if mgdl < t["glucose_amber_below"]:
        return Evaluation(
            level="amber",
            rule="glucose_amber_below",
            reason=f"{v} is below {limit} ({t['glucose_amber_below']:g}). A recheck in "
            f"{GLUCOSE_RECHECK_WAIT.seconds // 60} minutes was requested.",
        )
    if mgdl > t["glucose_amber_above"]:
        return Evaluation(
            level="amber",
            rule="glucose_amber_above",
            reason=f"{v} is above {limit} ({t['glucose_amber_above']:g}).",
        )
    return Evaluation()


def evaluate_glucose_no_recheck(
    first: Reading,
    later: list[Reading],
    now: datetime,
    t: dict[str, float],
    whose: str = "their",
    tz: str = "Asia/Kolkata",
) -> Evaluation:
    """Amber when a low (54-69) glucose got no confirmed recheck within 15 minutes.
    `later` = the parent's confirmed glucose readings taken after `first`."""
    mgdl = first.glucose_mgdl
    if first.type != "glucose" or mgdl is None:
        return Evaluation()
    if not t["glucose_red_below"] <= mgdl < t["glucose_amber_below"]:
        return Evaluation()
    deadline = first.created_at + GLUCOSE_RECHECK_WAIT
    if now < deadline:
        return Evaluation(rule="glucose_low_first", reason="Waiting for the recheck.")
    if any(first.created_at < r.created_at <= deadline for r in later if r.id != first.id):
        return Evaluation()
    v = f"{glucose_words(mgdl, first.display)} at {reading_time(first.created_at, tz)}"
    return Evaluation(
        level="amber",
        rule="glucose_low_no_recheck",
        reason=f"{v} was below the limit {whose} doctor set ({t['glucose_amber_below']:g}). "
        f"No recheck came within {GLUCOSE_RECHECK_WAIT.seconds // 60} minutes.",
    )


def is_severe_bp(r: Reading, t: dict[str, float]) -> bool:
    return (r.systolic or 0) >= t["bp_severe_systolic"] or (r.diastolic or 0) >= t[
        "bp_severe_diastolic"
    ]


def is_over_limit_bp(r: Reading, t: dict[str, float]) -> bool:
    return (r.systolic or 0) >= t["bp_limit_systolic"] or (r.diastolic or 0) >= t[
        "bp_limit_diastolic"
    ]


_SYMPTOM_NOTE = {
    "no": "No warning signs were reported.",
    "no_reply": "The warning-signs question was not answered.",
}


def evaluate_bp(
    reading: Reading, history: list[Reading], t: dict[str, float], whose: str = "their"
) -> Evaluation:
    """`history` = the parent's confirmed BP readings (any order; this reading excluded)."""
    v = f"BP {reading.systolic}/{reading.diastolic} at {reading_time(reading.created_at)}"
    severe = f"{t['bp_severe_systolic']:g}/{t['bp_severe_diastolic']:g}"
    limit = f"the limit {whose} doctor set"
    if is_severe_bp(reading, t):
        if reading.symptoms == "yes":
            return Evaluation(
                level="red",
                rule="bp_severe_symptoms",
                reason=f"{v} is at or above {limit} ({severe}), and warning signs were "
                f"reported ({BP_WARNING_SIGNS}). Told to call 108/112 now. Please call now.",
            )
        earlier_severe = [
            h
            for h in history
            if is_severe_bp(h, t)
            and BP_REPEAT_MIN <= reading.created_at - h.created_at <= BP_REPEAT_MAX
        ]
        if earlier_severe:
            note = _SYMPTOM_NOTE.get(reading.symptoms or "", "")
            return Evaluation(
                level="red",
                rule="bp_severe_repeat",
                reason=f"Repeat {v} is still at or above {limit} ({severe}). "
                + (f"{note} " if note else "")
                + f"Urgent: contact {whose} doctor today.",
            )
        return Evaluation(
            level="none",
            rule="bp_severe_first",
            ask_repeat=True,
            reason=f"{v} is at or above {limit} ({severe}); "
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
            lim = f"{t['bp_limit_systolic']:g}/{t['bp_limit_diastolic']:g}"
            return Evaluation(
                level="amber",
                rule="bp_weekly_over_limit_count",
                reason=f"{count} BP readings in the last 7 days were at or above {limit} "
                f"({lim}); latest {v}.",
            )
    return Evaluation()


def evaluate_bp_no_repeat(
    first: Reading,
    later: list[Reading],
    now: datetime,
    t: dict[str, float],
    tz: str = "Asia/Kolkata",
    whose: str = "their",
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
        reason=f"BP {first.systolic}/{first.diastolic} at {at} was at or above the limit "
        f"{whose} doctor set ({severe}). No repeat reading was taken within "
        f"{BP_REPEAT_WAIT.seconds // 60} minutes.",
    )


def evaluate_reading(
    reading: Reading, history: list[Reading], t: dict[str, float], whose: str = "their"
) -> Evaluation:
    if reading.type == "glucose" and reading.glucose_mgdl is not None:
        return evaluate_glucose(
            reading.glucose_mgdl,
            t,
            reading.created_at,
            symptoms=reading.symptoms,
            display=reading.display,
            whose=whose,
        )
    if reading.type == "bp":
        bp_history = [h for h in history if h.type == "bp" and h.id != reading.id]
        return evaluate_bp(reading, bp_history, t, whose)
    return Evaluation()


def evaluate_missed_doses(
    events: list[DoseEvent],
    day: date,
    t: dict[str, float],
    tz: str = "Asia/Kolkata",
    whose: str = "their",
) -> Evaluation:
    """Amber when the parent missed `missed_doses_per_day` or more doses on `day` (local)."""
    zone = ZoneInfo(tz)
    missed = [e for e in events if e.status == "missed" and e.due_at.astimezone(zone).date() == day]
    limit = t["missed_doses_per_day"]
    if len(missed) >= limit:
        return Evaluation(
            level="amber",
            rule="missed_doses_per_day",
            reason=f"{len(missed)} medicine reminders were marked missed on {day:%d %b}, at or "
            f"above the limit {whose} doctor set ({limit:g} a day).",
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
            label="Severe BP, systolic (symptom check + repeat)",
            unit="mmHg",
            min=150,
            max=220,
            integer=True,
        ),
        ThresholdSpec(
            rule="bp_severe_diastolic",
            label="Severe BP, diastolic (symptom check + repeat)",
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
        # Glucose lows: the minimum is the fixed ADA floor (a doctor can only go stricter).
        ThresholdSpec(
            rule="glucose_red_below", label="Glucose red below", unit="mg/dL", min=54, max=80
        ),
        ThresholdSpec(
            rule="glucose_amber_below", label="Glucose amber below", unit="mg/dL", min=70, max=110
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
