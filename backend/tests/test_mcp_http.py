"""The agent talks to the MCP server over real streamable HTTP, with the sender in _meta."""

import asyncio

import pytest
from fastmcp.utilities.tests import run_server_async

from app.agent.rule_agent import RuleAgent
from app.alerts.notifier import StoreNotifier
from app.channels.service import build_service
from app.config import Settings
from app.conversation.engine import ConversationEngine
from app.conversation.messages import Incoming
from app.reader.mock_reader import MockReader
from app.storage import set_store
from app.storage.models import Family, Member
from app.storage.sqlite_store import SQLiteStore
from mcp_server.server import create_server

PARENT = "+919800000001"


def make_store() -> tuple[SQLiteStore, Member]:
    store = SQLiteStore(":memory:")
    fam = store.add_family(Family(name="Sharma"))
    parent = store.add_member(
        Member(
            family_id=fam.id,
            role="parent",
            name="Kamla",
            channel="simulator",
            channel_ref=PARENT,
            consent_status="granted",
        )
    )
    store.add_member(
        Member(family_id=fam.id, role="child", name="Ravi", channel="simulator", channel_ref="+2")
    )
    return store, parent


def over_http(store, check):
    """Run the MCP server on a real localhost port and call `check(url)` in a worker thread."""
    server = create_server(store, StoreNotifier(lambda: store))

    async def main():
        async with run_server_async(server) as url:
            return await asyncio.to_thread(check, url)

    return asyncio.run(main())


def test_conversation_alerts_family_through_mcp_over_http():
    store, parent = make_store()

    def check(url: str):
        agent = RuleAgent(store, url)
        assert agent.transport == "streamable-http"
        assert "alert_family" in agent.ping()
        engine = ConversationEngine(store, MockReader(), agent)
        engine.handle_message(Incoming(channel="simulator", sender_ref=PARENT, text="sugar 45"))
        return engine.handle_message(Incoming(channel="simulator", sender_ref=PARENT, text="1"))

    out = over_http(store, check)
    family = [o.text for o in out if o.member_id != parent.id]
    assert len(family) == 1 and family[0].startswith("RED alert for Kamla")
    [entry] = store.list_audit(tool="alert_family")
    assert entry.parent_id == parent.id  # identity came from _meta over HTTP


def test_agent_only_calls_allowlisted_tools():
    store, parent = make_store()

    def check(url: str):
        with pytest.raises(PermissionError):
            RuleAgent(store, url).call_tool(parent, "approve_refill", {"refill_id": "x"})
        return True

    assert over_http(store, check)


def test_consent_paused_is_refused_over_http():
    store, parent = make_store()
    store.set_consent(parent.id, "paused")

    def check(url: str):
        with pytest.raises(Exception, match="Consent is paused"):
            RuleAgent(store, url).call_tool(parent, "get_meds", {})
        return True

    assert over_http(store, check)


def test_app_service_uses_http_transport_by_default():
    set_store(SQLiteStore(":memory:"))
    try:
        svc = build_service(Settings(_env_file=None))
        assert svc.agent.transport == "streamable-http"
        assert svc.agent.mcp == "http://localhost:8001/mcp"
        assert set(svc.channels) == {"simulator"}
    finally:
        set_store(None)
