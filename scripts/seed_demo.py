"""Seed a demo family: one parent, two children, medicines and 14 days of readings.

Run from backend/ with the servers stopped (uses DATABASE_URL, default ./khayal.db,
and wipes it first):
    cd backend && uv run python ../scripts/seed_demo.py
or let the start script do it: uv run python ../scripts/start.py --seed

Numbers are synthetic but realistic for a 70-year-old on BP and diabetes medicines:
morning BP most days, some evening BP, fasting glucose most days, a few post-meal values,
the odd missed day and missed dose. Deterministic (fixed random seed).
"""

import random
import sys
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.alerts.thresholds import effective_thresholds, evaluate_reading  # noqa: E402
from app.storage.base import Store  # noqa: E402
from app.storage.models import (  # noqa: E402
    Alert,
    DoseEvent,
    Family,
    Medicine,
    Member,
    Reading,
)

IST = ZoneInfo("Asia/Kolkata")
DAYS = 14
PARENT_REF = "+919800000001"  # simulator "phone number" of the parent
CHILD_REFS = ("+919800000002", "+919800000003")


def _at(day: datetime, hhmm: str, jitter_min: int, rng: random.Random) -> datetime:
    h, m = map(int, hhmm.split(":"))
    local = datetime.combine(day.date(), time(h, m), IST)
    return local + timedelta(minutes=rng.randint(-jitter_min, jitter_min))


def seed(store: Store, now: datetime | None = None, rng_seed: int = 7) -> dict[str, str]:
    rng = random.Random(rng_seed)
    now = (now or datetime.now(IST)).astimezone(IST)
    consent_at = now - timedelta(days=DAYS + 2)

    family = store.add_family(Family(name="Sharma family"))
    parent = store.add_member(
        Member(
            family_id=family.id,
            role="parent",
            name="Kamla Sharma",
            channel="simulator",
            channel_ref=PARENT_REF,
            language="hi",
            consent_status="granted",
            consent_at=consent_at,
        )
    )
    children = [
        store.add_member(
            Member(
                family_id=family.id,
                role="child",
                name=name,
                channel="simulator",
                channel_ref=ref,
                language="en",
                consent_status="granted",
                consent_at=consent_at,
            )
        )
        for name, ref in zip(("Ravi Sharma", "Priya Sharma"), CHILD_REFS, strict=True)
    ]

    # Doctor-set limits (same values as the defaults, but labelled "set by your doctor").
    for rule, value in (
        ("bp_limit_systolic", 140),
        ("bp_limit_diastolic", 90),
        ("glucose_amber_above", 250),
    ):
        store.set_threshold(parent.id, rule, value, set_by="doctor")

    meds = [
        Medicine(
            parent_id=parent.id,
            brand="Telma 40",
            generic="Telmisartan",
            strength="40 mg",
            times=["08:00"],
            stock_count=6,
            daily_dose=1,
            confirmed=True,
        ),
        Medicine(
            parent_id=parent.id,
            brand="Glycomet 500",
            generic="Metformin",
            strength="500 mg",
            times=["08:30", "20:30"],
            stock_count=44,
            daily_dose=2,
            confirmed=True,
        ),
        Medicine(
            parent_id=parent.id,
            brand="Ecosprin 75",
            generic="Aspirin",
            strength="75 mg",
            times=["14:00"],
            stock_count=25,
            daily_dose=1,
            confirmed=True,
        ),
        Medicine(
            parent_id=parent.id,
            brand="Atorva 10",
            generic="Atorvastatin",
            strength="10 mg",
            times=["21:30"],
            stock_count=18,
            daily_dose=1,
            confirmed=True,
        ),
    ]
    for med in meds:
        store.add_medicine(med)

    readings: list[Reading] = []
    for offset in range(DAYS, 0, -1):
        day = now - timedelta(days=offset)
        if 1 < offset < DAYS and rng.random() < 0.12:  # skipped day (never first/last)
            continue
        # Morning BP (most days), a mild upward drift in week 2.
        drift = 4 if offset <= 7 else 0
        sys_ = round(rng.gauss(134 + drift, 7))
        dia = round(rng.gauss(84 + drift / 2, 4))
        readings.append(
            Reading(
                parent_id=parent.id,
                type="bp",
                systolic=sys_,
                diastolic=dia,
                pulse=rng.randint(66, 84),
                source="photo",
                confidence=0.95,
                created_at=_at(day, "08:15", 20, rng),
            )
        )
        if rng.random() < 0.45:  # evening BP
            readings.append(
                Reading(
                    parent_id=parent.id,
                    type="bp",
                    systolic=round(rng.gauss(130 + drift, 7)),
                    diastolic=round(rng.gauss(82, 4)),
                    pulse=rng.randint(64, 82),
                    source="photo",
                    confidence=0.93,
                    created_at=_at(day, "19:30", 30, rng),
                )
            )
        if rng.random() < 0.85:  # fasting glucose
            readings.append(
                Reading(
                    parent_id=parent.id,
                    type="glucose",
                    glucose_mgdl=round(rng.gauss(118, 12)),
                    context="fasting",
                    source="photo",
                    confidence=0.96,
                    created_at=_at(day, "07:30", 20, rng),
                )
            )
        if rng.random() < 0.3:  # post-meal glucose
            readings.append(
                Reading(
                    parent_id=parent.id,
                    type="glucose",
                    glucose_mgdl=round(rng.gauss(178, 18)),
                    context="post_meal",
                    source="photo",
                    confidence=0.94,
                    created_at=_at(day, "14:30", 30, rng),
                )
            )
    # One low fasting value five days ago so the dashboard shows an amber alert.
    readings.append(
        Reading(
            parent_id=parent.id,
            type="glucose",
            glucose_mgdl=64,
            context="fasting",
            source="photo",
            confidence=0.97,
            created_at=_at(now - timedelta(days=5), "06:50", 0, rng),
        )
    )

    readings.sort(key=lambda r: r.created_at)
    t = effective_thresholds(store.list_thresholds(parent.id))
    alerted: set[tuple[str, str]] = set()
    confirmed: list[Reading] = []
    for r in readings:
        r.status = "confirmed"
        r.confirmed_at = r.created_at + timedelta(minutes=rng.randint(1, 4))
        store.add_reading(r)
        ev = evaluate_reading(r, confirmed, t)
        key = (ev.rule or "", r.created_at.astimezone(IST).date().isoformat())
        if ev.level != "none" and key not in alerted:  # one alert per rule per day
            alerted.add(key)
            store.add_alert(
                Alert(
                    parent_id=parent.id,
                    level=ev.level,
                    reason=ev.reason,
                    reading_id=r.id,
                    notified_member_ids=[c.id for c in children],
                    created_at=r.confirmed_at,
                )
            )
        confirmed.append(r)

    doses = 0
    for med in meds:
        for offset in range(DAYS, 0, -1):
            day = now - timedelta(days=offset)
            for hhmm in med.times:
                due = _at(day, hhmm, 0, rng)
                missed = rng.random() < 0.07
                store.add_dose_event(
                    DoseEvent(
                        medicine_id=med.id,
                        due_at=due,
                        status="missed" if missed else "taken",
                        responded_at=None
                        if missed
                        else due + timedelta(minutes=rng.randint(2, 40)),
                    )
                )
                doses += 1

    return {
        "family_id": family.id,
        "parent_id": parent.id,
        "parent_sender": f"simulator:{PARENT_REF}",
        "child_ids": ",".join(c.id for c in children),
        "readings": str(len(readings)),
        "dose_events": str(doses),
        "alerts": str(len(store.list_alerts(parent.id))),
    }


def main() -> None:
    from app.config import get_settings
    from app.storage import set_store
    from app.storage.sqlite_store import SQLiteStore, path_from_url

    path = path_from_url(get_settings().database_url)
    if path != ":memory:":
        for suffix in ("", "-wal", "-shm"):  # WAL files would resurrect the old data
            Path(path + suffix).unlink(missing_ok=True)
    store = SQLiteStore(path)
    set_store(store)
    summary = seed(store)
    store.close()
    print(f"Seeded {path}")
    for key, value in summary.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
