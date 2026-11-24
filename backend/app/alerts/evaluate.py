"""Evaluate a confirmed reading against the parent's limits, with the history it needs.

Shared by the MCP `alert_family` tool (server-side re-check) and the agent (deciding
whether to call it), so both always see the same evaluation.
"""

from datetime import datetime, timedelta

from app.alerts.thresholds import (
    LEVEL_RANK,
    Evaluation,
    effective_thresholds,
    evaluate_bp_no_repeat,
    evaluate_glucose_no_recheck,
    evaluate_reading,
)
from app.storage.base import NotFoundError, Store
from app.storage.models import Reading

HISTORY_WINDOW = timedelta(days=8)  # weekly over-limit count + repeat-BP window


def whose_doctor(store: Store, parent_id: str) -> str:
    """ "her" / "his" / "their", as the family set it at onboarding."""
    try:
        return store.get_member(parent_id).possessive
    except NotFoundError:
        return "their"


def evaluate_confirmed_reading(store: Store, reading: Reading) -> Evaluation:
    t = effective_thresholds(store.list_thresholds(reading.parent_id))
    history = store.list_readings(
        reading.parent_id,
        since=reading.created_at - HISTORY_WINDOW,
        until=reading.created_at,
        type=reading.type,
        status="confirmed",
    )
    return evaluate_reading(reading, history, t, whose_doctor(store, reading.parent_id))


def evaluate_for_alert(store: Store, reading: Reading, now: datetime) -> list[Evaluation]:
    """Every alert that may be sent to the family for this confirmed reading right now,
    most urgent first (alert_family sends the first one not sent yet).

    - A first severe BP: nothing until 15 minutes have passed without a confirmed repeat,
      then amber "no repeat taken" (unless warning signs were reported: red at once).
    - A low (54-69) glucose: its amber, plus amber "no recheck" once 15 minutes passed
      without a confirmed recheck.
    """
    evaluation = evaluate_confirmed_reading(store, reading)
    t = effective_thresholds(store.list_thresholds(reading.parent_id))
    whose = whose_doctor(store, reading.parent_id)
    if evaluation.rule == "bp_severe_first":
        later = store.list_readings(
            reading.parent_id, since=reading.created_at, type="bp", status="confirmed"
        )
        return [evaluate_bp_no_repeat(reading, later, now, t, whose=whose)]
    found = [evaluation]
    if reading.type == "glucose":
        later = store.list_readings(
            reading.parent_id, since=reading.created_at, type="glucose", status="confirmed"
        )
        recheck = evaluate_glucose_no_recheck(reading, later, now, t, whose)
        if recheck.level != "none":
            found.append(recheck)
    found = [e for e in found if e.level != "none"] or [evaluation]
    return sorted(found, key=lambda e: -LEVEL_RANK[e.level])
