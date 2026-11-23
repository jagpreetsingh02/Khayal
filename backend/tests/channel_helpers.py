"""Shared setup for channel adapter tests: a family, an in-process MCP server, a service."""

from app.agent.rule_agent import RuleAgent
from app.alerts.notifier import StoreNotifier
from app.channels.service import KhayalService
from app.channels.simulator import SimulatorChannel
from app.reader.mock_reader import MockReader
from app.storage.models import Family, Member
from app.storage.sqlite_store import SQLiteStore
from mcp_server.server import create_server


def family(parent_channel: str, parent_ref: str, child_channel: str, child_ref: str):
    store = SQLiteStore(":memory:")
    fam = store.add_family(Family(name="Gowda"))
    parent = store.add_member(
        Member(
            family_id=fam.id,
            role="parent",
            name="Lakshmi",
            channel=parent_channel,
            channel_ref=parent_ref,
            consent_status="granted",
        )
    )
    child = store.add_member(
        Member(
            family_id=fam.id,
            role="child",
            name="Ravi",
            channel=child_channel,
            channel_ref=child_ref,
        )
    )
    return store, parent, child


def service(store, *channels) -> KhayalService:
    server = create_server(store, StoreNotifier(lambda: store))
    return KhayalService(
        store, MockReader(), RuleAgent(store, server), [SimulatorChannel(), *channels]
    )
