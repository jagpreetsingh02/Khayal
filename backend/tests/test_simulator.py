"""Web simulator + channel layer: the mock-mode golden path through the HTTP API."""

import base64

import pytest
from fastapi.testclient import TestClient

from app.agent.rule_agent import RuleAgent
from app.alerts.notifier import StoreNotifier
from app.channels.service import KhayalService
from app.channels.simulator import SimulatorChannel
from app.conversation import templates as tpl
from app.demo import PARENT_REF, load_sample
from app.main import create_app
from app.reader.mock_reader import MockReader
from app.storage.sqlite_store import SQLiteStore
from mcp_server.server import create_server


@pytest.fixture
def sim():
    store = SQLiteStore(":memory:")
    server = create_server(store, StoreNotifier(lambda: store))  # in-process for tests
    service = KhayalService(
        store, MockReader(), RuleAgent(store, server), [SimulatorChannel(load_sample)]
    )
    with TestClient(create_app(service, tick_seconds=0)) as client:
        client.service = service
        yield client
    store.close()


def say(client, text=None, sample=None, **extra):
    body = {"sender_ref": PARENT_REF, "text": text, "sample": sample, **extra}
    r = client.post("/simulator/api/send", json=body)
    assert r.status_code == 200, r.text
    return state(client)


def state(client):
    return client.get("/simulator/api/state").json()


def last_reply(s) -> str:
    return [i for i in s["parent"]["feed"] if i["direction"] == "out"][-1]["text"]


def child_texts(s) -> list[list[str]]:
    return [[i["text"] for i in c["feed"] if i["direction"] == "out"] for c in s["children"]]


def tools_called(s) -> list[str]:
    return [a["tool"] for a in s["audit"]]


def test_page_and_samples_are_served(sim):
    page = sim.get("/simulator")
    assert page.status_code == 200 and "What the AI did" in page.text
    names = [s["file"] for s in sim.get("/simulator/samples").json()]
    assert "synthetic_bp_high.jpg" in names
    assert sim.get("/simulator/samples/synthetic_bp_high.jpg").status_code == 200
    assert sim.get("/simulator/samples/..%2F..%2F.env").status_code == 404
    assert sim.get("/simulator/samples/labels.json").status_code == 404


def test_golden_path_in_mock_mode(sim):
    s = state(sim)
    assert s["parent"]["consent"] == "pending" and len(s["children"]) == 2
    assert s["info"]["reader"] == "mock"

    assert "Reply 1 (Haan / Yes) to agree" in last_reply(say(sim, "hello"))
    assert last_reply(say(sim, "1")) == tpl.CONSENT_THANKS

    s = say(sim, sample="synthetic_bp_high.jpg")
    assert last_reply(s) == "I read 186/122, pulse 92 — correct? Reply 1 yes / 2 no"
    assert s["parent"]["feed"][-2]["photo"] and s["parent"]["feed"][-2]["sample"]

    s = say(sim, "1")
    assert last_reply(s) == tpl.REPEAT_ASK.format(value="186/122, pulse 92")
    assert child_texts(s) == [[], []]  # first severe reading: family not alerted
    assert s["conversation"]["state"] == "awaiting_repeat"

    assert sim.post("/simulator/api/skip", json={"minutes": 5}).status_code == 200
    s = say(sim, sample="synthetic_bp_high.jpg")
    assert last_reply(s).startswith("I read 186/122")
    s = say(sim, "1")
    assert last_reply(s).startswith(tpl.RED_REPEAT.format(value="186/122, pulse 92"))
    for texts in child_texts(s):
        assert len(texts) == 1 and texts[0].startswith("RED alert for Kamla Sharma")
    assert "alert_family" in tools_called(s)
    assert any(i["direction"] == "note" for i in s["children"][0]["feed"])  # "5 minutes later"

    s = say(sim, sample="synthetic_strip_telma40.jpg")
    assert last_reply(s) == "Telma 40 = Telmisartan 40 mg. Remind at 8 PM? Reply 1 yes / 2 no"
    s = say(sim, "1")
    assert last_reply(s) == "Done. I will remind you to take Telma 40 at 8:00 PM every day."
    assert tools_called(s)[0] == "create_reminder"

    s = say(sim, "what is the weather today?")
    assert last_reply(s) == tpl.OFF_TOPIC
    assert all(not a["error"] for a in s["audit"])


def test_no_repeat_for_15_minutes_sends_amber_to_children(sim):
    say(sim, "1")
    say(sim, sample="synthetic_bp_high.jpg")
    say(sim, "1")
    for _ in range(3):
        sim.post("/simulator/api/skip", json={"minutes": 5})
    s = state(sim)
    assert last_reply(s) == tpl.NO_REPEAT_FAMILY_TOLD.format(value="186/122, pulse 92")
    for texts in child_texts(s):
        assert len(texts) == 1 and texts[0].startswith("AMBER alert for Kamla Sharma: BP 186/122")
        assert "rest" not in texts[0].lower()


def test_uploaded_unknown_photo_asks_for_retake(sim):
    say(sim, "1")
    data = base64.b64encode(b"not a sample photo").decode()
    s = say(sim, image_b64=data, filename="my_photo.jpg", client_id="abc")
    assert last_reply(s).startswith("I could not read the screen clearly.")
    assert s["parent"]["feed"][-2]["client_id"] == "abc"


def test_bad_requests_are_rejected(sim):
    r = sim.post("/simulator/api/send", json={"sender_ref": PARENT_REF, "text": "  "})
    assert r.status_code == 400
    r = sim.post("/simulator/api/send", json={"sender_ref": PARENT_REF, "sample": "../x"})
    assert r.status_code == 400
    r = sim.post("/simulator/api/send", json={"sender_ref": PARENT_REF, "image_b64": "%%%"})
    assert r.status_code == 400


def test_unknown_sender_is_told_to_register(sim):
    r = sim.post("/simulator/api/send", json={"sender_ref": "+910000000000", "text": "hi"})
    assert r.status_code == 200
    assert sim.service.simulator.feed("+910000000000")[-1].text == tpl.UNKNOWN_SENDER


def test_paused_parent_is_blocked_in_the_simulator(sim):
    say(sim, "1")
    assert last_reply(say(sim, "STOP")) == tpl.PAUSED_NOW
    s = say(sim, sample="synthetic_bp_high.jpg")
    assert last_reply(s) == tpl.PAUSED
    assert s["audit"] == [] and child_texts(s) == [[], []]


def test_reset_starts_a_fresh_family(sim):
    say(sim, "1")
    assert state(sim)["parent"]["consent"] == "granted"
    assert sim.post("/simulator/api/reset").status_code == 200
    s = state(sim)
    assert s["parent"]["consent"] == "pending" and s["parent"]["feed"] == []


def test_mcp_status_lists_the_tools(sim):
    status = sim.get("/simulator/api/mcp").json()
    assert status["ok"] and "alert_family" in status["tools"]
    assert "approve_refill" not in status["tools"]
