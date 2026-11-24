"""Family dashboard API (brief §5.6): auth, refill approval (the only approval path),
parent-first sharing, doctor-set limits, the low-stock demo through the MCP tool."""

import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.agent.rule_agent import RuleAgent
from app.alerts.notifier import StoreNotifier
from app.channels.service import KhayalService
from app.channels.simulator import SimulatorChannel
from app.config import get_settings
from app.demo import CHILDREN, PARENT_REF, load_sample, seed_fresh_family
from app.main import create_app
from app.reader.mock_reader import MockReader
from app.storage.models import Refill
from app.storage.sqlite_store import SQLiteStore
from mcp_server.server import create_server

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
TOKEN = "test-demo-token"


def _seed_module():
    spec = importlib.util.spec_from_file_location("seed_demo", SCRIPTS / "seed_demo.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


seed_demo = _seed_module()


class Dash:
    def __init__(self, client: TestClient, store: SQLiteStore, service: KhayalService):
        self.c, self.store, self.service = client, store, service
        self.parent = store.find_member_by_channel("simulator", PARENT_REF)
        self.ravi = store.find_member_by_channel("simulator", CHILDREN[0][1])
        self.priya = store.find_member_by_channel("simulator", CHILDREN[1][1])
        self.telma = next(m for m in store.list_medicines(self.parent.id) if m.brand == "Telma 40")

    def login(self, member) -> dict[str, str]:
        r = self.c.post("/api/login", json={"token": TOKEN, "member_id": member.id})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['session']}"}

    def pending_refill(self) -> Refill:
        return self.store.add_refill(Refill(medicine_id=self.telma.id, qty=30))

    def feed(self, member) -> list[str]:
        items = self.service.simulator.feed(member.channel_ref)
        return [i.text for i in items if i.direction == "out"]


@pytest.fixture
def dash(monkeypatch):
    monkeypatch.setenv("DASHBOARD_DEMO_TOKEN", TOKEN)
    monkeypatch.setenv("APP_MODE", "mock")
    get_settings.cache_clear()
    store = SQLiteStore(":memory:")
    seed_demo.seed(store)
    server = create_server(store, StoreNotifier(lambda: store))  # in-process MCP for tests
    service = KhayalService(
        store, MockReader(), RuleAgent(store, server), [SimulatorChannel(load_sample)]
    )
    with TestClient(create_app(service, tick_seconds=0)) as client:
        yield Dash(client, store, service)
    store.close()
    get_settings.cache_clear()


# ---------- auth ----------


def test_every_api_route_needs_a_session(dash):
    pid = dash.parent.id
    for path in (
        "/api/me",
        f"/api/parents/{pid}/overview",
        f"/api/parents/{pid}/readings",
        "/api/alerts",
        "/api/refills",
        f"/api/parents/{pid}/medicines",
        f"/api/parents/{pid}/thresholds",
        f"/api/parents/{pid}/sharing",
        "/api/audit",
        "/api/mcp/tools",
    ):
        assert dash.c.get(path).status_code == 401, path
    assert dash.c.post("/api/refills/rf_x/approve").status_code == 401
    forged = {"Authorization": f"Bearer {dash.ravi.id}.deadbeef"}
    assert dash.c.get("/api/me", headers=forged).status_code == 401


def test_login_checks_the_demo_token(dash):
    bad = dash.c.post("/api/login/members", json={"token": "wrong"})
    assert bad.status_code == 401
    names = [
        m["name"]
        for m in dash.c.post("/api/login/members", json={"token": TOKEN}).json()["members"]
    ]
    assert names == ["Priya Gowda", "Ravi Gowda", "Lakshmi Gowda"]  # children first
    bad = dash.c.post("/api/login", json={"token": "wrong", "member_id": dash.ravi.id})
    assert bad.status_code == 401
    me = dash.c.get("/api/me", headers=dash.login(dash.ravi)).json()
    assert me["member"]["name"] == "Ravi Gowda" and me["parents"][0]["id"] == dash.parent.id


def test_rotating_the_session_secret_ends_sessions(dash, monkeypatch):
    headers = dash.login(dash.ravi)
    monkeypatch.setenv("DASHBOARD_DEMO_TOKEN", "rotated")  # no longer signs sessions
    get_settings.cache_clear()
    assert dash.c.get("/api/me", headers=headers).status_code == 200
    monkeypatch.setenv("SESSION_SECRET", "a-different-secret-" + "x" * 20)
    get_settings.cache_clear()
    assert dash.c.get("/api/me", headers=headers).status_code == 401


# ---------- seeded data ----------


def test_seeded_data_shows_on_the_dashboard(dash):
    h, pid = dash.login(dash.ravi), dash.parent.id
    readings = dash.c.get(f"/api/parents/{pid}/readings?range=14d", headers=h).json()
    types = {r["type"] for r in readings["readings"]}
    assert types == {"bp", "glucose"} and len(readings["readings"]) > 20
    assert readings["limits"]["bp_limit_systolic"] == 140
    ov = dash.c.get(f"/api/parents/{pid}/overview", headers=h).json()
    assert ov["latest"]["bp"]["systolic"] and ov["today_doses"]
    assert any(a["level"] == "amber" for a in ov["active_alerts"])  # seeded low glucose
    meds = dash.c.get(f"/api/parents/{pid}/medicines", headers=h).json()
    assert {m["brand"] for m in meds["medicines"]} >= {"Telma 40", "Glycomet 500"}


def test_acknowledge_alert_is_recorded_once(dash):
    h = dash.login(dash.ravi)
    [alert] = [a for a in dash.c.get("/api/alerts", headers=h).json()["alerts"]][:1]
    first = dash.c.post(f"/api/alerts/{alert['id']}/acknowledge", headers=h).json()
    assert first["changed"] and first["alert"]["acknowledged_by"] == "Ravi Gowda"
    again = dash.c.post(f"/api/alerts/{alert['id']}/acknowledge", headers=dash.login(dash.priya))
    assert again.json()["changed"] is False
    assert again.json()["alert"]["acknowledged_by"] == "Ravi Gowda"
    assert dash.store.list_audit(tool="dashboard:acknowledge_alert")[0].actor == "member"


# ---------- refills: approve / reject ----------


def test_approve_records_member_sends_to_demo_pharmacy_and_tells_parent(dash):
    refill = dash.pending_refill()
    h = dash.login(dash.ravi)
    pending = dash.c.get("/api/refills?status=pending_approval", headers=h).json()["refills"]
    assert [r["id"] for r in pending] == [refill.id]

    out = dash.c.post(f"/api/refills/{refill.id}/approve", headers=h).json()
    assert out["changed"] is True
    assert out["refill"]["status"] == "sent_to_pharmacy"
    assert out["refill"]["decided_by"] == "Ravi Gowda" and out["refill"]["decided_at"]
    assert "demo, no real order" in out["refill"]["pharmacy"]
    stored = dash.store.get_refill(refill.id)
    assert stored.decided_by_member_id == dash.ravi.id and stored.pharmacy_ref.startswith("DEMO-")

    # Parent hears it in the simulator chat; the other child is told who approved.
    assert any(
        "Refill approved" in t and "pharmacy will confirm" in t for t in dash.feed(dash.parent)
    )
    assert any("Ravi Gowda approved" in t for t in dash.feed(dash.priya))
    actors = {(e.tool, e.actor) for e in dash.store.list_audit()}
    assert ("dashboard:approve_refill", "member") in actors
    assert ("pharmacy:demo_order", "system") in actors


def test_double_approval_is_safe(dash):
    refill = dash.pending_refill()
    first = dash.c.post(f"/api/refills/{refill.id}/approve", headers=dash.login(dash.ravi))
    second = dash.c.post(f"/api/refills/{refill.id}/approve", headers=dash.login(dash.priya))
    assert first.json()["changed"] is True
    assert second.status_code == 200 and second.json()["changed"] is False
    assert second.json()["refill"]["decided_by"] == "Ravi Gowda"  # first click wins
    assert len(dash.store.list_audit(tool="dashboard:approve_refill")) == 1
    parent_msgs = [t for t in dash.feed(dash.parent) if "Refill approved" in t]
    assert len(parent_msgs) == 1  # the parent is told once


def test_reject_is_idempotent_and_cannot_flip(dash):
    refill = dash.pending_refill()
    h = dash.login(dash.priya)
    assert dash.c.post(f"/api/refills/{refill.id}/reject", headers=h).json()["changed"] is True
    assert dash.c.post(f"/api/refills/{refill.id}/reject", headers=h).json()["changed"] is False
    flip = dash.c.post(f"/api/refills/{refill.id}/approve", headers=dash.login(dash.ravi))
    assert flip.status_code == 409
    stored = dash.store.get_refill(refill.id)
    assert stored.status == "rejected" and stored.decided_by_member_id == dash.priya.id
    assert any("did not approve" in t for t in dash.feed(dash.parent))


def test_approve_after_reject_race_is_conflict(dash):
    refill = dash.pending_refill()
    dash.store.decide_refill(refill.id, "rejected", dash.priya.id)  # someone else was first
    r = dash.c.post(f"/api/refills/{refill.id}/approve", headers=dash.login(dash.ravi))
    assert r.status_code == 409


def test_unknown_refill_is_404(dash):
    assert (
        dash.c.post("/api/refills/rf_nope/approve", headers=dash.login(dash.ravi)).status_code
        == 404
    )


# ---------- parent-first sharing ----------


def test_only_the_parent_changes_sharing(dash):
    url = f"/api/parents/{dash.parent.id}/sharing"
    body = {"readings": True, "alerts": True, "medicines": False}
    child = dash.c.put(url, json=body, headers=dash.login(dash.ravi))
    assert child.status_code == 403 and "Only Lakshmi" in child.json()["detail"]
    parent = dash.c.put(url, json=body, headers=dash.login(dash.parent))
    assert parent.status_code == 200
    assert parent.json()["categories"]["medicines"] is False
    assert parent.json()["updated_by"] == "Lakshmi Gowda"


def test_child_sees_only_shared_categories(dash):
    dash.store.set_sharing(
        dash.store.get_sharing(dash.parent.id).model_copy(
            update={"readings": False, "medicines": False}
        )
    )
    refill = dash.pending_refill()
    h, pid = dash.login(dash.ravi), dash.parent.id
    assert dash.c.get(f"/api/parents/{pid}/readings", headers=h).status_code == 403
    assert dash.c.get(f"/api/parents/{pid}/thresholds", headers=h).status_code == 403
    assert dash.c.get(f"/api/parents/{pid}/medicines", headers=h).status_code == 403
    assert dash.c.post(f"/api/refills/{refill.id}/approve", headers=h).status_code == 403
    assert dash.c.get("/api/refills", headers=h).json()["refills"] == []
    assert dash.c.get("/api/alerts", headers=h).status_code == 200  # alerts still shared
    ov = dash.c.get(f"/api/parents/{pid}/overview", headers=h).json()
    assert "latest" not in ov and "today_doses" not in ov and "active_alerts" in ov
    me = dash.c.get("/api/me", headers=h).json()
    assert me["parents"][0]["sharing"]["visible_to_me"] == {
        "readings": False,
        "alerts": True,
        "medicines": False,
    }
    # The parent still sees everything of their own.
    hp = dash.login(dash.parent)
    assert dash.c.get(f"/api/parents/{pid}/readings", headers=hp).status_code == 200


def test_child_sees_red_alerts_even_when_alerts_not_shared(dash):
    from app.storage.models import Alert

    pid = dash.parent.id
    red = dash.store.add_alert(Alert(parent_id=pid, level="red", reason="Repeat BP 188/122"))
    dash.store.set_sharing(dash.store.get_sharing(pid).model_copy(update={"alerts": False}))
    h = dash.login(dash.ravi)
    levels = {a["level"] for a in dash.c.get("/api/alerts", headers=h).json()["alerts"]}
    assert levels == {"red"}  # seeded ambers are hidden, the red one is not
    ov = dash.c.get(f"/api/parents/{pid}/overview", headers=h).json()
    assert ov["alerts_shared"] is False
    assert [a["id"] for a in ov["active_alerts"]] == [red.id]
    ack = dash.c.post(f"/api/alerts/{red.id}/acknowledge", headers=h)
    assert ack.status_code == 200 and ack.json()["changed"] is True
    amber = next(a for a in dash.store.list_alerts(pid) if a.level == "amber")
    assert dash.c.post(f"/api/alerts/{amber.id}/acknowledge", headers=h).status_code == 403


def test_red_alert_reaches_children_in_chat_when_alerts_not_shared(dash):
    pid = dash.parent.id
    dash.store.set_sharing(dash.store.get_sharing(pid).model_copy(update={"alerts": False}))

    def say(**kw):
        r = dash.c.post("/simulator/api/send", json={"sender_ref": PARENT_REF, **kw})
        assert r.status_code == 200, r.text
        return dash.feed(dash.parent)[-1]

    say(sample="synthetic_bp_high.jpg")
    say(text="1")
    dash.c.post("/simulator/api/skip", json={"minutes": 5})
    say(sample="synthetic_bp_high.jpg")
    reply = say(text="1")
    assert "I have told your family" in reply
    assert any("RED alert" in t for t in dash.feed(dash.ravi))
    assert any("RED alert" in t for t in dash.feed(dash.priya))


def test_nothing_shared_after_consent_is_revoked(dash):
    dash.store.set_consent(dash.parent.id, "revoked")
    h, pid = dash.login(dash.ravi), dash.parent.id
    assert dash.c.get(f"/api/parents/{pid}/readings", headers=h).status_code == 403
    assert dash.c.get("/api/alerts", headers=h).json()["alerts"] == []
    sharing = dash.c.get(f"/api/parents/{pid}/sharing", headers=h).json()
    assert sharing["consent"]["status"] == "revoked"


def test_audit_details_hidden_for_unshared_categories(dash):
    dash.c.post(
        f"/api/parents/{dash.parent.id}/demo/low-stock",
        json={"medicine_id": dash.telma.id},
        headers=dash.login(dash.ravi),
    )
    dash.store.set_sharing(
        dash.store.get_sharing(dash.parent.id).model_copy(update={"medicines": False})
    )
    rows = dash.c.get("/api/audit", headers=dash.login(dash.ravi)).json()["entries"]
    refill_call = next(r for r in rows if r["tool"] == "request_refill")
    assert refill_call["redacted"] is True and refill_call["args"] is None


def test_other_familys_parent_is_not_visible(dash):
    from app.storage.models import Family, Member

    fam = dash.store.add_family(Family(name="Iyer"))
    other = dash.store.add_member(
        Member(
            family_id=fam.id,
            role="parent",
            name="Lakshmi",
            channel="simulator",
            channel_ref="+919811111111",
            consent_status="granted",
        )
    )
    h = dash.login(dash.ravi)
    assert dash.c.get(f"/api/parents/{other.id}/readings", headers=h).status_code == 404
    assert dash.c.get(f"/api/parents/{other.id}/sharing", headers=h).status_code == 404


# ---------- thresholds ("set by your doctor") ----------


def test_thresholds_are_labelled_and_updated_as_doctor_set(dash):
    url = f"/api/parents/{dash.parent.id}/thresholds"
    h = dash.login(dash.ravi)
    got = dash.c.get(url, headers=h).json()
    assert got["label"] == "Set by your doctor"
    assert {t["rule"] for t in got["thresholds"]} >= {"bp_limit_systolic", "glucose_red_below"}
    body = {"values": {"bp_limit_systolic": 135}, "doctor_confirmed": True}
    put = dash.c.put(url, json=body, headers=h)
    assert put.status_code == 200
    row = next(t for t in put.json()["thresholds"] if t["rule"] == "bp_limit_systolic")
    assert (row["value"], row["set_by"]) == (135, "doctor")
    [entry] = dash.store.list_audit(tool="dashboard:update_thresholds")
    assert entry.actor == "member" and "Ravi Gowda" in entry.args_json


@pytest.mark.parametrize(
    ("values", "status"),
    [
        ({"bp_limit_systolic": 14}, 422),  # typo, out of range
        ({"no_such_rule": 1}, 422),
        ({"glucose_red_below": 75, "glucose_amber_below": 70}, 422),  # red must be lower
        ({"bp_limit_systolic": 175, "bp_severe_systolic": 170}, 422),
        ({"bp_weekly_over_limit_count": 2.5}, 422),  # whole number
        ({}, 422),
    ],
)
def test_threshold_validation(dash, values, status):
    url = f"/api/parents/{dash.parent.id}/thresholds"
    r = dash.c.put(
        url, json={"values": values, "doctor_confirmed": True}, headers=dash.login(dash.ravi)
    )
    assert r.status_code == status, r.text
    assert dash.store.list_audit(tool="dashboard:update_thresholds") == []


def test_threshold_update_needs_doctor_confirmation(dash):
    url = f"/api/parents/{dash.parent.id}/thresholds"
    r = dash.c.put(url, json={"values": {"bp_limit_systolic": 135}}, headers=dash.login(dash.ravi))
    assert r.status_code == 422


# ---------- low stock demo -> MCP request_refill -> family approves ----------


def test_low_stock_goes_through_mcp_and_family_approves(dash):
    h, pid = dash.login(dash.ravi), dash.parent.id
    r = dash.c.post(
        f"/api/parents/{pid}/demo/low-stock", json={"medicine_id": dash.telma.id}, headers=h
    )
    assert r.status_code == 200, r.text
    assert r.json()["tool_result"]["status"] == "pending_approval"
    [refill] = dash.store.list_refills(pid, status="pending_approval")
    assert refill.requested_by == "agent" and refill.qty == 30  # one month, daily dose 1
    assert dash.store.get_medicine(dash.telma.id).stock_count <= 3

    # The AI's call is in the audit log as an MCP tool call; the family was told.
    [call] = dash.store.list_audit(tool="request_refill")
    assert call.actor == "agent" and call.parent_id == pid
    assert any("Refill request" in t for t in dash.feed(dash.ravi))
    assert any("running low" in t for t in dash.feed(dash.parent))

    # Asking again while one is pending: the MCP server refuses, the API says so.
    again = dash.c.post(
        f"/api/parents/{pid}/demo/low-stock", json={"medicine_id": dash.telma.id}, headers=h
    )
    assert again.status_code == 409 and "already waiting" in again.json()["detail"]

    dash.c.post(f"/api/refills/{refill.id}/approve", headers=h)
    audit = dash.c.get("/api/audit", headers=h).json()
    assert audit["stats"]["ai_approve_calls"] == 0
    assert audit["stats"]["family_approvals"] == 1
    tools = [(e["actor"], e["tool"]) for e in audit["entries"]]
    assert ("agent", "request_refill") in tools and ("member", "dashboard:approve_refill") in tools
    assert not any(a == "agent" and "approve" in t for a, t in tools)


def test_mcp_tools_endpoint_shows_no_approve_tool(dash):
    out = dash.c.get("/api/mcp/tools", headers=dash.login(dash.ravi)).json()
    assert out["live"] is True and "request_refill" in out["tools"]
    assert out["has_approve_tool"] is False


def test_dashboard_page_is_served_or_explains_the_build(dash):
    r = dash.c.get("/dashboard/")
    assert r.status_code in (200, 503)
    assert "Khayal" in r.text


# ---------- README golden path on seeded data (mock mode) ----------


def test_readme_golden_path_on_seeded_data(dash):
    def say(text=None, sample=None):
        r = dash.c.post(
            "/simulator/api/send", json={"sender_ref": PARENT_REF, "text": text, "sample": sample}
        )
        assert r.status_code == 200, r.text
        return dash.feed(dash.parent)[-1]

    h, pid = dash.login(dash.ravi), dash.parent.id
    assert "186/122" in say(sample="synthetic_bp_high.jpg")
    say("1")
    first = dash.c.get("/api/alerts?days=1", headers=h).json()["alerts"]
    assert not any(a["level"] == "red" for a in first)  # first severe BP: nobody alerted
    dash.c.post("/simulator/api/skip", json={"minutes": 5})
    say(sample="synthetic_bp_high.jpg")
    say("1")
    [red] = [
        a
        for a in dash.c.get("/api/alerts?days=1", headers=h).json()["alerts"]
        if a["level"] == "red"
    ]
    assert any("RED alert" in t for t in dash.feed(dash.ravi))
    latest = dash.c.get(f"/api/parents/{pid}/overview", headers=h).json()["latest"]["bp"]
    assert latest["systolic"] >= 180

    assert "Telma 40" in say(sample="synthetic_strip_telma40.jpg")
    say("1")
    telmas = [m for m in dash.store.list_medicines(pid) if m.brand == "Telma 40"]
    assert len(telmas) == 1  # the seeded medicine, not a duplicate

    r = dash.c.post(
        f"/api/parents/{pid}/demo/low-stock", json={"medicine_id": telmas[0].id}, headers=h
    )
    assert r.status_code == 200, r.text
    [refill] = dash.c.get("/api/refills?status=pending_approval", headers=h).json()["refills"]
    dash.c.post(f"/api/refills/{refill['id']}/approve", headers=h)
    assert "your pharmacy will confirm" in dash.feed(dash.parent)[-1]
    assert dash.c.get("/api/audit", headers=h).json()["stats"]["ai_approve_calls"] == 0
    assert red["reason"].endswith("call 108/112.")


def test_demo_parent_gets_indian_english_voice_notes():
    """Both demo seeds (seed_demo.py and the simulator's fresh family) speak English to the
    parent; the simulator and Twilio voice it with the en-IN locale."""
    for seed in (seed_demo.seed, seed_fresh_family):
        store = SQLiteStore(":memory:")
        seed(store)
        assert store.find_member_by_channel("simulator", PARENT_REF).language == "en"
        store.close()
