"""Descriptive weekly summary: numbers against doctor-set limits, nothing more."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.alerts.thresholds import is_over_limit_bp
from app.storage.models import Alert, DoseEvent, Member, Reading
from mcp_server.guardrails import blocked_terms

FALLBACK_TEXT = "This week's numbers are ready on the family dashboard."


def build_summary_text(
    parent: Member,
    readings: list[Reading],
    doses: list[DoseEvent],
    alerts: list[Alert],
    t: dict[str, float],
    start: datetime,
    end: datetime,
    tz: str = "Asia/Kolkata",
) -> str:
    zone = ZoneInfo(tz)
    period = (
        f"{start.astimezone(zone):%d %b} to {(end - timedelta(seconds=1)).astimezone(zone):%d %b}"
    )
    lines = [f"Weekly summary for {parent.name} ({period})."]

    bps = [r for r in readings if r.type == "bp"]
    if bps:
        lo = min(bps, key=lambda r: (r.systolic, r.diastolic))
        hi = max(bps, key=lambda r: (r.systolic, r.diastolic))
        over = sum(is_over_limit_bp(r, t) for r in bps)
        lines.append(
            f"BP: {len(bps)} readings, from {lo.systolic}/{lo.diastolic} to "
            f"{hi.systolic}/{hi.diastolic}. {over} of {len(bps)} at or above the limit set by "
            f"the doctor ({t['bp_limit_systolic']:g}/{t['bp_limit_diastolic']:g})."
        )
    else:
        lines.append("BP: no readings this week.")

    gl = [r.glucose_mgdl for r in readings if r.type == "glucose" and r.glucose_mgdl is not None]
    if gl:
        low = sum(v < t["glucose_amber_below"] for v in gl)
        high = sum(v > t["glucose_amber_above"] for v in gl)
        lines.append(
            f"Glucose: {len(gl)} readings, from {min(gl):g} to {max(gl):g} mg/dL. "
            f"{low} below {t['glucose_amber_below']:g} and {high} above "
            f"{t['glucose_amber_above']:g} (limits set by the doctor)."
        )
    else:
        lines.append("Glucose: no readings this week.")

    answered = [d for d in doses if d.status in ("taken", "missed")]
    if answered:
        taken = sum(d.status == "taken" for d in answered)
        lines.append(
            f"Medicine reminders: {taken} of {len(answered)} marked taken, "
            f"{len(answered) - taken} missed."
        )

    amber = sum(a.level == "amber" for a in alerts)
    red = sum(a.level == "red" for a in alerts)
    lines.append(f"Alerts sent to family: {amber} amber, {red} red.")
    return "\n".join(lines)


def filter_summary(text: str) -> tuple[str, list[str]]:
    """Output filter: if any diagnosis / dosing word slips in, send the safe fallback."""
    hits = blocked_terms(text)
    return (FALLBACK_TEXT, hits) if hits else (text, [])
