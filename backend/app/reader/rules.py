"""Plausibility rules (brief §6.1). Pure functions: no I/O, no model calls.

A failed rule means "ask the parent for a retake"; a history flag means "read it back
with a note that it is unusual", never a silent rejection.
"""

from statistics import median

from pydantic import BaseModel, Field

from app.storage.models import Reading

GLUCOSE_MGDL_RANGE = (20, 600)
SYSTOLIC_RANGE = (70, 250)
DIASTOLIC_RANGE = (40, 150)
PULSE_RANGE = (30, 200)
MMOL_TO_MGDL = 18.0

# "Deviates hugely from the parent's own recent history". BP is stable day to day, so a
# relative band works; glucose swings a lot around meals, so use a ratio instead.
MIN_HISTORY = 3
BP_MAX_RELATIVE_DEVIATION = 0.35
GLUCOSE_MAX_RATIO = 2.0


class PlausibilityResult(BaseModel):
    ok: bool
    problems: list[str] = Field(default_factory=list)  # any problem -> ask for a retake
    flags: list[str] = Field(default_factory=list)  # unusual vs history -> mention in read-back


def to_mgdl(value: float, unit: str | None) -> float:
    """Normalise a glucose value to mg/dL. Unknown unit is treated as mg/dL."""
    if unit and unit.lower().replace(" ", "") in {"mmol/l", "mmol"}:
        return round(value * MMOL_TO_MGDL, 1)
    return float(value)


def _in_range(name: str, value: float | None, bounds: tuple[int, int]) -> list[str]:
    if value is None:
        return [f"{name} missing"]
    low, high = bounds
    if not low <= value <= high:
        return [f"{name} {value:g} outside plausible range {low}-{high}"]
    return []


def check_bp(systolic: float | None, diastolic: float | None, pulse: float | None) -> list[str]:
    problems = _in_range("systolic", systolic, SYSTOLIC_RANGE)
    problems += _in_range("diastolic", diastolic, DIASTOLIC_RANGE)
    if pulse is not None:
        problems += _in_range("pulse", pulse, PULSE_RANGE)
    if systolic is not None and diastolic is not None and systolic <= diastolic:
        problems.append("systolic must be greater than diastolic")
    return problems


def check_glucose(glucose_mgdl: float | None) -> list[str]:
    return _in_range("glucose", glucose_mgdl, GLUCOSE_MGDL_RANGE)


def bp_deviates(value: float, history: list[float]) -> bool:
    if len(history) < MIN_HISTORY:
        return False
    usual = median(history)
    return abs(value - usual) > BP_MAX_RELATIVE_DEVIATION * usual


def glucose_deviates(value: float, history: list[float]) -> bool:
    if len(history) < MIN_HISTORY:
        return False
    usual = median(history)
    return value > usual * GLUCOSE_MAX_RATIO or value < usual / GLUCOSE_MAX_RATIO


def check_plausibility(
    reading_type: str,
    *,
    systolic: float | None = None,
    diastolic: float | None = None,
    pulse: float | None = None,
    glucose_mgdl: float | None = None,
    history: list[Reading] | None = None,
) -> PlausibilityResult:
    """Check one reading against §6.1 and the parent's recent confirmed `history`."""
    history = history or []
    flags: list[str] = []
    if reading_type == "bp":
        problems = check_bp(systolic, diastolic, pulse)
        if not problems:
            for field, value in (("systolic", systolic), ("diastolic", diastolic)):
                past = [getattr(h, field) for h in history if getattr(h, field) is not None]
                if bp_deviates(value, past):  # type: ignore[arg-type]
                    flags.append(f"{field} {value:g} is far from usual ({median(past):g})")
    elif reading_type == "glucose":
        problems = check_glucose(glucose_mgdl)
        past = [h.glucose_mgdl for h in history if h.glucose_mgdl is not None]
        if not problems and glucose_deviates(glucose_mgdl, past):  # type: ignore[arg-type]
            flags.append(f"glucose {glucose_mgdl:g} is far from usual ({median(past):g})")
    else:
        problems = [f"unknown reading type {reading_type!r}"]
    return PlausibilityResult(ok=not problems, problems=problems, flags=flags)
