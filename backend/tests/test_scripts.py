import asyncio
import importlib.util
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastmcp import Client
from fastmcp.utilities.tests import run_server_async

from app.storage.sqlite_store import SQLiteStore
from mcp_server.guardrails import blocked_terms
from mcp_server.identity import sender_meta
from mcp_server.server import create_server

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
IST = ZoneInfo("Asia/Kolkata")


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


seed_demo = load("seed_demo")
mcp_client_demo = load("mcp_client_demo")


@pytest.fixture
def seeded():
    store = SQLiteStore(":memory:")
    info = seed_demo.seed(store, now=datetime(2026, 11, 22, 10, 0, tzinfo=IST))
    yield store, info
    store.close()


def test_seed_creates_family_parent_two_children(seeded):
    store, info = seeded
    members = store.list_members(info["family_id"])
    assert sorted(m.role for m in members) == ["child", "child", "parent"]
    parent = store.get_member(info["parent_id"])
    assert store.find_member_by_channel("simulator", seed_demo.PARENT_REF).id == parent.id
    assert parent.consent_status == "granted"


def test_seed_medicines_and_doctor_thresholds(seeded):
    store, info = seeded
    meds = store.list_medicines(info["parent_id"])
    assert len(meds) == 4 and all(m.confirmed and not m.is_schedule_x for m in meds)
    assert {t.set_by for t in store.list_thresholds(info["parent_id"])} == {"doctor"}


def test_seed_has_14_days_of_bp_and_glucose(seeded):
    store, info = seeded
    readings = store.list_readings(info["parent_id"], status="confirmed")
    days = {r.created_at.astimezone(IST).date() for r in readings}
    assert len(days) >= 10 and (max(days) - min(days)).days == 13
    bp = [r for r in readings if r.type == "bp"]
    gl = [r for r in readings if r.type == "glucose"]
    assert len(bp) >= 12 and len(gl) >= 12
    assert all(70 <= r.systolic <= 250 and r.systolic > r.diastolic for r in bp)
    assert all(20 <= r.glucose_mgdl <= 600 for r in gl)
    assert any(r.glucose_mgdl == 64 for r in gl)  # the planted low value
    assert any(a.level == "amber" for a in store.list_alerts(info["parent_id"]))
    assert store.list_dose_events(info["parent_id"])


def test_seed_is_deterministic():
    now = datetime(2026, 11, 22, 10, 0, tzinfo=IST)
    a, b = SQLiteStore(":memory:"), SQLiteStore(":memory:")
    ia, ib = seed_demo.seed(a, now=now), seed_demo.seed(b, now=now)
    values = [
        [(r.systolic, r.glucose_mgdl) for r in s.list_readings(i["parent_id"])]
        for s, i in ((a, ia), (b, ib))
    ]
    assert values[0] == values[1]


def test_weekly_summary_on_seeded_data_is_clean():
    store = SQLiteStore(":memory:")
    seed_demo.seed(store)  # real clock, so the last 7 days contain seeded data
    server = create_server(store)

    async def run():
        async with Client(server) as c:
            meta = sender_meta("simulator", seed_demo.PARENT_REF)
            return (await c.call_tool("weekly_summary", {}, meta=meta)).structured_content

    out = asyncio.run(run())
    assert out["filtered"] is False and blocked_terms(out["text"]) == []
    assert "BP: no readings" not in out["text"] and "Medicine reminders:" in out["text"]


def test_client_demo_over_real_http(seeded):
    store, info = seeded
    server = create_server(store)

    async def run():
        async with run_server_async(server) as url:
            lines: list[str] = []
            result = await mcp_client_demo.demo(
                url, "simulator", seed_demo.PARENT_REF, lines.append
            )
            return result, lines

    result, lines = asyncio.run(run())
    assert "approve_refill" not in result["tools"] and "log_reading" in result["tools"]
    assert result["log_reading"]["status"] == "unconfirmed"
    reading = store.get_reading(result["log_reading"]["reading_id"])
    assert reading.parent_id == info["parent_id"]  # resolved from _meta over HTTP
    assert reading.created_at > datetime.now(IST) - timedelta(minutes=1)
    assert any("approve_refill exposed: False" in line for line in lines)


def test_start_refuses_and_keeps_the_database_when_khayal_is_already_running(
    tmp_path, monkeypatch, capsys
):
    """`start.py --seed` is the documented demo reset: it must not wipe the database under
    servers that are still running."""
    import socket

    start = load("start")
    db = tmp_path / "khayal.db"
    db.write_bytes(b"keep me")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")
    for name in ("MCP_TOKEN_SECRET", "MCP_SHARED_SECRET"):  # restored after the test
        monkeypatch.setenv(name, os.environ.get(name, ""))
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        assert start.main(["--seed", "--port", str(port)]) == 1
    assert db.read_bytes() == b"keep me"
    assert "already in use" in capsys.readouterr().err
    assert os.environ["MCP_TOKEN_SECRET"] == ""  # refused before changing anything
