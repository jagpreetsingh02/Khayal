"""Tool metadata: "read vs act" is visible in tools/list, every result has structured content
that matches the published outputSchema plus a short text summary, and bad input comes back
as an isError result the model can act on."""

import asyncio

import jsonschema
import pytest
from fastmcp import Client

from mcp_server.identity import sender_meta
from tests.test_mcp_tools import PARENT_REF, World

READ_ONLY = {"get_history", "get_meds", "weekly_summary"}
EXPECTED = {
    # name: (readOnly, destructive, idempotent, openWorld)
    "log_reading": (False, False, False, False),
    "get_history": (True, False, True, False),
    "get_meds": (True, False, True, False),
    "create_reminder": (False, False, True, False),
    "alert_family": (False, False, True, True),
    "request_refill": (False, False, True, False),
    "weekly_summary": (True, False, True, False),
}


@pytest.fixture
def w():
    world = World()
    yield world
    world.store.close()


def listed(w) -> dict[str, dict]:
    async def run():
        async with Client(w.server) as c:
            return await c.list_tools()

    return {
        t.name: t.model_dump(by_alias=True, exclude_none=True, mode="json")
        for t in asyncio.run(run())
    }


def raw_call(w, tool, args):
    """The full CallToolResult (content + structuredContent + isError), errors not raised."""

    async def run():
        async with Client(w.server) as c:
            return await c.call_tool(
                tool, args, meta=sender_meta("simulator", PARENT_REF), raise_on_error=False
            )

    return asyncio.run(run())


def test_every_tool_has_a_title_and_all_four_hints_set_explicitly(w):
    tools = listed(w)
    assert set(tools) == set(EXPECTED)
    for name, (ro, destructive, idem, open_world) in EXPECTED.items():
        t = tools[name]
        assert t["title"] and t["title"] == t["annotations"]["title"]
        hints = t["annotations"]
        # every hint present: the spec defaults (destructive, open world) never apply
        assert (
            hints["readOnlyHint"],
            hints["destructiveHint"],
            hints["idempotentHint"],
            hints["openWorldHint"],
        ) == (ro, destructive, idem, open_world), name


def test_only_alert_family_reaches_the_outside_world(w):
    tools = listed(w)
    assert [n for n, t in tools.items() if t["annotations"]["openWorldHint"]] == ["alert_family"]
    assert {n for n, t in tools.items() if t["annotations"]["readOnlyHint"]} == READ_ONLY


def test_every_tool_publishes_an_output_schema_and_strict_inputs(w):
    for name, t in listed(w).items():
        assert t["outputSchema"]["type"] == "object", name
        assert t["inputSchema"].get("additionalProperties") is False, name


@pytest.mark.parametrize(
    "tool,args",
    [
        ("log_reading", {"type": "bp", "values": {"systolic": 132, "diastolic": 84, "pulse": 70}}),
        ("get_history", {}),
        ("get_history", {"response_format": "detailed"}),
        ("get_meds", {}),
        ("create_reminder", {"times": ["08:00"]}),
        ("request_refill", {"qty": 10}),
        ("weekly_summary", {}),
    ],
)
def test_results_carry_structured_content_matching_the_schema_and_a_text_summary(w, tool, args):
    if tool in ("create_reminder", "request_refill"):
        args = args | {"med_id": w.telma.id}
    schema = listed(w)[tool]["outputSchema"]
    result = raw_call(w, tool, args)
    assert result.is_error is False
    jsonschema.validate(result.structured_content, schema)
    [text] = result.content
    assert text.type == "text" and 0 < len(text.text) < 600
    assert not text.text.lstrip().startswith("{")  # a summary, not the JSON again


def test_alert_family_result_matches_its_schema(w):
    r = w.confirmed_reading(type="glucose", glucose_mgdl=45)
    result = raw_call(w, "alert_family", {"level": "red", "reading_id": r.id})
    jsonschema.validate(result.structured_content, listed(w)["alert_family"]["outputSchema"])
    assert result.content[0].text.startswith("Sent red (glucose_red_below)")


@pytest.mark.parametrize(
    "values,message",
    [
        ({"systolic": 420, "diastolic": 90}, "Systolic 420 is outside 60-260; ask the parent"),
        ({"systolic": 120, "diastolic": 170}, "Diastolic 170 is outside 30-160"),
        ({"systolic": 120, "diastolic": 130}, "Systolic 120 must be higher than diastolic 130"),
        ({"systolic": 120, "diastolic": 80, "pulse": 220}, "Pulse 220 is outside 30-200"),
        ({"systolic": 59, "diastolic": 40}, "Systolic 59 is outside 60-260"),
    ],
)
def test_implausible_bp_is_an_is_error_result_with_what_to_do(w, values, message):
    result = raw_call(w, "log_reading", {"type": "bp", "values": values})
    assert result.is_error is True
    assert message in result.content[0].text
    assert "retake the photo" in result.content[0].text
    assert w.store.list_readings(w.parent.id) == []


@pytest.mark.parametrize(
    "values,message",
    [
        ({"glucose_value": 19, "glucose_unit": "mg/dL"}, "Glucose 19 is outside 20-600"),
        ({"glucose_value": 650}, "Glucose 650 is outside 20-600"),
        ({"glucose_value": 6.2, "glucose_unit": "mg/dL"}, "looks like a mmol/L value"),
        ({"glucose_value": 112, "glucose_unit": "mmol/L"}, "is the meter in mg/dL?"),
    ],
)
def test_implausible_glucose_and_unit_mixups_are_refused(w, values, message):
    result = raw_call(w, "log_reading", {"type": "glucose", "values": values})
    assert result.is_error is True and message in result.content[0].text


def test_hi_lo_are_only_for_glucometers(w):
    bad = raw_call(w, "log_reading", {"type": "bp", "values": {"display": "HI"}})
    assert bad.is_error and "cannot show 'HI'" in bad.content[0].text
    ok = raw_call(w, "log_reading", {"type": "glucose", "values": {"display": "HI"}})
    assert ok.is_error is False and "sugar HI" in ok.structured_content["read_back"]


def test_mmol_values_in_range_are_converted(w):
    ok = raw_call(
        w,
        "log_reading",
        {"type": "glucose", "values": {"glucose_value": 3.4, "glucose_unit": "mmol/L"}},
    )
    assert ok.is_error is False
    assert w.store.get_reading(ok.structured_content["reading_id"]).glucose_mgdl == 61.2


def test_get_history_is_concise_by_default(w):
    w.confirmed_reading(type="bp", systolic=130, diastolic=80)
    w.confirmed_reading(type="bp", systolic=140, diastolic=90)
    w.confirmed_reading(type="glucose", glucose_mgdl=110)
    concise = raw_call(w, "get_history", {}).structured_content
    assert concise["response_format"] == "concise" and concise["readings"] is None
    assert concise["bp"] == {
        "count": 2,
        "latest": "140/90",
        "latest_time": concise["bp"]["latest_time"],
        "lowest": "130/80",
        "highest": "140/90",
        "average": "135/85",
    }
    assert concise["glucose"]["latest"] == "110 mg/dL"
    detailed = raw_call(w, "get_history", {"response_format": "detailed"}).structured_content
    assert len(detailed["readings"]) == 3 and detailed["readings"][0]["id"].startswith("rd_")
    bad = raw_call(w, "get_history", {"response_format": "everything"})
    assert bad.is_error


def test_create_reminder_note_is_stored_and_shown(w):
    out = raw_call(
        w, "create_reminder", {"med_id": w.telma.id, "times": ["08:00"], "note": "after food"}
    )
    assert out.structured_content["reminder_note"] == "after food"
    meds = raw_call(w, "get_meds", {}).structured_content["medicines"]
    assert next(m for m in meds if m["brand"] == "Telma 40")["reminder_note"] == "after food"
