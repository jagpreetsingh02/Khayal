"""Who is this tool call for? Resolved server-side, never from LLM tool arguments.

Our backend (channel webhook -> agent) attaches the *sender* of the incoming message to
every MCP request in `_meta`. The LLM only produces tool arguments and cannot set `_meta`.
The server maps (channel, sender address) to a member through the `members` table, so a
parent can only ever act on their own data.

Why `_meta` can be trusted: the server only accepts HTTP requests that carry
"Authorization: Bearer <MCP_SHARED_SECRET>" (mcp_server/auth.py), and the only holder of
that secret is our API process. A caller without the secret is rejected (401) before any
tool runs, so it cannot claim a sender. The server also binds to localhost only.
"""

from typing import Any

from fastmcp import Context
from fastmcp.exceptions import ToolError

from app.storage.base import Store
from app.storage.models import Member

SENDER_META_KEY = "khayal/sender"


def sender_meta(channel: str, channel_ref: str) -> dict[str, Any]:
    """Build the `_meta` block the backend sends with each tool call."""
    return {SENDER_META_KEY: {"channel": channel, "ref": channel_ref}}


def sender_from_context(ctx: Context | None) -> tuple[str, str] | None:
    rc = ctx.request_context if ctx is not None else None
    meta = rc.meta if rc is not None else None
    sender = (meta or {}).get(SENDER_META_KEY)
    if not isinstance(sender, dict):
        return None
    channel, ref = sender.get("channel"), sender.get("ref")
    if not isinstance(channel, str) or not isinstance(ref, str):
        return None
    return channel, ref


def resolve_parent(ctx: Context | None, store: Store) -> Member:
    """Return the parent this call acts for, or raise a ToolError."""
    sender = sender_from_context(ctx)
    if sender is None:
        raise ToolError("Unknown sender: request has no sender context.")
    member = store.find_member_by_channel(*sender)
    if member is None:
        raise ToolError("Unknown sender: not registered with Khayal.")
    if member.role != "parent":
        raise ToolError("These tools act for a parent; the sender is not a parent.")
    if member.consent_status != "granted":
        raise ToolError(
            f"Consent is {member.consent_status} for this parent: Khayal does not log, "
            "alert or remind until the parent agrees (or replies START after a pause)."
        )
    return member
