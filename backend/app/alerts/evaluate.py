"""Evaluate a confirmed reading against the parent's limits, with the history it needs.

Shared by the MCP `alert_family` tool (server-side re-check) and the agent (deciding
whether to call it), so both always see the same evaluation.
"""

from datetime import timedelta

from app.alerts.thresholds import Evaluation, effective_thresholds, evaluate_reading
from app.storage.base import Store
from app.storage.models import Reading

HISTORY_WINDOW = timedelta(days=8)  # weekly over-limit count + repeat-BP window


def evaluate_confirmed_reading(store: Store, reading: Reading) -> Evaluation:
    t = effective_thresholds(store.list_thresholds(reading.parent_id))
    history = store.list_readings(
        reading.parent_id,
        since=reading.created_at - HISTORY_WINDOW,
        until=reading.created_at,
        type=reading.type,
        status="confirmed",
    )
    return evaluate_reading(reading, history, t)
