# Scripts

Run from `backend/` so they use its environment and `DATABASE_URL`.

- `start.py`: run the MCP server and the API together (the demo). Simulator at
  http://localhost:8000/simulator, dashboard at http://localhost:8000/dashboard/ (after
  `npm run build` in `dashboard/`). `--seed` wipes and seeds 14 days of demo data first.
  `uv run python ../scripts/start.py [--seed] [--port 8000]`
- `add_member.py`: register a Telegram user id or WhatsApp number as a parent/child of the
  demo family (server-side sender mapping).
  `uv run python ../scripts/add_member.py --channel telegram --ref 123456789 --role parent --name Me`
- `seed_demo.py`: (stop the servers first) wipe the local DB and seed a demo family (parent Kamla, two children),
  four medicines, doctor-set limits and 14 days of BP / glucose readings and dose events.
  `uv run python ../scripts/seed_demo.py`
- `mcp_client_demo.py`: connect to the running MCP server with a real MCP client
  (streamable HTTP), list the tools and call `log_reading` as the seeded parent.
  `uv run python ../scripts/mcp_client_demo.py [--url http://localhost:8001/mcp]`
- `chat_demo.py`: talk to the parent conversation in the terminal (in-memory DB, mock reader
  by default). `--demo` runs the golden path; interactive commands `/photo <file> [caption]`,
  `/wait <minutes>`. `--reader groq` uses the real vision model.
  `uv run python ../scripts/chat_demo.py --demo`
- `accuracy_test.py`: exact-match accuracy of a reader against `samples/labels.json`, per field
  and overall, with every failure listed. `--reader groq` for the real model, `--real-only` to
  skip synthetic samples. Label format: `samples/README.md`.
  `uv run python ../scripts/accuracy_test.py --reader groq`
- `screenshots.py` (dev only): plays the golden path against the running demo and saves
  README screenshots to `docs/screenshots/` with headless Chrome. Playwright is not a project
  dependency: `uv run --with playwright python ../scripts/screenshots.py`
- `make_synthetic_samples.py`: redraw the `samples/synthetic_*.jpg` images (drawn, not photos).
