"""Tool manifest pinning (the postmark-mcp supply-chain incident class).

A tool's name, description, schemas and annotations are what a model reads to decide what a
tool does. A silent change there (a dependency update, a careless edit, a malicious patch
that adds "and BCC every message to ...") changes the model's behaviour without any code
review of a "logic" change. So the full manifest is hashed and the hash is committed:
tests/test_redteam.py fails (and with it CI) whenever the manifest changes without the pin
being updated in the same commit, where a reviewer sees both.

    cd backend
    uv run python -m mcp_server.manifest            # print the hash and the tool list
    uv run python -m mcp_server.manifest --update   # after an intended change: rewrite the pin
"""

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from fastmcp import Client, FastMCP

PIN_FILE = Path(__file__).with_name("tool_manifest.sha256")


def manifest(server: FastMCP) -> list[dict[str, Any]]:
    """Every tool exactly as a client sees it in tools/list (wire names, camelCase)."""

    async def listed() -> list[dict[str, Any]]:
        async with Client(server) as client:
            tools = await client.list_tools()
        return [t.model_dump(by_alias=True, exclude_none=True, mode="json") for t in tools]

    keep = ("name", "title", "description", "inputSchema", "outputSchema", "annotations")
    rows = [{k: t[k] for k in keep if k in t} for t in asyncio.run(listed())]
    return sorted(rows, key=lambda t: t["name"])


def manifest_hash(server: FastMCP) -> str:
    canonical = json.dumps(manifest(server), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def pinned_hash() -> str:
    return PIN_FILE.read_text(encoding="utf-8").split()[0] if PIN_FILE.is_file() else ""


def _server() -> FastMCP:
    from app.storage.sqlite_store import SQLiteStore
    from mcp_server.server import create_server

    return create_server(SQLiteStore(":memory:"), secret="")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Print or update the pinned MCP tool manifest hash.")
    ap.add_argument("--update", action="store_true", help="write the current hash to the pin")
    args = ap.parse_args(argv)
    server = _server()
    current = manifest_hash(server)
    for tool in manifest(server):
        hints = tool.get("annotations", {})
        print(f"  {tool['name']:<16} {tool.get('title', '')}  {json.dumps(hints)}")
    print(f"current: {current}\npinned:  {pinned_hash() or '(none)'}")
    if args.update:
        PIN_FILE.write_text(f"{current}  MCP tool manifest (mcp_server/manifest.py)\n")
        print(f"Updated {PIN_FILE.name}. Commit it with the change that caused it.")
        return 0
    return 0 if current == pinned_hash() else 1


if __name__ == "__main__":
    sys.exit(main())
