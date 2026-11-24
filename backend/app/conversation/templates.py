"""Everything the bot says to a parent, in one place (English first; Hindi comes next).

Rules (brief §6.4): short messages, numbered replies, numbers against doctor-set limits only,
never a diagnosis or dosing advice.
"""

from app.alerts.thresholds import BP_WARNING_SIGNS
from app.storage.models import Reading

CONSENT_TEXT_VERSION = "2026-11-v1"

CONSENT_NOTICE = (
    "Namaste {name}! Your family invited you to Khayal. Send a photo of your BP or sugar "
    "meter and I will note the numbers, remind you about medicines, and share readings and "
    "alerts with your family. Reply 1 (Haan / Yes) to agree. Reply STOP any time to pause."
)
CONSENT_THANKS = (
    "Thank you. Send a photo of your BP or sugar meter screen any time, or a photo of a "
    "medicine strip."
)
PAUSED_NOW = "Paused. Nothing new will be shared with your family. Reply START to turn it on again."
PAUSED = "Khayal is paused. Reply START to turn it on again."
RESUMED = "Khayal is on again. " + CONSENT_THANKS

OFF_TOPIC = "I can only help with readings and medicines."
VOICE_UNCLEAR = (
    "Sorry, I could not understand the voice note. Please say it again, or type it "
    "(for example 142/88 or sugar 110)."
)
HELP = (
    "Send a photo of your BP or sugar meter screen, or of a medicine strip. You can also "
    "type a reading, like 142/88 or sugar 110."
)
NOTHING_TO_CONFIRM = "There is nothing to confirm right now. " + HELP
UNKNOWN_SENDER = "This number is not registered with Khayal. Please ask your family to add you."
NOT_A_PARENT = "This chat is for your parent's readings. Please use the family dashboard."

RETAKE_TIPS = (
    "Please take the photo again: hold the phone straight, avoid light reflection, and fill "
    "the frame with the screen."
)
ISSUE_TIPS = {
    "glare": "there is light reflecting on the screen",
    "angle": "the photo is taken at an angle",
    "partial": "part of the screen is cut off",
    "blur": "the photo is blurred",
}
TYPE_INSTEAD = "Or type the numbers, like 142/88 or sugar 110."
READ_FAILED = "Sorry, I could not read the photo just now. Please try again in a minute. " + (
    TYPE_INSTEAD
)
IMPLAUSIBLE = "The numbers I read don't look right. "
UNUSUAL = "This is very different from your usual readings, please check. "

READ_BACK = "I read {value} — correct? Reply 1 yes / 2 no"
PLEASE_ANSWER = "Please reply 1 if {value} is correct, or 2 if it is not."
SAVED = "Saved: {value}. Thank you."
REJECTED = "OK, I did not save it. " + RETAKE_TIPS + " " + TYPE_INSTEAD
EXPIRED = (
    "Your earlier reading ({value}) was not confirmed within 30 minutes, so I did not save it."
)

# Severe BP (2025 AHA/ACC): one warning-signs question before anything else.
SYMPTOM_CHECK_BP = (
    "Saved: {value}. This is at or above the limit your doctor set. Do you have any of these "
    f"right now: {BP_WARNING_SIGNS}? Reply 1 yes / 2 no"
)
SYMPTOM_CHECK_BP_REPEAT = (
    "Saved: {value}. The repeat reading is still at or above the limit your doctor set. Do you "
    f"have any of these right now: {BP_WARNING_SIGNS}? Reply 1 yes / 2 no"
)
SYMPTOM_PLEASE_ANSWER = (
    f"Please answer first: do you have any of these right now: {BP_WARNING_SIGNS}? "
    "Reply 1 yes / 2 no"
)
BP_CALL_NOW = "Please call 108 or 112 now, or ask someone near you to call. {family}"
REPEAT_ASK = (
    "Thank you. Please sit and rest for 5 minutes, then measure again and send a photo of the "
    "new reading. If any of those signs start, call 108/112."
)
REPEAT_ASK_NO_REPLY = (
    "I did not get an answer. Please sit and rest for 5 minutes, then measure again and send a "
    "photo of the new reading. If you have chest pain, breathlessness or any other of those "
    "signs, call 108/112."
)
RED_REPEAT_URGENT = (
    "Saved: {value}. The repeat reading is still at or above the limit your doctor set. "
    "{family} Please contact your doctor today. If any of those signs start, call 108/112."
)
# Low sugar (ADA 2026). Guidance is only ever the doctor's own plan, never Khayal's advice.
PLAN = "Your doctor's plan: {plan}"
NO_PLAN = "Please follow your doctor's plan for low sugar."
RECHECK_ASK = "Please check your sugar again in 15 minutes and send the reading."
LOW_SUGAR_QUESTION = (
    "Did you faint, feel confused, or did someone have to help you? Reply 1 yes / 2 no"
)
LOW_SUGAR_PLEASE_ANSWER = "Please answer: " + LOW_SUGAR_QUESTION
LOW_SUGAR_HELP = (
    "Please ask someone near you to help now, and call 108/112 if you feel faint or confused. "
    "{family} {plan}"
)
LOW_SUGAR_NO_SIGNS = "Thank you. " + RECHECK_ASK
LOW_RED = "Saved: {value}. It is below the limit your doctor set. {family} {plan} " + (
    RECHECK_ASK + " If you feel unwell, call 108/112."
)
RECHECK_OK = "Saved: {value}. Thank you for checking again."
NO_RECHECK_FAMILY_TOLD = (
    "No new sugar reading came after {value}, so I let your family know. You can still send it."
)
NO_RECHECK_NOT_SHARED = (
    "No new sugar reading came after {value}. You chose not to share alerts, so your family "
    "was not told. You can still send it."
)
NO_RECHECK_NOT_REACHED = (
    "No new sugar reading came after {value}. I could not reach your family just now. You can "
    "still send it."
)
REPEAT_TOO_SOON = (
    "Please rest {minutes} more minute(s) before the repeat reading, then send the photo."
)
REPEAT_OK = "Saved: {value}. Thank you for taking the repeat reading."
EMERGENCY = "If you feel unwell, call 108/112."
FAMILY_TOLD = "Saved: {value}. It is outside the limit your doctor set, so I let your family know."
FAMILY_NOT_REACHED = (
    "Saved: {value}. It is outside the limit your doctor set. I could not reach your family "
    "just now."
)
FAMILY_NOT_SHARED = (
    "Saved: {value}. It is outside the limit your doctor set. You chose not to share alerts, "
    "so your family was not told."
)
RED_REPEAT = "Saved: {value}. The repeat reading is still at or above the limit your doctor set."
NO_REPEAT_FAMILY_TOLD = (
    "No repeat reading came after {value}, so I let your family know. You can still send the "
    "repeat reading. " + EMERGENCY
)
NO_REPEAT_NOT_SHARED = (
    "No repeat reading came after {value}. You chose not to share alerts, so your family was "
    "not told. You can still send the repeat reading. " + EMERGENCY
)
NO_REPEAT_NOT_REACHED = (
    "No repeat reading came after {value}. I could not reach your family just now. You can "
    "still send the repeat reading. " + EMERGENCY
)

# Refill status (the order itself never happens in chat, brief §3).
LOW_STOCK_ASKED = (
    "Your {brand} is running low ({stock} left). I have asked your family to approve a refill."
)
REFILL_APPROVED = "Refill approved for {brand} ({qty}); your pharmacy will confirm."
REFILL_DECLINED = "Your family did not approve the refill for {brand} this time."

STRIP_READ_BACK = "{name} = {generic}. Remind at 8 PM? Reply 1 yes / 2 no"
STRIP_UNREADABLE = "I could not read the medicine name. " + (
    "Please send a clear photo of the side of the strip with the name and strength."
)
STRIP_UNKNOWN = (
    "I could not match this strip to a medicine in my list. Please send a clearer photo of "
    "the name and strength, or ask your family to add it on the dashboard."
)
STRIP_PLEASE_ANSWER = "Please reply 1 to get a reminder at 8 PM for {name}, or 2 for no."
STRIP_ASK_TIME = (
    "OK, no reminder at 8 PM. To pick another time, reply with a time like 9:00 or 21:30. "
    "If the medicine name is wrong, send a clearer photo of the strip."
)
STRIP_TIME_FORMAT = "Please reply with a time like 9:00, 9 PM or 21:30, or 2 for no reminder."
REMINDER_SET = "Done. I will remind you to take {name} at {times} every day."
REMINDER_FAILED = "I saved {name}, but could not set the reminder just now. Please try again later."
NO_REMINDER = "OK, no reminder set."

DOSE_REMINDER = "{time}: time for {names}. Reply 1 when taken, 2 if skipped."
DOSE_FOLLOWUP = "Did you take {names} ({time})? Reply 1 if you have taken it."
DOSE_TAKEN = "Noted: {names} taken. Thank you."
DOSE_SKIPPED = "Noted: {names} not taken."


def plan_line(plan: str | None) -> str:
    return PLAN.format(plan=plan) if plan else NO_PLAN


def family_line(alert_sent: bool, not_shared: bool) -> str:
    if alert_sent:
        return "I have told your family."
    if not_shared:
        return "You chose not to share alerts, so your family was not told."
    return "I could not reach your family just now."


def format_value(reading: Reading, shown: str | None = None) -> str:
    """How a reading is read back: "142/88, pulse 76" or "sugar 112 mg/dL"."""
    if reading.type == "bp":
        value = f"{reading.systolic}/{reading.diastolic}"
        return f"{value}, pulse {reading.pulse}" if reading.pulse else value
    if reading.display == "LO":
        return f"sugar LO (below {reading.glucose_mgdl:g} mg/dL)"
    if reading.display == "HI":
        return f"sugar HI (above {reading.glucose_mgdl:g} mg/dL)"
    mgdl = f"sugar {reading.glucose_mgdl:g} mg/dL"
    return f"{mgdl} ({shown})" if shown else mgdl


def read_back(value: str, unusual: bool = False) -> str:
    return (UNUSUAL if unusual else "") + READ_BACK.format(value=value)


def retake_message(issues: list[str], prefix: str = "I could not read the screen clearly. ") -> str:
    reasons = [ISSUE_TIPS[i] for i in issues if i in ISSUE_TIPS]
    why = f"({'; '.join(reasons)}) " if reasons else ""
    return f"{prefix}{why}{RETAKE_TIPS} {TYPE_INSTEAD}"


def clock_12h(hhmm: str) -> str:
    """ "20:00" -> "8:00 PM"."""
    hour, minute = (int(x) for x in hhmm.split(":"))
    suffix = "AM" if hour < 12 else "PM"
    return f"{hour % 12 or 12}:{minute:02d} {suffix}"


def join_names(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
