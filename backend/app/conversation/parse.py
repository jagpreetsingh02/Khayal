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


# --- voice notes (speech-to-text output) ---
# Speech arrives as a sentence ("my BP is 142 by 88", "मैंने दवा ले ली"). normalize_speech()
# turns it into the same short forms typed replies use, so a spoken reading still goes through
# the read-back, and anything else is still off-topic.

_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_TAMIL_DIGITS = str.maketrans("௦௧௨௩௪௫௬௭௮௯", "0123456789")
_SPOKEN_BP = re.compile(
    r"(\d{2,3})\s*(?:/|by|over|upon|bata|बटा|बाई|बाय|ओवर)\s*(\d{2,3})"
    r"(?:.*?(?:pulse|पल्स)\s*(?:is\s*)?(\d{2,3}))?"
)
_BP_WORDS = ("bp", "blood pressure", "बीपी", "बी पी", "ब्लड प्रेशर", "இரத்த அழுத்தம்")
_SUGAR_WORDS = ("sugar", "glucose", "shugar", "शुगर", "शक्कर", "ग्लूकोज", "சர்க்கரை")
_NUMBER_ANY = re.compile(r"(\d{2,3}(?:\.\d)?)\s*(mg/dl|mg|mmol/l|mmol)?")
_SPOKEN_NOT_TAKEN = (
    "didn't take",
    "did not take",
    "not taken",
    "missed",
    "skipped",
    "nahi li",
    "nahin li",
    "नहीं ली",
    "नही ली",
    "नहीं खाई",
    "எடுக்கவில்லை",
)
_SPOKEN_TAKEN = (
    "took my medicine",
    "taken my medicine",
    "took the medicine",
    "took my tablet",
    "took my medicines",
    "taken my medicines",
    "i took it",
    "have taken it",
    "dawai le li",
    "dawa le li",
    "dava le li",
    "goli le li",
    "dawai kha li",
    "le li",
    "दवा ले ली",
    "दवाई ले ली",
    "दवा खा ली",
    "गोली ले ली",
    "गोली खा ली",
    "ले ली",
    "खा ली",
    "மருந்து எடுத்தேன்",
    "மாத்திரை எடுத்தேன்",
    "எடுத்துக்கொண்டேன்",
)
_SPOKEN_YES = (
    "yes",
    "haan",
    "han ji",
    "ji haan",
    "sahi hai",
    "correct",
    "right",
    "हाँ",
    "हां",
    "जी हाँ",
    "जी",
    "सही है",
    "ஆம்",
    "சரி",
)
_SPOKEN_NO = ("no", "nahi", "galat", "wrong", "नहीं", "नही", "गलत", "இல்லை", "தவறு")


def normalize_speech(text: str | None) -> str:
    """A voice-note transcript -> a short reply form ("142/88", "sugar 110", "taken", "1", "2",
    "stop"), or the cleaned sentence if nothing matched (then it is off-topic)."""
    t = normalize(text).translate(_DEVANAGARI_DIGITS).translate(_TAMIL_DIGITS)
    t = re.sub(r"[,.;!?।]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if t in STOP or t in START:
        return t
    if m := _SPOKEN_BP.search(t):
        sys_, dia, pulse = m.groups()
        return f"{sys_}/{dia}" + (f" {pulse}" if pulse else "")
    if any(w in t for w in _SUGAR_WORDS) and (m := _NUMBER_ANY.search(t)):
        value, unit = m.groups()
        return f"sugar {value}" + (f" {unit}" if unit else "")
    if any(w in t for w in _BP_WORDS) and len(nums := re.findall(r"\d{2,3}", t)) >= 2:
        return f"{nums[0]}/{nums[1]}"
    if any(p in t for p in _SPOKEN_NOT_TAKEN):
        return "2"
    if any(p in t for p in _SPOKEN_TAKEN):
        return "taken"
    words = t.split()
    if words and (t in _SPOKEN_NO or words[0] in _SPOKEN_NO):
        return "2"
    if words and (t in _SPOKEN_YES or words[0] in _SPOKEN_YES):
        return "1"
    return t
