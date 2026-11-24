"""The Docker Compose layout: the API and the MCP server are separate processes (containers)
sharing one SQLite file in WAL mode. Here each side gets its own connection to the same file
and the agent reaches the MCP server over real streamable HTTP with the bearer secret."""

import asyncio

from fastapi.testclient import TestClient
from fastmcp.utilities.tests import run_server_async

from app.agent.rule_agent import RuleAgent
from app.alerts.notifier import StoreNotifier
from app.channels.service import KhayalService
from app.channels.simulator import SimulatorChannel
from app.config import get_settings
from app.demo import CHILDREN, PARENT_REF, load_sample
from app.main import create_app
from app.reader.mock_reader import MockReader
from app.storage.sqlite_store import SQLiteStore
from mcp_server.server import create_server
from tests.test_dashboard_api import TOKEN, seed_demo

MCP_SECRET = "shared-db-mcp-secret-" + "m" * 20


def test_alert_written_by_the_mcp_process_shows_on_the_dashboard(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_DEMO_TOKEN", TOKEN)
    monkeypatch.setenv("SESSION_SECRET", "s" * 40)
    get_settings.cache_clear()
    db = str(tmp_path / "khayal.db")
    api_store = SQLiteStore(db)
    seed_demo.seed(api_store)
    mcp_store = SQLiteStore(db)  # the mcp container's own connection
    assert api_store._conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    server = create_server(mcp_store, StoreNotifier(lambda: mcp_store), secret=MCP_SECRET)
    parent = api_store.find_member_by_channel("simulator", PARENT_REF)
    ravi = api_store.find_member_by_channel("simulator", CHILDREN[0][1])
    assert parent is not None and ravi is not None
    reds_before = [a.id for a in api_store.list_alerts(parent.id) if a.level == "red"]

    def flow(url: str) -> list[dict]:
        agent = RuleAgent(api_store, url, secret=MCP_SECRET)
        svc = KhayalService(api_store, MockReader(), agent, [SimulatorChannel(load_sample)])
        with TestClient(create_app(svc, tick_seconds=0)) as c:

            def say(**body) -> None:
                r = c.post("/simulator/api/send", json={"sender_ref": PARENT_REF, **body})
                assert r.status_code == 200, r.text

            say(sample="synthetic_bp_high.jpg")
            say(text="1")
            assert c.post("/simulator/api/skip", json={"minutes": 5}).status_code == 200
            say(sample="synthetic_bp_high.jpg")
            say(text="1")
            session = c.post("/api/login", json={"token": TOKEN, "member_id": ravi.id})
            headers = {"Authorization": f"Bearer {session.json()['session']}"}
            assert c.get("/health").json()["mcp"] == "reachable"
            return c.get("/api/alerts", headers=headers).json()["alerts"]

    async def main() -> list[dict]:
        async with run_server_async(server) as url:
            return await asyncio.to_thread(flow, url)

    try:
        alerts = asyncio.run(main())
    finally:
        mcp_store.close()
        api_store.close()
        get_settings.cache_clear()

    new_reds = [a for a in alerts if a["level"] == "red" and a["id"] not in reds_before]
    assert len(new_reds) == 1 and "186/122" in new_reds[0]["reason"]
    # written by the MCP side's connection (audit row from the MCP middleware)
    check = SQLiteStore(db)
    tools = [e.tool for e in check.list_audit(limit=20)]
    check.close()
    assert "alert_family" in tools
