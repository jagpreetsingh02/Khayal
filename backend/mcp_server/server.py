"""Care Circle MCP server. Tools are added in Phase 1.

There is deliberately no approve_refill tool: refill approval exists only in the
dashboard API, triggered by a family member.
"""

from fastmcp import FastMCP

mcp = FastMCP("care-circle")


if __name__ == "__main__":
    mcp.run(transport="http", host="127.0.0.1", port=8001)
