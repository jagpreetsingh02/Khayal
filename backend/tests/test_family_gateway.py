"""scripts/family_gateway.py: a local stdio bridge with only the family's read tools, forwarding
to the Khayal MCP server with a fresh family token per call."""

import asyncio
import json

from fastmcp import Client
from fastmcp.utilities.tests import run_server_async

from app.config import Settings
from app.storage.models import Sharing
from tests.test_redteam import TOKEN_SECRET, TwoFamilies
from tests.test_scripts import load

family_gateway = load("family_gateway")
READS = {"get_history", "get_meds", "weekly_summary"}


def through_gateway(world: TwoFamilies, member_id: str, calls):
    """Serve the Khayal server over HTTP; talk to the gateway in-process; return results."""

    async def main():
        async with run_server_async(world.server) as url:
            gateway = family_gateway.create_gateway(url, TOKEN_SECRET, member_id)
            async with Client(gateway) as c:
                tools = await c.list_tools()
                out = [await c.call_tool(t, a, raise_on_error=False) for t, a in calls]
            return tools, out

    return asyncio.run(main())


def test_gateway_lists_only_the_read_tools_all_read_only():
    world = TwoFamilies(token_secret=TOKEN_SECRET)
    tools, _ = through_gateway(world, world.a["child"].id, [])
    assert {t.name for t in tools} == READS
    for t in tools:
        assert t.annotations.read_only_hint and not t.annotations.destructive_hint
        assert set(t.input_schema.get("properties", {})) <= {
            "range", "type", "response_format", "parent_name"
        }  # fmt: skip


def test_gateway_reads_the_own_parent_with_a_family_token_and_is_audited():
    world = TwoFamilies(token_secret=TOKEN_SECRET)
    calls = [
        ("get_history", {"range": "30d", "response_format": "detailed"}),
        ("get_meds", {}),
        ("weekly_summary", {}),
        ("log_reading", {"type": "bp", "values": {"systolic": 130, "diastolic": 80}}),
        ("get_meds", {"parent_name": "Meera Iyer"}),
    ]
    _, (history, meds, summary, write, other) = through_gateway(world, world.a["child"].id, calls)
    assert history.structured_content["readings"][0]["id"] == world.a_reading.id
    assert [m["id"] for m in meds.structured_content["medicines"]] == [world.a_med.id]
    assert summary.structured_content["text"].startswith("Weekly summary for Lakshmi Gowda")
    assert write.is_error and "Unknown tool" in write.content[0].text  # not on the gateway
    assert other.is_error and "not a parent in your family" in other.content[0].text
    entries = world.store.list_audit(parent_id=world.a["parent"].id)
    assert {e.tool for e in entries} == {f"family_mcp:{t}" for t in READS}
    assert all(json.loads(e.args_json)["by"] == "Ravi Gowda" for e in entries)
    assert world.store.list_readings(world.a["parent"].id, status="unconfirmed") == []


def test_gateway_passes_on_the_servers_sharing_refusal():
    world = TwoFamilies(token_secret=TOKEN_SECRET)
    world.store.set_sharing(Sharing(parent_id=world.a["parent"].id, medicines=False))
    _, [meds] = through_gateway(world, world.a["child"].id, [("get_meds", {})])
    assert meds.is_error and "has not shared medicines" in meds.content[0].text


def test_gateway_for_a_parent_id_is_refused_by_the_server():
    world = TwoFamilies(token_secret=TOKEN_SECRET)
    _, [r] = through_gateway(world, world.a["parent"].id, [("get_meds", {})])
    assert r.is_error and "role does not match" in r.content[0].text


def test_gateway_refuses_unsafe_or_missing_config():
    ok = dict(mcp_token_secret="s" * 40, family_gateway_member_id="mem_x")
    assert family_gateway.config_problems(Settings(_env_file=None, **ok)) == []
    for over, why in (
        ({"mcp_token_secret": ""}, "MCP_TOKEN_SECRET"),
        ({"family_gateway_member_id": ""}, "FAMILY_GATEWAY_MEMBER_ID"),
        ({"mcp_server_url": "https://khayal.example/mcp"}, "localhost only"),
        ({"mcp_auth_mode": "secret"}, "must be token"),
        ({"app_mode": "aws"}, "local demo bridge"),
    ):
        problems = family_gateway.config_problems(Settings(_env_file=None, **(ok | over)))
        assert any(why in p for p in problems), over


def test_who_lists_the_family_member_ids():
    world = TwoFamilies()
    from app.storage import set_store

    set_store(world.store)
    lines: list[str] = []
    assert family_gateway.who("simulator", "+919800000001", lines.append) == 0
    assert lines[0] == "Gowda family:"
    assert any(world.a["child"].id in line and "use this id" in line for line in lines)
    assert not any("Iyer" in line for line in lines)
    assert family_gateway.who("simulator", "+910000000000", lines.append) == 1


def test_member_can_be_given_as_channel_and_number_and_survives_reseeding():
    from app.storage import set_store

    world = TwoFamilies(token_secret=TOKEN_SECRET)
    set_store(world.store)
    assert family_gateway.member_id_of("simulator:+919800000002") == world.a["child"].id
    assert family_gateway.member_id_of("mem_abc") == "mem_abc"
    _, [meds] = through_gateway(world, "simulator:+919800000002", [("get_meds", {})])
    assert [m["id"] for m in meds.structured_content["medicines"]] == [world.a_med.id]
    _, [nobody] = through_gateway(world, "simulator:+910000000000", [("get_meds", {})])
    assert nobody.is_error and "No member simulator:+910000000000" in nobody.content[0].text
