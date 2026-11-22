"""Values read from a meter -> an UNCONFIRMED Reading, after the §6.1 plausibility rules.

One place for this step, used by both the MCP `log_reading` tool and the conversation
state machine, so the two can never disagree about what is plausible.
"""

from datetime import datetime, timedelta

from app.reader.rules import PlausibilityResult, check_plausibility, to_mgdl
from app.storage.base import Store
from app.storage.models import Reading, ReadingSource, ReadingType, utcnow

HISTORY_DAYS = 30


def build_reading(
    store: Store,
    parent_id: str,
    type: ReadingType,
    *,
    systolic: int | None = None,
    diastolic: int | None = None,
    pulse: int | None = None,
    glucose_value: float | None = None,
    glucose_unit: str | None = None,
    context: str | None = None,
    source: ReadingSource = "photo",
    confidence: float | None = None,
    image_ref: str | None = None,
    now: datetime | None = None,
) -> tuple[Reading | None, PlausibilityResult]:
    """Return (unsaved unconfirmed reading, check). The reading is None if a rule failed."""
    now = now or utcnow()
    is_bp = type == "bp"
    glucose = None
    if type == "glucose" and glucose_value is not None:
        glucose = to_mgdl(glucose_value, glucose_unit)
    history = store.list_readings(
        parent_id, since=now - timedelta(days=HISTORY_DAYS), type=type, status="confirmed"
    )
    check = check_plausibility(
        type,
        systolic=systolic if is_bp else None,
        diastolic=diastolic if is_bp else None,
        pulse=pulse if is_bp else None,
        glucose_mgdl=glucose,
        history=history,
    )
    if not check.ok:
        return None, check
    reading = Reading(
        parent_id=parent_id,
        type=type,
        systolic=systolic if is_bp else None,
        diastolic=diastolic if is_bp else None,
        pulse=pulse if is_bp else None,
        glucose_mgdl=glucose,
        context=context,
        status="unconfirmed",
        source=source,
        confidence=confidence,
        image_ref=image_ref,
        created_at=now,
    )
    return reading, check
