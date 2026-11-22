# PROGRESS — Care Circle (read this first every session)

Deadline: **24 Nov 2026, 11:59 PM IST** — PPT + video + GitHub repo link on Devnovate.
Spec: `docs/PROJECT_BRIEF.md` (read only the sections you need). Rules: `CLAUDE.md`.
Repo: https://github.com/jagpreetsingh02/care-circle (public)

## Current status
- Current phase: **Phase 0 — setup: DONE** (merged to `main`, tag `phase-0-done`)
- Current branch: `main`
- Next step: **Phase 1 — MCP core + storage.** Create `phase-1-mcp-core` from `main`.
  First task: pydantic models + SQLite store for the §7 data model in `backend/app/storage/`
  (`base.py` interface + `sqlite_store.py`), with unit tests. Then §5.5 MCP tools, audit log,
  §6.3 thresholds, `scripts/seed_demo.py`. Read brief §5.5, §6.3, §7, §9 (Phase 1).

## Phase checklist
- [x] Phase 0 — setup (`phase-0-setup`)
- [ ] Phase 1 — MCP core + storage (`phase-1-mcp-core`)
- [ ] Phase 2 — reader + read-back (`phase-2-reader`)
- [ ] Phase 3 — channels + agent, golden path in simulator (`phase-3-channels-agent`)
- [ ] Phase 4 — dashboard + approve refill (`phase-4-dashboard`)
- [ ] Phase 5 — voice + reminders (`phase-5-voice-alerts`)
- [ ] Phase 6 — AWS + polish + accuracy test (`phase-6-aws-polish`)

## Blockers / needs from user
- (none yet)

## Keys / accounts available
- Groq: no · Telegram bot: no · Twilio: no · ElevenLabs: no · AWS: no

## How to run (current)
- Backend: `cd backend && uv sync && uv run pytest` · API `uv run uvicorn app.main:app --port 8000`
  · MCP `uv run python -m mcp_server.server` (http://localhost:8001/mcp)
- Dashboard: `cd dashboard && npm install && npm run dev` (http://localhost:5173)

## Tech notes (verified versions)
- Backend uses **uv** (`backend/pyproject.toml`, `uv.lock`, `[tool.uv] package = false`;
  pytest `pythonpath = ["."]`). Local Python 3.14; project requires >=3.11.
- FastAPI 0.165, **FastMCP 4.2.4**, pydantic 2.14, pydantic-settings 2.16, pytest 9.2, ruff 0.17.
- FastMCP 4: `mcp.run(transport="http", host=..., port=...)`; in tests use
  `async with fastmcp.Client(mcp) as c: await c.list_tools()` via `asyncio.run` (no pytest-asyncio).
- Dashboard: Vite 8, React 19, TypeScript 7, Recharts 3, oxlint (`npm run lint`).
- macOS has no `timeout` command; start servers in background and `kill` them.

## Decisions log
- 2026-11-22: Mock mode first (no keys needed); SQLite locally, DynamoDB later.
- 2026-11-22: No `approve_refill` MCP tool; approval only via dashboard API (smoke test guards it).
- 2026-11-22: No AI attribution in commits/PRs.
- 2026-11-22: Dev LLM provider = Groq (vision + agent), not Gemini. Bedrock stays the AWS target.
- 2026-11-22: `KICKOFF_PROMPT.md` is git-ignored (local only).

## Session log (newest first, one line each)
- 2026-11-22: Phase 0 done — repo created, skeleton (§8), .gitignore, .env.example, backend
  (FastAPI /health, empty FastMCP server, 3 smoke tests, ruff), dashboard scaffold, README skeleton.
- 2026-11-22: Kit created (CLAUDE.md, brief, progress). Build not started.
