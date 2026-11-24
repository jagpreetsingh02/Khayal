"""Descriptive weekly summary: numbers against doctor-set limits, nothing more."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from statistics import mean

from app.alerts.thresholds import HOME_BP_REFERENCE, is_over_limit_bp
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
    doctor = f"the limit {parent.possessive} doctor set"

    bps = [r for r in readings if r.type == "bp"]
    if bps:
        lo = min(bps, key=lambda r: (r.systolic, r.diastolic))
        hi = max(bps, key=lambda r: (r.systolic, r.diastolic))
        over = sum(is_over_limit_bp(r, t) for r in bps)
        lines.append(
            f"BP: {len(bps)} readings, from {lo.systolic}/{lo.diastolic} to "
            f"{hi.systolic}/{hi.diastolic}. {over} of {len(bps)} at or above {doctor} "
            f"({t['bp_limit_systolic']:g}/{t['bp_limit_diastolic']:g})."
        )
        # 2025 AHA/ACC home reference: described against, never interpreted.
        avg_s = round(mean(r.systolic or 0 for r in bps))
        avg_d = round(mean(r.diastolic or 0 for r in bps))
        ref_s, ref_d = HOME_BP_REFERENCE
        side = "at or above" if avg_s >= ref_s or avg_d >= ref_d else "below"
        lines.append(
            f"Home BP average: {avg_s}/{avg_d}, {side} the {ref_s}/{ref_d} reference for "
            "home readings."
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
            f"{t['glucose_amber_above']:g} (limits {parent.possessive} doctor set)."
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
