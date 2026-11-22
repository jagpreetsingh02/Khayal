# PROGRESS — Care Circle (read this first every session)

Deadline: **24 Nov 2026, 11:59 PM IST** — PPT + video + GitHub repo link on Devnovate.
Spec: `docs/PROJECT_BRIEF.md` (read only the sections you need). Rules: `CLAUDE.md`.
Repo: https://github.com/jagpreetsingh02/care-circle (public)

## Current status
- Current phase: **Phase 1 — MCP core + storage: DONE** (merged to `main`, tag `phase-1-done`)
- Current branch: `main`
- Next step: **Phase 2 — reader + read-back.** Create `phase-2-reader` from `main`.
  First task: §6.2 vision output schema (pydantic, `backend/app/reader/schema.py`) + `MockReader`
  that returns labels from `samples/labels.json` (add 3-4 sample meter photos + labels), with tests.
  Then Groq reader (Bedrock stub), §5.3 read-back state machine incl. repeat-BP flow (uses
  `store.set_reading_status` to confirm; `log_reading` already stores `unconfirmed`), medicine
  strip matching with a small curated CSV. §6.1 rules are already done (`app/reader/rules.py`).
  Read brief §5.2, §5.3, §6.1, §6.2, §9 (Phase 2).

## Phase checklist
- [x] Phase 0 — setup (`phase-0-setup`)
- [x] Phase 1 — MCP core + storage (`phase-1-mcp-core`)
- [ ] Phase 2 — reader + read-back (`phase-2-reader`)
- [ ] Phase 3 — channels + agent, golden path in simulator (`phase-3-channels-agent`)
- [ ] Phase 4 — dashboard + approve refill (`phase-4-dashboard`)
- [ ] Phase 5 — voice + reminders (`phase-5-voice-alerts`)
- [ ] Phase 6 — AWS + polish + accuracy test (`phase-6-aws-polish`)

## Blockers / needs from user
- Phase 2 needs a **Groq API key** (`GROQ_API_KEY`) for the real vision reader; mock reader works without.
- Phase 2 needs **3-6 real photos** of BP monitor / glucometer screens for `samples/` (with the true values).

## Keys / accounts available
- Groq: no · Telegram bot: no · Twilio: no · ElevenLabs: no · AWS: no

## How to run (current)
- Backend: `cd backend && uv sync && uv run pytest` · API `uv run uvicorn app.main:app --port 8000`
  · MCP `uv run python -m mcp_server.server` (http://localhost:8001/mcp)
  · Seed `uv run python ../scripts/seed_demo.py` (wipes local DB) · MCP client demo
  `uv run python ../scripts/mcp_client_demo.py` (server must be running)
- Dashboard: `cd dashboard && npm install && npm run dev` (http://localhost:5173)

## Tech notes (verified versions)
- Backend uses **uv** (`backend/pyproject.toml`, `uv.lock`, `[tool.uv] package = false`;
  pytest `pythonpath = ["."]`). Local Python 3.14; project requires >=3.11.
- FastAPI 0.165, **FastMCP 4.2.4**, pydantic 2.14, pydantic-settings 2.16, pytest 9.2, ruff 0.17.
- FastMCP 4: `mcp.run(transport="http", host=..., port=...)`; in tests use
  `async with fastmcp.Client(mcp) as c: await c.list_tools()` via `asyncio.run` (no pytest-asyncio).
- Dashboard: Vite 8, React 19, TypeScript 7, Recharts 3, oxlint (`npm run lint`).
- macOS has no `timeout` command; start servers in background and `kill` them.
- FastMCP 4: tool `ctx: Context` param is hidden from the schema; request `_meta` is
  `ctx.request_context.meta` (works in-memory and over HTTP; client sends `call_tool(..., meta=)`).
  Audit uses `Middleware.on_call_tool`. HTTP test helper: `fastmcp.utilities.tests.run_server_async`.
- Code map: storage `app/storage/{models,base,sqlite_store}.py` (`get_store()/set_store()`),
  rules `app/reader/rules.py`, thresholds `app/alerts/thresholds.py`, mock notifier
  `app/alerts/notifier.py`, MCP `mcp_server/{server,identity,guardrails,summary}.py`.

## Decisions log
- 2026-11-22: Mock mode first (no keys needed); SQLite locally, DynamoDB later.
- 2026-11-22: No `approve_refill` MCP tool; approval only via dashboard API (smoke test guards it).
- 2026-11-22: No AI attribution in commits/PRs.
- 2026-11-22: Dev LLM provider = Groq (vision + agent), not Gemini. Bedrock stays the AWS target.
- 2026-11-22: `KICKOFF_PROMPT.md` is git-ignored (local only).
- 2026-11-22: Sender identity travels in MCP request `_meta["care-circle/sender"]={channel,ref}`,
  set by our backend (never tool args); server maps it to a parent via `members`. No tool takes
  parent_id. Only parents can be senders for tools. MCP binds 127.0.0.1; shared-secret header TODO (Phase 6).
- 2026-11-22: `log_reading` stores **unconfirmed** only; confirmation = parent's read-back reply,
  server-side (`store.set_reading_status`), no MCP confirm tool. `alert_family` requires a
  confirmed reading (or the missed-doses rule), re-evaluates thresholds server-side, refuses
  LLM escalation (amber->red), sends red even if amber asked, dedupes per reading, amber cap 3/24h.
  Family sees only server-written text; agent `reason` is filtered and kept in audit_log only.
- 2026-11-22: Refill cap = daily_dose x 30; Schedule X refused; one pending refill per medicine.
- 2026-11-22: Red BP = severe reading with an earlier confirmed severe reading 5-60 min before.
  Weekly rule = this reading over limit (140/90) and >=3 such in 7 days. Thresholds are
  defaults overlaid with per-parent rows (`set_by=doctor`).
- 2026-11-22: §7 extensions: `medicines.confirmed` (reminders only for confirmed meds),
  `audit_log.parent_id`. Consent (paused/revoked) is not enforced in tools yet — do in Phase 3.

## Session log (newest first, one line each)
- 2026-11-22: Phase 1 done — SQLite store (all §7 tables), plausibility + threshold rules, 7 MCP
  tools with guardrails + audit middleware, seed script, MCP client demo; 103 tests green.
- 2026-11-22: Phase 0 done — repo created, skeleton (§8), .gitignore, .env.example, backend
  (FastAPI /health, empty FastMCP server, 3 smoke tests, ruff), dashboard scaffold, README skeleton.
- 2026-11-22: Kit created (CLAUDE.md, brief, progress). Build not started.
