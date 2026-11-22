# Scripts

Run from `backend/` so they use its environment and `DATABASE_URL`.

- `seed_demo.py`: wipe the local DB and seed a demo family (parent Kamla, two children),
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
- `make_synthetic_samples.py`: redraw the `samples/synthetic_*.jpg` images (drawn, not photos).
