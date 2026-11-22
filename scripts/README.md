# Scripts

Run from `backend/` so they use its environment and `DATABASE_URL`.

- `seed_demo.py`: wipe the local DB and seed a demo family (parent Kamla, two children),
  four medicines, doctor-set limits and 14 days of BP / glucose readings and dose events.
  `uv run python ../scripts/seed_demo.py`
- `mcp_client_demo.py`: connect to the running MCP server with a real MCP client
  (streamable HTTP), list the tools and call `log_reading` as the seeded parent.
  `uv run python ../scripts/mcp_client_demo.py [--url http://localhost:8001/mcp]`
- `accuracy_test.py`: measure reader accuracy against `samples/labels.json` (Phase 6)
