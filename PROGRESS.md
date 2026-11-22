# PROGRESS — Care Circle (read this first every session)

Deadline: **24 Nov 2026, 11:59 PM IST** — PPT + video + GitHub repo link on Devnovate.
Spec: `docs/PROJECT_BRIEF.md` (read only the sections you need). Rules: `CLAUDE.md`.
Repo: https://github.com/jagpreetsingh02/care-circle (public)

## Current status
- Current phase: **Phase 4 — dashboard: DONE** (merged to `main`, tag `phase-4-done`)
- Current branch: `main`
- Next step: **Phase 5 — voice + reminders.** Create `phase-5-voice-alerts` from `main`. First
  task: the scheduler (`app/scheduler/`, started from `create_app` lifespan like `_ticker`):
  create `DoseEvent`s at each confirmed medicine's reminder times (IST) and send
  `engine.dose_reminder(...)` through `service.deliver`; mark doses `missed` after 3 h and call
  `alert_family` (no reading_id) for the missed-doses rule; daily low-stock check
  (days_left <= 5) -> `agent.request_refill(parent, med)` (same path as the dashboard demo
  button, which already notifies the family). Then weekly summary, then ElevenLabs (check the
  official docs/pricing first). Read brief §5.7, §5.8, §9 (Phase 5).
- Still open (optional): LLM agent via Groq tool calling behind `AGENT=rules|llm`
  (allowlist = `ALLOWED_TOOLS` in `app/agent/rule_agent.py`; check Groq tool-use docs first).

## Phase checklist
- [x] Phase 0 — setup (`phase-0-setup`)
- [x] Phase 1 — MCP core + storage (`phase-1-mcp-core`)
- [x] Phase 2 — reader + read-back (`phase-2-reader`)
- [x] Phase 3 — channels + agent, golden path in simulator (`phase-3-channels-agent`)
- [x] Phase 4 — dashboard + approve refill (`phase-4-dashboard`)
- [ ] Phase 5 — voice + reminders (`phase-5-voice-alerts`)
- [ ] Phase 6 — AWS + polish + accuracy test (`phase-6-aws-polish`)

## Blockers / needs from user
- **Team names** for the README "Team" section (currently "To be added").
- **ElevenLabs API key + voice id** for Phase 5 voice notes (`ELEVENLABS_API_KEY`,
  `ELEVENLABS_VOICE_ID`); without them voice stays disabled (`VOICE=none`).
- **Real photos** (for an honest accuracy number): 10+ photos of BP monitor / glucometer screens
  (some with glare/angle) and 2-3 medicine strips, labelled as in `samples/README.md`, then
  `accuracy_test.py --reader groq --real-only`.
- Set your own `DASHBOARD_DEMO_TOKEN` in `.env` before sharing a running dashboard (default
  `change-me`; the login page shows that hint only in mock mode with the default).
- Optional: Telegram bot token (adapter tested only with a mocked Bot API). Twilio not called
  live yet: needs `PUBLIC_BASE_URL` (ngrok), Sandbox webhook `<URL>/webhooks/twilio`, a Sandbox
  template Content SID; untested whether MediaUrl0 needs Basic auth.
- Groq free tier: ~3 photos/min (7k input tokens/min). Avoid rapid-fire photos in the video.
- Groq daily quota (200k tokens/day) was used up on 23 Nov by test runs; fixed: live tests are
  now opt-in (`RUN_LIVE_TESTS=1`) and the suite blocks real network calls.

## Keys / accounts available
- Groq: **yes** (vision verified live) · Telegram bot: no · Twilio: in `.env`, unverified
  · ElevenLabs: no · AWS: no

## How to run (current)
- Once: `cd dashboard && npm install && npm run build` (FastAPI serves `dashboard/dist` at
  `/dashboard/`). Backend: `cd backend && uv sync && uv run pytest` (322 tests; includes a
  dashboard build + lint check, skipped without node_modules).
- **Demo: `uv run python ../scripts/start.py --seed`** (wipes + seeds 14 days, then MCP :8001 +
  API :8000). Simulator http://localhost:8000/simulator · Dashboard
  http://localhost:8000/dashboard/ (token `change-me`, sign in as Ravi / Priya / Kamla).
  Without `--seed` keeps the DB. Simulator "Reset demo" = fresh family, consent pending.
- Dashboard dev: `cd dashboard && npm run dev` -> http://localhost:5173/dashboard/ (proxies /api).
- Screenshots (dev only): demo running, then `uv run --with playwright python ../scripts/screenshots.py`
  (uses installed Chrome, `channel="chrome"`; latest Playwright needs a newer cached Chromium).
- Other scripts: `mcp_client_demo.py`, `chat_demo.py [--demo]`, `accuracy_test.py`,
  `add_member.py`, `seed_demo.py` (servers stopped; also deletes -wal/-shm).

## Tech notes (verified versions)
- Backend uses **uv** (`backend/pyproject.toml`, `uv.lock`, `[tool.uv] package = false`;
  pytest `pythonpath = ["."]`). Local Python 3.14; project requires >=3.11.
- FastAPI 0.165, **FastMCP 4.2.4**, pydantic 2.14, pydantic-settings 2.16, pytest 9.2, ruff 0.17.
- FastMCP 4: `mcp.run(transport="http", ...)`; tests `async with fastmcp.Client(mcp)` via
  `asyncio.run`; `ctx: Context` hidden from schema; request `_meta` = `ctx.request_context.meta`
  (client `call_tool(..., meta=)`); audit via `Middleware.on_call_tool`; HTTP test helper
  `fastmcp.utilities.tests.run_server_async`; client raises `fastmcp.exceptions.ToolError`.
- Dashboard: Vite 8 (`base: '/dashboard/'`), React 19, TypeScript 7, Recharts 3 (lazy-loaded
  Trends chunk), oxlint (React compiler rules: no refs in render, no sync setState in effects,
  no `Date.now()` in render — readings API returns `now`). Chart colours validated (dataviz
  validator): series blue `#2a78d6` + aqua `#1baf7a` (dark `#3987e5` / `#199e70`); status bands
  warning `#fab219`, critical `#d03b3b`; lines `linear` (monotone overshoots real values).
- Ruff B008: use `Me = Annotated[Member, Depends(current_member)]`, not `= Depends(...)`.
- macOS has no `timeout` command; start servers in background and stop them afterwards.
- Code map: storage `app/storage/{models,base,sqlite_store}.py`, rules `app/reader/rules.py`,
  thresholds `app/alerts/thresholds.py` (+ `THRESHOLD_SPECS`, `validate_threshold_update`),
  `evaluate.py`, notifier `app/alerts/notifier.py`, MCP `mcp_server/{server,identity,guardrails,
  summary}.py`, reader `app/reader/*`, chat `app/conversation/{engine,parse,templates,messages}.py`,
  agent `app/agent/rule_agent.py` (`request_refill(parent, med)`), channels `app/channels/*`
  (`service.flush_outbox()`), simulator `app/api/simulator.py`, dashboard API
  `app/api/{auth,dashboard}.py`, pharmacy stub `app/pharmacy.py`, demo family `app/demo.py`.
- Groq vision: `qwen/qwen3.8-27b`, `temperature 0`, `reasoning_effort: "none"`, json_schema
  strict; 429 `retry-after` honoured. Pillow 13, rapidfuzz 3.14, httpx 0.28.
- SQLite store applies small column migrations on open (`MIGRATIONS` in `sqlite_store.py`).

## Decisions log
- 2026-11-22 (Phases 0-2): mock mode first (no keys); SQLite now, DynamoDB later; dev LLM = Groq,
  Bedrock = AWS target; no AI attribution; `KICKOFF_PROMPT.md` git-ignored; photos never stored;
  strip match needs brand ratio >= 85 AND exact strength; refill cap = daily_dose x 30,
  Schedule X refused, one pending refill per medicine; English templates only (Hindi next).
- 2026-11-22: No `approve_refill` MCP tool; approval only via dashboard API (smoke test guards it).
- 2026-11-22: Sender identity in MCP `_meta["care-circle/sender"]={channel,ref}` set by our
  backend; no tool takes parent_id; only parents act via tools. MCP binds 127.0.0.1; shared-secret
  header TODO (Phase 6).
- 2026-11-22: `log_reading` stores unconfirmed only; confirmation = parent's reply, server-side.
  `alert_family` needs a confirmed reading (or missed-doses rule), re-evaluates limits, refuses
  escalation, dedupes per reading, amber cap 3/24h; family sees only server-written text.
- 2026-11-22: BP §6.3: first severe BP alerts nobody (ask repeat); confirmed repeat 5-60 min later
  = red; no repeat by 15 min = amber "no repeat taken" (tick every 30 s). Weekly rule = >=3 over
  140/90 in 7 days. Thresholds = defaults overlaid with per-parent rows (`set_by=doctor`).
- 2026-11-22: Consent: every MCP tool refuses parents not `granted`; STOP pauses, START resumes.
- 2026-11-22: Phase 3 — API agent calls the MCP server over streamable HTTP; family messages go
  MCP server -> store `outbox` -> API delivers. SQLite WAL (two processes). Simulator "skip 5
  minutes" shifts the parent's timestamps back. Twilio: REST queue 1 msg/3 s, templates outside 24 h.
- 2026-11-23: Phase 4 — dashboard auth = demo token + member pick -> HMAC session
  `<member_id>.<sig>` keyed by `DASHBOARD_DEMO_TOKEN` (rotating the token logs everyone out).
  The parent can sign in too (only the parent changes sharing).
- 2026-11-23: Sharing (§6.5, parent-first) = new `sharing` table (readings / alerts / medicines,
  default all on). Children see a category only if shared AND consent is granted or paused
  (nothing when pending/revoked); audit entries of unshared categories are redacted.
  `alert_family` holds back AMBER if alerts aren't shared (parent told "you chose not to
  share") but RED always reaches the family (user decision 2026-11-23); children see red alerts
  on the dashboard too (overview `alerts_shared: false` = red only). `request_refill` refused if
  medicines aren't shared (nobody could approve).
- 2026-11-23: Refill decisions: `decide_refill` is an atomic conditional update from
  `pending_approval` (ConflictError otherwise). Same decision twice -> 200 `changed:false`;
  opposite decision -> 409. Approve -> demo pharmacy stub sets `sent_to_pharmacy` +
  `pharmacy_ref` "DEMO-…" (labelled "demo, no real order"); parent gets "Refill approved for X
  (n); your pharmacy will confirm." and other children get who approved, via the outbox.
- 2026-11-23: `request_refill` now notifies children itself (server text). Dashboard "Simulate
  low stock (demo)" sets stock to ~3 days then calls the real MCP tool via `agent.request_refill`;
  disabled when APP_MODE=aws. Family/system actions are audited as actor `member` / `system`
  (`dashboard:*`, `pharmacy:demo_order`, `demo:simulate_low_stock`) next to AI calls.
- 2026-11-23: Threshold edits need `doctor_confirmed: true`, known rules only, per-rule ranges
  (typo guard) and ordering checks (red < amber below < amber above; limit < severe).

## Session log (newest first, one line each)
- 2026-11-23: Follow-up: red alerts always go out even when alerts aren't shared (MCP +
  dashboard + UI note); 325 tests. Note: stopping a `uv run start.py` background task can leave
  start.py + servers running — check ports 8000/8001 after stopping.
- 2026-11-23: Phase 4 done — dashboard API (auth, readings, alerts+ack, refills approve/reject,
  medicines, thresholds, sharing, audit, MCP tool list, low-stock demo), React dashboard (7
  sections, mobile-first, IST, 4 s polling), served at /dashboard, `start.py --seed`, README
  rewrite (stats, Mermaid, tool table, checkpoints, safety), 8 screenshots; clean-clone mock
  golden path verified live; 322 tests.
- 2026-11-22: Phase 3 done — real MCP boundary (HTTP), BP repeat/amber fix, consent enforcement,
  channels + web simulator, Telegram + Twilio adapters (mocked tests); 290 tests.
- 2026-11-22: Phase 2 done — reader (mock, Groq, Bedrock stub), strip matching, chat state
  machine, accuracy + chat demo scripts; 226 tests; live Groq 6/6 on synthetic samples.
- 2026-11-22: Phase 1 done — SQLite store, rules, 7 MCP tools + guardrails + audit; 103 tests.
- 2026-11-22: Phase 0 done — repo, skeleton, backend + dashboard scaffolds, README skeleton.
