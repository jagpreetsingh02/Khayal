"""Tool manifest pinning (the postmark-mcp supply-chain incident class), one pin per role.

A tool's name, description, schemas and annotations are what a model reads to decide what a
tool does. A silent change there (a dependency update, a careless edit, a malicious patch
that adds "and BCC every message to ...") changes the model's behaviour without any code
review of a "logic" change. So the manifest each role sees in tools/list is hashed and the
hashes are committed: tests/test_redteam.py fails (and with it CI) whenever a role's manifest
changes without the pin being updated in the same commit, where a reviewer sees both. The
red-team test also lists the tools over real HTTP with a parent token and a family token and
checks each against its pin, so the scope filter itself is pinned too.

    cd backend
    uv run python -m mcp_server.manifest            # print the hashes and the tool lists
    uv run python -m mcp_server.manifest --update   # after an intended change: rewrite the pins
"""

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from fastmcp import Client, FastMCP

from mcp_server.scopes import ROLE_SCOPES, ROLES, Role, tool_allowed

PIN_FILE = Path(__file__).with_name("tool_manifest.sha256")
KEEP = ("name", "title", "description", "inputSchema", "outputSchema", "annotations")


def rows(tools: list[Any]) -> list[dict[str, Any]]:
    """Tools exactly as a client sees them in tools/list (wire names, camelCase), sorted."""
    dumped = [t.model_dump(by_alias=True, exclude_none=True, mode="json") for t in tools]
    out = [{k: t[k] for k in KEEP if k in t} for t in dumped]
    return sorted(out, key=lambda t: t["name"])


def manifest(server: FastMCP, role: Role | None = None) -> list[dict[str, Any]]:
    """Every listed tool (in-process: no token, so every tool that declares a scope), or
    only those `role`'s scopes allow, which is what tools/list gives a token of that role."""

    async def listed() -> list[Any]:
        async with Client(server) as client:
            return list(await client.list_tools())

    tools = asyncio.run(listed())
    if role is not None:
        tools = [t for t in tools if tool_allowed(t.name, ROLE_SCOPES[role])]
    return rows(tools)


def rows_hash(manifest_rows: list[dict[str, Any]]) -> str:
    canonical = json.dumps(manifest_rows, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def manifest_hash(server: FastMCP, role: Role | None = None) -> str:
    return rows_hash(manifest(server, role))


def pinned_hashes() -> dict[str, str]:
    """{role: hash} from the pin file ("<hash>  <role>  <comment>" per line)."""
    if not PIN_FILE.is_file():
        return {}
    pins: dict[str, str] = {}
    for line in PIN_FILE.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] in ROLES:
            pins[parts[1]] = parts[0]
    return pins


def pinned_hash(role: Role = "parent") -> str:
    return pinned_hashes().get(role, "")


def _server() -> FastMCP:
    from app.storage.sqlite_store import SQLiteStore
    from mcp_server.server import create_server

    return create_server(SQLiteStore(":memory:"), secret="")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Print or update the pinned MCP tool manifest hashes.")
    ap.add_argument("--update", action="store_true", help="write the current hashes to the pin")
    args = ap.parse_args(argv)
    server = _server()
    current: dict[str, str] = {}
    for role in ROLES:
        current[role] = manifest_hash(server, role)
        print(f"{role}:")
        for tool in manifest(server, role):
            hints = tool.get("annotations", {})
            print(f"  {tool['name']:<16} {tool.get('title', '')}  {json.dumps(hints)}")
        print(f"  current: {current[role]}\n  pinned:  {pinned_hash(role) or '(none)'}")
    if args.update:
        PIN_FILE.write_text(
            "".join(
                f"{current[r]}  {r}  MCP tools/list for a {r} token (mcp_server/manifest.py)\n"
                for r in ROLES
            )
        )
        print(f"Updated {PIN_FILE.name}. Commit it with the change that caused it.")
        return 0
    return 0 if all(current[r] == pinned_hash(r) for r in ROLES) else 1


if __name__ == "__main__":
    sys.exit(main())
