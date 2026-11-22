"""Understand short parent replies: 1/2, yes/no (English + Hinglish), STOP/START,
typed readings ("142/88", "sugar 110") and reminder times ("9 pm", "21:30").

Deterministic on purpose: everything else is off-topic and gets the fixed refusal.
"""

import re

from pydantic import BaseModel

YES = {"1", "yes", "y", "haan", "haa", "ha", "han", "ji", "ji haan", "ok", "okay", "correct"}
NO = {"2", "no", "n", "nahi", "nahin", "na", "galat", "wrong"}
STOP = {"stop", "pause", "ruko", "band karo"}
START = {"start", "resume", "shuru", "chalu"}
TAKEN = {"taken", "done", "le li", "li", "kha li", "kha liya", "le liya"}
HELP = {"hi", "hello", "hey", "help", "namaste", "namaskar", "menu", "start over"}
STRIP_WORDS = ("medicine", "strip", "tablet", "dawai", "dawa", "dava", "goli")

_BP = re.compile(
    r"^(?:bp\s*:?\s*)?(\d{2,3})\s*[/\\-]\s*(\d{2,3})(?:\s*(?:pulse|p|,)?\s*(\d{2,3}))?$"
)
_GLUCOSE = re.compile(
    r"^(?:sugar|glucose|shugar)\s*:?\s*(\d{1,3}(?:\.\d)?)\s*(mg/dl|mg|mmol/l|mmol)?$"
)
_NUMBER = re.compile(r"^(\d{1,3}(?:\.\d)?)\s*(mg/dl|mg|mmol/l|mmol)?$")
_TIME = re.compile(r"^(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm|baje)?$")


class TypedReading(BaseModel):
    type: str  # "bp" | "glucose"
    systolic: int | None = None
    diastolic: int | None = None
    pulse: int | None = None
    glucose_value: float | None = None
    glucose_unit: str | None = None


def normalize(text: str | None) -> str:
    text = (text or "").strip().lower()
    text = re.sub(r"[!?।]+$", "", text).rstrip(".").strip()
    return re.sub(r"\s+", " ", text)


def is_yes(t: str) -> bool:
    return t in YES


def is_no(t: str) -> bool:
    return t in NO


def mentions_strip(t: str) -> bool:
    return any(word in t for word in STRIP_WORDS)


def _unit(raw: str | None) -> str | None:
    if not raw:
        return None
    return "mmol/L" if raw.startswith("mmol") else "mg/dL"


def parse_typed_reading(t: str, *, bare_number_is_glucose: bool = False) -> TypedReading | None:
    """ "142/88", "bp 142/88 76", "sugar 110", "glucose 6.2 mmol". A bare number counts as
    glucose only when we just asked for a value (`bare_number_is_glucose`)."""
    if m := _BP.match(t):
        sys_, dia, pulse = m.groups()
        return TypedReading(
            type="bp", systolic=int(sys_), diastolic=int(dia), pulse=int(pulse) if pulse else None
        )
    m = _GLUCOSE.match(t) or (_NUMBER.match(t) if bare_number_is_glucose else None)
    if m:
        value, unit = m.groups()
        return TypedReading(type="glucose", glucose_value=float(value), glucose_unit=_unit(unit))
    return None


def parse_time(t: str) -> str | None:
    """ "9" / "9:00" -> "09:00"; "9 pm" / "9 baje" (evening is not assumed) -> "21:00" / "09:00";
    "21:30" -> "21:30". Returns None when it is not a valid time."""
    m = _TIME.match(t)
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2) or 0)
    suffix = m.group(3)
    if m.group(2) is None and suffix is None and hour < 3:
        return None  # a bare "1" / "2" is a menu reply, not 1 AM
    if suffix in ("am", "pm"):
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if suffix == "pm" else 0)
    if hour > 23 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"
