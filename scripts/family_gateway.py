"""Family gateway: a LOCAL DEMO BRIDGE that lets a family member read their parent's numbers
from an MCP client (MCP Inspector, Claude Desktop) on their own computer.

    cd backend
    uv run python ../scripts/family_gateway.py --who     # the demo family's member ids
    uv run python ../scripts/family_gateway.py           # stdio MCP server (a client starts it)

It is a stdio MCP server with exactly three read-only tools (get_history, get_meds,
weekly_summary). Each call is forwarded to the Khayal MCP server (MCP_SERVER_URL) with a fresh
60-second FAMILY token for FAMILY_GATEWAY_MEMBER_ID, signed with MCP_TOKEN_SECRET from the
local .env. The Khayal server decides everything else: the family token's scopes, the parent's
sharing and consent, the registry match of parent_name, and the "viewed by" audit entry.

Demo bridge, not the product: whoever holds this .env can mint a token for ANY member, so it
must never leave this computer or be exposed beyond localhost. It speaks stdio only (no port),
refuses an MCP_SERVER_URL that is not on this machine, and refuses APP_MODE=aws. The production
version is OAuth 2.1 with per-person consent (planned, not built). See docs/FAMILY_MCP.md.
"""

import argparse
import sys
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from fastmcp import Client, FastMCP  # noqa: E402
from fastmcp.exceptions import ToolError  # noqa: E402
from fastmcp.tools.base import ToolResult  # noqa: E402
from mcp_types import ToolAnnotations  # noqa: E402
from pydantic import Field  # noqa: E402

from app.demo import PARENT_REF  # noqa: E402
from mcp_server.tokens import mint_token  # noqa: E402

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
ParentName = Annotated[
    str | None,
    Field(
        max_length=60,
        description="Only if your family has more than one parent on Khayal: their name as "
        "registered. Leave out otherwise.",
    ),
]


def _read_only(title: str) -> ToolAnnotations:
    return ToolAnnotations(
        title=title,
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    )


def create_gateway(
    upstream: str, token_secret: str, member_id: str, timeout: float = 15
) -> FastMCP:
    """The gateway server. `upstream` is the Khayal MCP server URL (streamable HTTP)."""
    gateway = FastMCP(
        "khayal-family",
        strict_input_validation=True,
        instructions=(
            "Read-only view of a parent's health numbers on Khayal, for a family member: "
            "confirmed readings, the medicine list, and the weekly summary. Only what the "
            "parent shares is visible. Never diagnose or give dosing advice."
        ),
    )

    async def forward(tool: str, args: dict[str, Any]) -> ToolResult:
        token = mint_token(token_secret, member_id, "family")  # fresh per call, 60 s
        async with Client(upstream, auth=token, timeout=timeout) as client:
            result = await client.call_tool(
                tool, {k: v for k, v in args.items() if v is not None}, raise_on_error=False
            )
        text = result.content[0].text if result.content else ""  # type: ignore[union-attr]
        if result.is_error:
            raise ToolError(text or f"{tool} was refused by the Khayal server.")
        return ToolResult(content=text, structured_content=result.structured_content)

    @gateway.tool(annotations=_read_only("Read confirmed readings"))
    async def get_history(
        range: Literal["today", "7d", "14d", "30d"] = "7d",
        type: Literal["bp", "glucose"] | None = None,
        response_format: Literal["concise", "detailed"] = "concise",
        parent_name: ParentName = None,
    ) -> ToolResult:
        """Your parent's confirmed readings over a time range (read-only): counts, latest,
        lowest, highest and average per type; detailed adds every reading."""
        args = {"range": range, "type": type, "response_format": response_format}
        return await forward("get_history", args | {"parent_name": parent_name})

    @gateway.tool(annotations=_read_only("Read the medicine list"))
    async def get_meds(parent_name: ParentName = None) -> ToolResult:
        """Your parent's medicines with reminder times and stock (read-only)."""
        return await forward("get_meds", {"parent_name": parent_name})

    @gateway.tool(annotations=_read_only("Weekly summary (descriptive)"))
    async def weekly_summary(parent_name: ParentName = None) -> ToolResult:
        """Your parent's descriptive 7-day summary: numbers against the limits their doctor
        set, nothing more (read-only)."""
        return await forward("weekly_summary", {"parent_name": parent_name})

    return gateway


def config_problems(settings) -> list[str]:
    problems = []
    if settings.app_mode == "aws":
        problems.append("APP_MODE=aws: the family gateway is a local demo bridge only.")
    if settings.mcp_auth_mode != "token":
        problems.append("MCP_AUTH_MODE must be token (the gateway sends family tokens).")
    if len(settings.mcp_token_secret) < 32:
        problems.append(
            "MCP_TOKEN_SECRET (32+ characters) must be set in .env, the same value the Khayal "
            "MCP server uses (start.py then uses it instead of a random one)."
        )
    if not settings.family_gateway_member_id:
        problems.append("FAMILY_GATEWAY_MEMBER_ID is empty (find yours with --who).")
    host = urlparse(settings.mcp_server_url).hostname
    if host not in LOCAL_HOSTS:
        problems.append(f"MCP_SERVER_URL host {host!r} is not this machine (localhost only).")
    return problems


def who(channel: str, ref: str, out=print) -> int:
    """Print the family of the member on (channel, ref) with their member ids."""
    from app.storage import get_store

    store = get_store()
    member = store.find_member_by_channel(channel, ref)
    if member is None:
        out(f"No member {channel}:{ref}. Seed the demo (uv run python ../scripts/seed_demo.py) "
            "or pass --who CHANNEL:NUMBER of someone in your family.")  # fmt: skip
        return 1
    out(f"{store.get_family(member.family_id).name}:")
    for m in store.list_members(member.family_id):
        label = "parent" if m.role == "parent" else "family member: use this id"
        out(f"  {m.id}  {m.name} ({label})")
    return 0


def main(argv: list[str] | None = None) -> int:
    from app.config import get_settings

    ap = argparse.ArgumentParser(description="Khayal family gateway (local demo bridge, stdio).")
    ap.add_argument(
        "--who",
        nargs="?",
        const=f"simulator:{PARENT_REF}",
        metavar="CHANNEL:NUMBER",
        help="list the member ids of this member's family (default: the demo family), then exit",
    )
    args = ap.parse_args(argv)
    if args.who:
        channel, _, ref = args.who.partition(":")
        return who(channel, ref)
    settings = get_settings()
    problems = config_problems(settings)
    if problems:
        print("Family gateway not started:\n" + "\n".join(f"  - {p}" for p in problems),
              file=sys.stderr)  # fmt: skip
        return 1
    print(
        "Khayal family gateway (LOCAL DEMO BRIDGE: stdio only, do not expose) for "
        f"{settings.family_gateway_member_id} -> {settings.mcp_server_url}",
        file=sys.stderr,
    )
    gateway = create_gateway(
        settings.mcp_server_url, settings.mcp_token_secret, settings.family_gateway_member_id
    )
    gateway.run(transport="stdio", show_banner=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
