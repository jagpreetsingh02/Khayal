"""Talk to the running Khayal MCP server through a real MCP client (streamable HTTP).

    cd backend
    uv run python ../scripts/seed_demo.py           # once: demo family
    export MCP_SHARED_SECRET=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')
    uv run python -m mcp_server.server &            # MCP server on :8001 (needs the secret)
    uv run python ../scripts/mcp_client_demo.py     # list tools + call log_reading

The sender (the parent's channel + number) travels in the request `_meta`, exactly as the
backend will send it. The tool arguments contain no parent id.
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
    """`target` is a URL (real HTTP, needs the server's MCP_SHARED_SECRET) or a FastMCP
    server object (in-process)."""
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
    parser.add_argument(
        "--secret",
        default=settings.mcp_shared_secret,
        help="the server's MCP_SHARED_SECRET (default: from .env)",
    )
    parser.add_argument("--channel", default=DEFAULT_SENDER[0])
    parser.add_argument("--sender", default=DEFAULT_SENDER[1])
    a = parser.parse_args()
    print(f"Connecting to {a.url} as {a.channel}:{a.sender}")
    try:
        asyncio.run(demo(a.url, a.channel, a.sender, secret=a.secret))
    except Exception as exc:  # connection refused, unknown sender, ...
        sys.exit(
            f"Failed: {exc}\nIs the server running (uv run python -m mcp_server.server) with "
            "the same MCP_SHARED_SECRET (.env or --secret), and the demo data seeded "
            "(uv run python ../scripts/seed_demo.py)?"
        )


if __name__ == "__main__":
    main()
