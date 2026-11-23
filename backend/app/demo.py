"""Demo data for the web simulator: one fresh family and the sample photos.

The simulator starts the parent with consent *pending*, so the golden path begins with the
consent notice. `scripts/seed_demo.py` seeds 14 days of history instead (dashboard).
"""

from app.reader.labels import SAMPLES_DIR, load_labels
from app.storage.base import Store
from app.storage.models import Family, Member

PARENT_REF = "+919800000001"
CHILDREN = (("Ravi Gowda", "+919800000002"), ("Priya Gowda", "+919800000003"))
DOCTOR_LIMITS = (
    ("bp_limit_systolic", 140),
    ("bp_limit_diastolic", 90),
    ("glucose_amber_above", 250),
)


def seed_fresh_family(store: Store) -> Member:
    """Gowda family on the simulator channel; returns the parent (consent pending)."""
    family = store.add_family(Family(name="Gowda family"))
    parent = store.add_member(
        Member(
            family_id=family.id,
            role="parent",
            name="Lakshmi Gowda",
            channel="simulator",
            channel_ref=PARENT_REF,
            language="en",
        )
    )
    for name, ref in CHILDREN:
        if store.find_member_by_channel("simulator", ref):
            continue  # number already registered elsewhere: keep that member
        store.add_member(
            Member(
                family_id=family.id,
                role="child",
                name=name,
                channel="simulator",
                channel_ref=ref,
                consent_status="granted",
            )
        )
    for rule, value in DOCTOR_LIMITS:
        store.set_threshold(parent.id, rule, value, set_by="doctor")
    return parent


def ensure_demo_family(store: Store) -> Member:
    """The simulator's parent, seeding a fresh family if the database has none."""
    return store.find_member_by_channel("simulator", PARENT_REF) or seed_fresh_family(store)


def sample_photos() -> list[dict[str, str]]:
    """Sample photos the simulator's picker offers (only files that exist)."""
    out = []
    for label in load_labels(SAMPLES_DIR):
        if (SAMPLES_DIR / label.file).is_file():
            out.append({"file": label.file, "kind": label.kind, "note": label.note or ""})
    return out


def load_sample(name: str) -> bytes | None:
    """Bytes of a listed sample photo; None for anything else (no path tricks)."""
    if name not in {s["file"] for s in sample_photos()}:
        return None
    return (SAMPLES_DIR / name).read_bytes()
