"""Minimal rule-based agent: what happens after the parent confirms something.

Phase 2 scope only (Phase 3 builds the full agent and the LLM agent behind a flag):
- after a confirmed reading: evaluate the doctor-set limits and, if one is crossed, call the
  MCP `alert_family` tool (which re-checks everything server-side);
- after a confirmed medicine strip: call the MCP `create_reminder` tool.

Tools are called through a real MCP client, in-process, with the parent's sender in `_meta`,
so the same guardrails and audit log apply as for any other agent.
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Literal, Protocol

from fastmcp import Client, FastMCP
from pydantic import BaseModel

from app.alerts.evaluate import evaluate_confirmed_reading
from app.alerts.notifier import Notifier
from app.storage.base import Store
from app.storage.models import Member, Reading

log = logging.getLogger(__name__)


class ReadingOutcome(BaseModel):
    level: Literal["none", "amber", "red"] = "none"
    rule: str | None = None
    ask_repeat: bool = False
    alert_sent: bool = False


class Agent(Protocol):
    def after_confirmed_reading(self, parent: Member, reading: Reading) -> ReadingOutcome: ...

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


class MinimalAgent:
    def __init__(self, store: Store, notifier: Notifier, server: FastMCP | None = None) -> None:
        from mcp_server.server import create_server  # avoid an import cycle at module load

        self.store = store
        self.server = server or create_server(store, notifier)

    def _call(self, parent: Member, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        from mcp_server.identity import sender_meta

        async def call() -> dict[str, Any]:
            async with Client(self.server) as client:
                meta = sender_meta(parent.channel, parent.channel_ref)
                result = await client.call_tool(tool, args, meta=meta)
                return result.structured_content or {}

        return run_sync(call)

    def after_confirmed_reading(self, parent: Member, reading: Reading) -> ReadingOutcome:
        evaluation = evaluate_confirmed_reading(self.store, reading)
        outcome = ReadingOutcome(
            level=evaluation.level, rule=evaluation.rule, ask_repeat=evaluation.ask_repeat
        )
        if evaluation.level == "none":
            return outcome
        try:
            result = self._call(
                parent,
                "alert_family",
                {"level": evaluation.level, "reason": evaluation.reason, "reading_id": reading.id},
            )
        except Exception:  # the parent still gets an honest reply; the failure is logged
            log.exception("alert_family failed for reading %s", reading.id)
            return outcome
        # "suppressed" = the amber cap for today is reached: the family already has today's alerts.
        outcome.alert_sent = bool(
            result.get("sent") or result.get("already_sent") or result.get("suppressed")
        )
        outcome.level = result.get("level", outcome.level)
        return outcome

    def set_reminder(self, parent: Member, medicine_id: str, times: list[str]) -> list[str]:
        result = self._call(parent, "create_reminder", {"med_id": medicine_id, "times": times})
        return list(result.get("times", times))
