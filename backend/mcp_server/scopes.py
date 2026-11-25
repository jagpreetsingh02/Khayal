"""Roles, scopes and the scope each MCP tool needs.

Two roles call the MCP server:
- parent: the API's agent acting for one parent (after the parent's message or a scheduler job);
- family: a family member reading what their parent shares (read-only).

The server enforces this table on every call (a tool refuses a caller without its scope) and
filters tools/list with it, so a family token never even sees a write tool. A tool missing
from TOOL_SCOPES is refused and never listed (fail closed). There is no approve scope and no
approve tool: refill approval exists only in the dashboard API, after a person's click.
"""

from typing import Literal

Role = Literal["parent", "family"]
ROLES: tuple[Role, ...] = ("parent", "family")

PARENT_SCOPES = frozenset(
    {
        "readings:write",
        "readings:read",
        "meds:read",
        "reminders:write",
        "alerts:send",
        "refills:request",
    }
)
FAMILY_SCOPES = frozenset({"readings:read", "meds:read", "summary:read"})
ROLE_SCOPES: dict[Role, frozenset[str]] = {"parent": PARENT_SCOPES, "family": FAMILY_SCOPES}

# The scope each tool needs. weekly_summary is readings:read for a parent (their own numbers)
# and summary:read for a family member; see tool_allowed().
TOOL_SCOPES: dict[str, tuple[str, ...]] = {
    "log_reading": ("readings:write",),
    "get_history": ("readings:read",),
    "get_meds": ("meds:read",),
    "create_reminder": ("reminders:write",),
    "alert_family": ("alerts:send",),
    "request_refill": ("refills:request",),
    "weekly_summary": ("readings:read", "summary:read"),  # either one
}

# Members table role -> token role.
MEMBER_ROLE: dict[str, Role] = {"parent": "parent", "child": "family"}


def tool_allowed(tool: str, scopes: frozenset[str]) -> bool:
    """True if `scopes` holds a scope `tool` accepts. Unknown tools: False (fail closed)."""
    return any(s in scopes for s in TOOL_SCOPES.get(tool, ()))


def tools_for(role: Role) -> list[str]:
    return sorted(t for t in TOOL_SCOPES if tool_allowed(t, ROLE_SCOPES[role]))
