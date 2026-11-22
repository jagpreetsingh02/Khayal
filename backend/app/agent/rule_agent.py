"""Rule-based agent (brief §5.4): what happens after the parent confirms something.

- confirmed reading: evaluate the doctor-set limits; if one is crossed, call the MCP
  `alert_family` tool (which re-checks everything server-side). A first severe BP alerts
  nobody: the conversation asks for a repeat instead.
- no repeat within 15 minutes of a first severe BP: `alert_family` amber.
- confirmed medicine strip: `create_reminder`.

Every tool call goes through a real MCP client to the Care Circle MCP server over
streamable HTTP (MCP_SERVER_URL), with the parent's sender identity in `_meta`; the LLM
(if any) never sets it. Passing a FastMCP object instead of a URL runs the same client
in-process: tests and the offline terminal demo only.
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Literal, Protocol

from fastmcp import Client, FastMCP
from pydantic import BaseModel

from app.alerts.evaluate import evaluate_confirmed_reading
from app.storage.base import Store
from app.storage.models import Member, Reading
from mcp_server.identity import sender_meta

log = logging.getLogger(__name__)

# The tools this agent may call. approve_refill does not exist on the server at all.
ALLOWED_TOOLS = frozenset(
    {
        "log_reading",
        "get_history",
        "get_meds",
        "create_reminder",
        "alert_family",
        "request_refill",
        "weekly_summary",
    }
)


class ReadingOutcome(BaseModel):
    level: Literal["none", "amber", "red"] = "none"
    rule: str | None = None
    ask_repeat: bool = False
    alert_sent: bool = False
    not_shared: bool = False  # the parent chose not to share alerts with the family


class Agent(Protocol):
    def after_confirmed_reading(self, parent: Member, reading: Reading) -> ReadingOutcome: ...

    def after_missed_repeat(self, parent: Member, reading: Reading) -> ReadingOutcome:
        """15 minutes after a first severe BP with no repeat: amber to the family."""
        ...

    def set_reminder(self, parent: Member, medicine_id: str, times: list[str]) -> list[str]:
        """Returns the medicine's reminder times after the change."""
        ...


def run_sync(make_coro) -> Any:
    """Run a coroutine from sync code, also when an event loop is already running."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(make_coro())
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(make_coro())).result()


class RuleAgent:
    def __init__(self, store: Store, mcp: str | FastMCP, timeout: float = 15) -> None:
        self.store = store
        self.mcp = mcp  # MCP server URL (streamable HTTP); a FastMCP object only in tests
        self.timeout = timeout

    @property
    def transport(self) -> str:
        return "streamable-http" if isinstance(self.mcp, str) else "in-process"

    def _client(self) -> Client:
        return Client(self.mcp, timeout=self.timeout)

    def call_tool(self, parent: Member, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        if tool not in ALLOWED_TOOLS:
            raise PermissionError(f"Tool {tool!r} is not on the agent's allowlist.")

        async def call() -> dict[str, Any]:
            async with self._client() as client:
                meta = sender_meta(parent.channel, parent.channel_ref)
                result = await client.call_tool(tool, args, meta=meta)
                return result.structured_content or {}

        return run_sync(call)

    def ping(self) -> list[str]:
        """Tool names the server exposes (raises if it cannot be reached)."""

        async def tools() -> list[str]:
            async with self._client() as client:
                return sorted(t.name for t in await client.list_tools())

        return run_sync(tools)

    def _alert(
        self, parent: Member, reading: Reading, outcome: ReadingOutcome, reason: str
    ) -> ReadingOutcome:
        args = {"level": outcome.level, "reason": reason, "reading_id": reading.id}
        try:
            result = self.call_tool(parent, "alert_family", args)
        except Exception:  # the parent still gets an honest reply; the failure is logged
            log.exception("alert_family failed for reading %s", reading.id)
            return outcome
        # "suppressed" = the amber cap for today is reached: the family already has today's alerts.
        outcome.alert_sent = bool(
            result.get("sent") or result.get("already_sent") or result.get("suppressed")
        )
        outcome.not_shared = bool(result.get("not_shared"))
        outcome.level = result.get("level", outcome.level)
        outcome.rule = result.get("rule", outcome.rule)
        return outcome

    def after_confirmed_reading(self, parent: Member, reading: Reading) -> ReadingOutcome:
        evaluation = evaluate_confirmed_reading(self.store, reading)
        outcome = ReadingOutcome(
            level=evaluation.level, rule=evaluation.rule, ask_repeat=evaluation.ask_repeat
        )
        if evaluation.level == "none":  # includes a first severe BP: ask for a repeat
            return outcome
        return self._alert(parent, reading, outcome, evaluation.reason)

    def after_missed_repeat(self, parent: Member, reading: Reading) -> ReadingOutcome:
        outcome = ReadingOutcome(level="amber", rule="bp_severe_no_repeat")
        return self._alert(parent, reading, outcome, "No repeat BP reading within 15 minutes.")

    def set_reminder(self, parent: Member, medicine_id: str, times: list[str]) -> list[str]:
        result = self.call_tool(parent, "create_reminder", {"med_id": medicine_id, "times": times})
        return list(result.get("times", times))
