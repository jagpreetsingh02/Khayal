"""Talk to the running Khayal MCP server through a real MCP client (streamable HTTP).

    cd backend
    uv run python ../scripts/seed_demo.py           # once: demo family
    export MCP_TOKEN_SECRET=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')
    uv run python -m mcp_server.server &            # MCP server on :8001 (needs the secret)
    uv run python ../scripts/mcp_client_demo.py     # list tools + call log_reading

Exactly as the backend does it: a fresh 60-second parent token (sub = the parent's member id,
looked up here from the sender) on the HTTP request, and the sender (the parent's channel +
number) in `_meta`, which the server only cross-checks. The tool arguments contain no parent
id. With MCP_AUTH_MODE=secret (mock only) the shared secret is sent instead.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from fastmcp import Client  # noqa: E402

from mcp_server.identity import sender_meta  # noqa: E402

DEFAULT_SENDER = ("simulator", "+919800000001")  # seeded parent


async def demo(target: Any, channel: str, ref: str, out=print, secret: str = "") -> dict[str, Any]:
    """`target` is a URL (real HTTP) or a FastMCP server object (in-process). `secret`: the
    bearer for HTTP, a minted token (token mode) or the shared secret (secret mode)."""
    auth = secret if isinstance(target, str) and secret else None
    async with Client(target, auth=auth) as client:
        tools = await client.list_tools()
        names = [t.name for t in tools]
        out(f"Tools ({len(names)}): {', '.join(names)}")
        out(f"approve_refill exposed: {'approve_refill' in names}")

        args = {
            "type": "bp",
            "values": {"systolic": 142, "diastolic": 88, "pulse": 76},
            "confidence": 0.94,
            "image_ref": "demo/bp_142_88.jpg",
        }
        result = await client.call_tool("log_reading", args, meta=sender_meta(channel, ref))
        out("log_reading ->")
        out(json.dumps(result.structured_content, indent=2, ensure_ascii=False))
        return {"tools": names, "log_reading": result.structured_content}


def main() -> None:
    from app.config import get_settings

    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default=settings.mcp_server_url)
    token_mode = settings.mcp_auth_mode == "token"
    name = "MCP_TOKEN_SECRET" if token_mode else "MCP_SHARED_SECRET"
    parser.add_argument(
        "--secret",
        default=settings.mcp_token_secret if token_mode else settings.mcp_shared_secret,
        help=f"the server's {name} (MCP_AUTH_MODE={settings.mcp_auth_mode}; default: from .env)",
    )
    parser.add_argument("--channel", default=DEFAULT_SENDER[0])
    parser.add_argument("--sender", default=DEFAULT_SENDER[1])
    a = parser.parse_args()
    bearer = a.secret
    if token_mode and a.secret:
        from app.storage import get_store
        from mcp_server.tokens import mint_token

        parent = get_store().find_member_by_channel(a.channel, a.sender)
        if parent is None:
            sys.exit(f"No member {a.channel}:{a.sender}; seed it: ../scripts/seed_demo.py")
        bearer = mint_token(a.secret, parent.id, "parent")
    print(f"Connecting to {a.url} as {a.channel}:{a.sender}")
    try:
        asyncio.run(demo(a.url, a.channel, a.sender, secret=bearer))
    except Exception as exc:  # connection refused, unknown sender, ...
        sys.exit(
            f"Failed: {exc}\nIs the server running (uv run python -m mcp_server.server) with "
            f"the same {name} (.env or --secret), and the demo data seeded "
            "(uv run python ../scripts/seed_demo.py)?"
        )


if __name__ == "__main__":
    main()
