# PROGRESS — Care Circle (read this first every session)

Deadline: **24 Nov 2026, 11:59 PM IST** — PPT + video + GitHub repo link on Devnovate.
Spec: `docs/PROJECT_BRIEF.md` (read only the sections you need). Rules: `CLAUDE.md`.
Repo: https://github.com/jagpreetsingh02/care-circle (public)

## Current status
- Current phase: **Phase 3 — channels + agent: DONE** (merged to `main`, tag `phase-3-done`)
- Current branch: `main`
- Next step: **Phase 4 — dashboard.** Create `phase-4-dashboard` from `main`. First task: the
  dashboard API (brief §5.6) in FastAPI (`app/api/dashboard.py`, include in `app/main.py`
  `create_app`): family-member auth with `DASHBOARD_DEMO_TOKEN`, readings/alerts/medicines/
  thresholds/audit endpoints, and `POST /api/refills/{id}/approve` + reject (the only approval
  path; never an MCP tool). Status messages to parent/child go out via
  `service.deliver([...Outgoing])` so they show in the simulator panes. Then the React dashboard.
  Read brief §5.6, §7, §9 (Phase 4).
- Still open from Phase 3 (optional): LLM agent via Groq tool calling behind a flag. Use a
  separate `AGENT=rules|llm` setting (LLM_PROVIDER=groq already selects the Groq *reader*);
  allowlist = `ALLOWED_TOOLS` in `app/agent/rule_agent.py`; pick `GROQ_AGENT_MODEL` only after
  checking Groq's model list + tool-use docs.

## Phase checklist
- [x] Phase 0 — setup (`phase-0-setup`)
- [x] Phase 1 — MCP core + storage (`phase-1-mcp-core`)
- [x] Phase 2 — reader + read-back (`phase-2-reader`)
- [x] Phase 3 — channels + agent, golden path in simulator (`phase-3-channels-agent`)
- [ ] Phase 4 — dashboard + approve refill (`phase-4-dashboard`)
- [ ] Phase 5 — voice + reminders (`phase-5-voice-alerts`)
- [ ] Phase 6 — AWS + polish + accuracy test (`phase-6-aws-polish`)

## Blockers / needs from user
- **Real photos** (not blocking the code; needed for an honest accuracy number): 10+ photos of BP
  monitor / glucometer screens (some with glare/angle) and 2-3 medicine strips, named and labelled
  as in `samples/README.md`, then run `accuracy_test.py --reader groq --real-only`.
- Optional: a **Telegram bot token** (@BotFather) to try the Telegram adapter live (tested only
  with a mocked Bot API so far).
- Optional, Twilio (not called live yet): verify SID/token; get a public https URL (ngrok) ->
  `PUBLIC_BASE_URL`; set the Sandbox "when a message comes in" webhook to
  `<PUBLIC_BASE_URL>/webhooks/twilio`; pick one Sandbox template in Console and put its Content
  SID in `TWILIO_TEMPLATE_CONTENT_SID` (needed for alerts to numbers outside the 24 h window).
  Untested live: whether MediaUrl0 needs Basic auth (code sends it; httpx drops it on the CDN
  redirect) and the exact Sandbox template variables (code fills {{1}} = alert label, {{2}} = time).
- Groq free tier limits vision to ~3 photos/min (7k input tokens/min, ~2.4k per photo). Fine for
  the demo; avoid rapid-fire photos while recording the video.

## Keys / accounts available
- Groq: **yes** (vision verified live) · Telegram bot: no · Twilio: in `.env`, unverified
  · ElevenLabs: no · AWS: no

## How to run (current)
- Backend: `cd backend && uv sync && uv run pytest` · **Demo: `uv run python ../scripts/start.py`**
  (MCP server :8001 + API :8000) -> http://localhost:8000/simulator (Reset demo button = fresh
  family, consent pending). Seed 14 days: `uv run python ../scripts/seed_demo.py` (servers stopped).
  · MCP client demo `uv run python ../scripts/mcp_client_demo.py` · terminal chat
  `uv run python ../scripts/chat_demo.py [--demo]` · accuracy `../scripts/accuracy_test.py`
  · register Telegram/WhatsApp senders `../scripts/add_member.py`
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
  rules `app/reader/rules.py`, thresholds `app/alerts/thresholds.py` + `evaluate.py`, mock notifier
  `app/alerts/notifier.py`, MCP `mcp_server/{server,identity,guardrails,summary}.py`.
  Reader `app/reader/{schema,base,mock_reader,groq_reader,bedrock_reader,image_prep,labels,
  intake,strips}.py` (`get_reader()`), chat `app/conversation/{engine,parse,templates,messages}.py`,
  rule agent `app/agent/rule_agent.py` (MCP URL or in-process FastMCP for tests). Demo meds CSV
  `app/reader/data/`. Channels `app/channels/{base,simulator,telegram,twilio_whatsapp,service}.py`
  (`build_service()`), simulator routes `app/api/simulator.py` + `app/api/static/simulator.html`,
  webhooks `app/api/webhooks.py`, demo family `app/demo.py`, `create_app(service, tick_seconds)`.
- No python-multipart: simulator posts photos as base64 JSON; Twilio form parsed with parse_qsl.
- HTTP MCP tests: `fastmcp.utilities.tests.run_server_async` + `asyncio.to_thread(agent call)`.
- Groq vision: `qwen/qwen3.8-27b` (only listed vision model on 22 Nov; docs/vision). Works with
  `temperature 0`, `reasoning_effort: "none"` (instruct mode), `response_format` json_schema
  strict; max 3 images, 20 MB. 429s carry `retry-after`; reader waits up to `max_wait`
  (10 s chat, 65 s batch). Pillow 13, rapidfuzz 3.14, httpx 0.28.
- SQLite store applies small column migrations on open (`MIGRATIONS` in `sqlite_store.py`).

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
  `audit_log.parent_id`. Consent enforced in tools since Phase 3.

- 2026-11-22: Phase 2 — photos are never stored (bytes read then dropped; `image_ref` unused).
  Readings/alerts only via the chat engine after "1"; family messages come back from
  `handle_message` (outbox drained). [Superseded in Phase 3: first severe BP alerts nobody] (old §6.3
  table); red only on a confirmed repeat 5-60 min later; too-early repeat photo is not stored.
  30-min timeout marks the reading `expired` (lazy on next message, or `expire_stale()`).
- 2026-11-22: Strips: match needs brand ratio >= 85 AND exact strength, else "not in my list"
  (never guessed). Medicine stored only after "1"/time; reminder times merged; default 20:00.
  Dose replies are stateless: 1/le li = taken, 2 = missed, for pending doses due in last 3 h;
  a pending reading read-back takes priority over doses.
- 2026-11-22: Consent: pending/revoked -> only the notice (photos not read); "1/haan" grants
  (stores `consent_text_version`); STOP pauses + discards pending reading; START resumes.
  Templates are English only for now (Hindi next).

- 2026-11-22: Phase 3 — the API's agent calls the MCP server (own process) via MCP client over
  streamable HTTP; family messages travel MCP server -> store `outbox` table -> API delivers.
  SQLite in WAL mode (two processes). In-process server only in tests + terminal chat demo.
- 2026-11-22: BP §6.3 changed: first severe BP alerts nobody (ask repeat); confirmed repeat
  5-60 min later = red; no confirmed repeat by 15 min = amber "no repeat taken" (engine
  `tick()` every 30 s + on next message; server re-checks timing via `evaluate_for_alert`).
  Amber deferred while a repeat read-back is pending. STOP cancels it. Family texts never
  contain parent wording. Red keeps "If you feel unwell, call 108/112" (brief §6.3).
- 2026-11-22: Consent: every MCP tool refuses parents not `granted` (audited); engine already
  blocked; `dose_reminder()` returns None; tick skips them.
- 2026-11-22: Simulator "skip 5 minutes" shifts the parent's readings/alerts/state timestamps back
  (demo only) instead of faking a clock, so the separate MCP process sees consistent time.
- 2026-11-22: Twilio: replies via REST queue (1 msg/3 s), empty TwiML; free-form only inside
  24 h window (in-memory, conservative after restart), else Sandbox template Content SID.

## Session log (newest first, one line each)
- 2026-11-22: Phase 3 done — real MCP boundary (HTTP), BP repeat/amber fix, consent enforcement,
  channel layer + web simulator (golden path verified live with 2 processes, Groq upload verified),
  Telegram + Twilio adapters (mocked tests), start/add_member scripts; 290 tests.
- 2026-11-22: Phase 2 done — reader (schema, mock, Groq, Bedrock stub, downscaling), strip
  matching (55-brand demo CSV), chat state machine (read-back, timeout, repeat BP, strips, doses,
  consent, off-topic), accuracy + chat demo scripts; 226 tests; live Groq 6/6 on synthetic samples.
- 2026-11-22: Phase 1 done — SQLite store (all §7 tables), plausibility + threshold rules, 7 MCP
  tools with guardrails + audit middleware, seed script, MCP client demo; 103 tests green.
- 2026-11-22: Phase 0 done — repo created, skeleton (§8), .gitignore, .env.example, backend
  (FastAPI /health, empty FastMCP server, 3 smoke tests, ruff), dashboard scaffold, README skeleton.
- 2026-11-22: Kit created (CLAUDE.md, brief, progress). Build not started.
