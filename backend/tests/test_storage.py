from datetime import UTC, datetime, timedelta

import pytest

from app.storage.base import ConflictError, NotFoundError
from app.storage.models import (
    Alert,
    AuditEntry,
    ConversationState,
    DoseEvent,
    Family,
    Medicine,
    Member,
    Reading,
    Refill,
)
from app.storage.sqlite_store import SQLiteStore, path_from_url


@pytest.fixture
def store():
    s = SQLiteStore(":memory:")
    yield s
    s.close()


@pytest.fixture
def parent(store):
    fam = store.add_family(Family(name="Sharma"))
    return store.add_member(
        Member(
            family_id=fam.id,
            role="parent",
            name="Kamla",
            channel="simulator",
            channel_ref="+911111111111",
        )
    )


def test_path_from_url():
    assert path_from_url("sqlite:///./care_circle.db") == "./care_circle.db"
    assert path_from_url("sqlite:///:memory:") == ":memory:"
    with pytest.raises(ValueError):
        path_from_url("postgres://x")


def test_family_and_member_roundtrip(store, parent):
    assert store.get_member(parent.id) == parent
    assert store.get_family(parent.family_id).name == "Sharma"
    child = store.add_member(
        Member(
            family_id=parent.family_id,
            role="child",
            name="Ravi",
            channel="telegram",
            channel_ref="42",
        )
    )
    assert [m.id for m in store.list_members(parent.family_id, role="child")] == [child.id]
    assert len(store.list_members(parent.family_id)) == 2


def test_sender_mapping_is_by_channel_and_ref(store, parent):
    assert store.find_member_by_channel("simulator", "+911111111111").id == parent.id
    assert store.find_member_by_channel("telegram", "+911111111111") is None
    assert store.find_member_by_channel("simulator", "+919999999999") is None


def test_missing_rows_raise_not_found(store):
    with pytest.raises(NotFoundError):
        store.get_reading("rd_nope")
    with pytest.raises(NotFoundError):
        store.set_reading_status("rd_nope", "confirmed")


def test_readings_filter_and_order(store, parent):
    now = datetime.now(UTC)
    old = store.add_reading(
        Reading(
            parent_id=parent.id,
            type="bp",
            systolic=130,
            diastolic=80,
            created_at=now - timedelta(days=10),
        )
    )
    # microsecond vs whole-second timestamps must still sort correctly
    a = store.add_reading(
        Reading(
            parent_id=parent.id,
            type="glucose",
            glucose_mgdl=110,
            created_at=now.replace(microsecond=0) - timedelta(hours=1),
        )
    )
    b = store.add_reading(
        Reading(
            parent_id=parent.id,
            type="bp",
            systolic=140,
            diastolic=90,
            created_at=now.replace(microsecond=500) - timedelta(hours=1, seconds=-1),
        )
    )
    recent = store.list_readings(parent.id, since=now - timedelta(days=7))
    assert [r.id for r in recent] == [a.id, b.id]
    assert [r.id for r in store.list_readings(parent.id, type="bp")] == [old.id, b.id]
    assert store.list_readings(parent.id, status="confirmed") == []


def test_confirm_reading_sets_confirmed_at(store, parent):
    r = store.add_reading(Reading(parent_id=parent.id, type="bp", systolic=130, diastolic=80))
    assert r.status == "unconfirmed" and r.confirmed_at is None
    confirmed = store.set_reading_status(r.id, "confirmed")
    assert confirmed.status == "confirmed" and confirmed.confirmed_at is not None


def test_medicines_doses_and_refills(store, parent):
    med = store.add_medicine(
        Medicine(
            parent_id=parent.id,
            brand="Telma 40",
            generic="Telmisartan",
            strength="40 mg",
            times=["08:00"],
            stock_count=10,
        )
    )
    assert store.get_medicine(med.id).times == ["08:00"]
    assert store.set_medicine_times(med.id, ["08:00", "20:00"]).times == ["08:00", "20:00"]
    due = datetime.now(UTC)
    ev = store.add_dose_event(DoseEvent(medicine_id=med.id, due_at=due))
    assert store.set_dose_status(ev.id, "taken").status == "taken"
    assert len(store.list_dose_events(parent.id, since=due - timedelta(minutes=1))) == 1

    rf = store.add_refill(Refill(medicine_id=med.id, qty=30))
    assert store.list_refills(parent.id, status="pending_approval")[0].id == rf.id
    decided = store.decide_refill(rf.id, "approved", member_id="mem_child")
    assert decided.status == "approved" and decided.decided_by_member_id == "mem_child"
    with pytest.raises(ConflictError):  # decided once; a second decision is refused
        store.decide_refill(rf.id, "rejected", member_id="mem_other")
    assert store.get_refill(rf.id).decided_by_member_id == "mem_child"
    sent = store.set_refill_pharmacy(rf.id, "sent_to_pharmacy", "DEMO-1")
    assert (sent.status, sent.pharmacy_ref, sent.decided_by_member_id) == (
        "sent_to_pharmacy",
        "DEMO-1",
        "mem_child",
    )
    assert store.set_medicine_stock(med.id, 3).stock_count == 3


def test_sharing_defaults_and_alert_acknowledge(store, parent):
    from app.storage.models import Alert, Sharing

    assert store.get_sharing(parent.id).allows("medicines")  # default: all shared
    store.set_sharing(Sharing(parent_id=parent.id, alerts=False, updated_by=parent.id))
    s = store.get_sharing(parent.id)
    assert (s.readings, s.alerts, s.medicines) == (True, False, True) and s.updated_at
    al = store.add_alert(Alert(parent_id=parent.id, level="amber", reason="x"))
    first = store.acknowledge_alert(al.id, "mem_a")
    again = store.acknowledge_alert(al.id, "mem_b")  # first acknowledgement wins
    assert first.acknowledged_by == again.acknowledged_by == "mem_a"


def test_thresholds_upsert(store, parent):
    store.set_threshold(parent.id, "bp_red_systolic", 180, set_by="default")
    store.set_threshold(parent.id, "bp_red_systolic", 170)
    [t] = store.list_thresholds(parent.id)
    assert (t.value, t.set_by) == (170, "doctor")


def test_alerts_conversation_state_and_audit(store, parent):
    store.add_alert(
        Alert(parent_id=parent.id, level="amber", reason="test", notified_member_ids=["a", "b"])
    )
    assert store.list_alerts(parent.id)[0].notified_member_ids == ["a", "b"]

    assert store.get_conversation_state(parent.id).state == "idle"
    store.set_conversation_state(
        ConversationState(member_id=parent.id, state="awaiting", payload={"reading_id": "rd_1"})
    )
    assert store.get_conversation_state(parent.id).payload == {"reading_id": "rd_1"}

    store.add_audit(AuditEntry(actor="agent", tool="get_meds", args_json="{}", result_json="[]"))
    store.add_audit(AuditEntry(actor="agent", tool="get_history", args_json="{}", result_json="[]"))
    assert [e.tool for e in store.list_audit()] == ["get_history", "get_meds"]
    assert len(store.list_audit(tool="get_meds")) == 1
