# PROGRESS — Care Circle (read this first every session)

Deadline: **24 Nov 2026, 11:59 PM IST** — PPT + video + GitHub repo link on Devnovate.
Spec: `docs/PROJECT_BRIEF.md` (read only the sections you need). Rules: `CLAUDE.md`.
Repo: https://github.com/jagpreetsingh02/care-circle (public)

## Current status
- Current phase: **Phase 5 — voice + reminders: DONE** (merged to `main`, tag `phase-5-done`)
- Current branch: `main`
- Next step: **Phase 6 — AWS + polish** (`phase-6-aws-polish` from `main`). First task, before any
  code: **ask the user** which deploy path and whether paid AWS resources are OK (brief §9 Phase 6:
  "Ask the user before creating any paid resource"; §11 costs). Simplest viable first: one small
  host (API + MCP server + built dashboard on one port) in ap-south-1, SQLite on disk, then
  DynamoDB/Bedrock only if wanted. Also Phase 6: accuracy test on real photos -> README number,
  MCP shared-secret header (localhost bind today), voice notes on Telegram/WhatsApp (needs a
  public audio URL). Read brief §4.1, §9 (Phase 6), §11.
- Optional, still open: LLM agent via Groq tool calling behind `AGENT=rules|llm`.

## Phase checklist
- [x] Phase 0 — setup · [x] Phase 1 — MCP core + storage · [x] Phase 2 — reader + read-back
- [x] Phase 3 — channels + agent · [x] Phase 4 — dashboard + approve refill
- [x] Phase 5 — voice + reminders (`phase-5-voice-alerts`)
- [ ] Phase 6 — AWS + polish + accuracy test (`phase-6-aws-polish`)

## Blockers / needs from user
- **Your `.env` has `VOICE=none`** (copied from the old example): set `VOICE=mock` (and
  `CALL_PROVIDER=mock`) to see voice notes and call cards, or `VOICE=elevenlabs` with keys.
- **ElevenLabs API key + a voice id** (`ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`) for real voice
  notes + Scribe. Never called live yet: confirm `eleven_v4_turbo` speaks Hindi well with the chosen
  voice and that `language_code` is accepted (docs disagree whether unsupported codes error).
- **Team names** for the README "Team" section (currently "To be added").
- **Real photos** for an honest accuracy number: 10+ BP/glucometer screens (some glare/angle) and
  2-3 strips, labelled as in `samples/README.md`; then `accuracy_test.py --reader groq --real-only`
  (prints a token estimate and asks first; needs Groq quota, which resets daily).
- Phase 6 decision: deploy target and whether paid AWS resources are allowed.
- Set your own `DASHBOARD_DEMO_TOKEN` before sharing a running dashboard (default `change-me`).
- Optional: Telegram bot token; Twilio not called live (WhatsApp webhook, template SID, and for
  red-alert calls `CALL_PROVIDER=twilio` + `TWILIO_CALL_FROM` — trial accounts may only call
  verified numbers).

## Keys / accounts available
- Groq: **yes** (vision verified live; daily quota used up 23 Nov by test runs, fixed since)
  · Telegram bot: no · Twilio: in `.env`, unverified · ElevenLabs: no · AWS: no

## How to run (current)
- Once: `cd dashboard && npm install && npm run build`. Backend: `cd backend && uv sync && uv run
  pytest` (387 tests, zero real API calls; `RUN_LIVE_TESTS=1` adds the 2 live ones).
- **Demo: `uv run python ../scripts/start.py --seed`** -> http://localhost:8000/simulator and
  /dashboard/ (token `change-me`). Simulator: ⏰ Next reminder / ⏩ 30 min / ⏩ 1 day (demo clock),
  📊 Weekly summary, 🗣️ sample voice notes, 🎤 recorder, ⏩ Skip 5 minutes, Reset demo.
- Background runs: start with `.venv/bin/python ../scripts/start.py` (not `uv run`) so stopping
  the task stops the servers; after stopping, check ports 8000/8001 are free.
- Screenshots: demo running, then `uv run --with playwright python ../scripts/screenshots.py`
  (installed Chrome via `channel="chrome"`).

## Tech notes (verified versions)
- uv backend (Python 3.14 local, >=3.11). FastAPI 0.165, **FastMCP 4.2.4**, pydantic 2.14,
  pydantic-settings 2.16 (python-dotenv comes with it), pytest 9.2, ruff 0.17, httpx 0.28.
- FastMCP 4: `mcp.run(transport="http")`; tests via `fastmcp.Client(mcp)` + `asyncio.run`;
  `_meta` = `ctx.request_context.meta`; audit + clock sync in `Middleware.on_call_tool`.
- Dashboard: Vite 8 (`base: '/dashboard/'`), React 19, TS 7, Recharts 3, oxlint (no refs/Date.now
  in render). Chart colours validated: blue `#2a78d6` + aqua `#1baf7a`; lines `linear`.
- ElevenLabs (docs checked 23 Nov 2026): TTS `POST /v1/text-to-speech/{voice_id}?output_format=
  mp3_44100_64`, header `xi-api-key`, body `{text, model_id, language_code}`; `eleven_v4_turbo`
  lists hi + ta; `eleven_flash_v2_5` cheaper (Tamil unconfirmed); `eleven_turbo_v2_5` deprecated.
  STT `POST /v1/speech-to-text` multipart `file` + `model_id=scribe_v3` -> `text` (scribe_v1
  deprecated). Free plan ~10k credits/month; TTS ~1 credit/char.
- Tests: `tests/conftest.py` forces mock providers and blocks non-localhost sockets; `live`
  marker skipped unless RUN_LIVE_TESTS=1 (env or .env). Scheduler tests pin the shared clock with
  `clock.set_shared(store, offset)` (the MCP middleware re-syncs from the store each call).
- Code map: storage `app/storage/*` (kv table: demo clock, job claims, TTS budget), clock
  `app/clock.py`, scheduler `app/scheduler/jobs.py`, voice `app/voice/{phrases,tts,stt,calls}.py`,
  thresholds `app/alerts/thresholds.py`, MCP `mcp_server/*`, reader `app/reader/*`, chat
  `app/conversation/*` (`parse.normalize_speech`, `engine.Said`), agent `app/agent/rule_agent.py`,
  channels `app/channels/*` (`service.run_scheduler/advance_time/deliver`), APIs `app/api/*`.
- Guardrail gotcha: `alert_family` reasons must avoid blocked words (e.g. "dose", "doses").

## Decisions log
- 2026-11-22 (Ph 0-3): mock-first, SQLite now; Groq dev / Bedrock AWS; no AI attribution; no
  `approve_refill` MCP tool; sender identity in MCP `_meta`, no parent_id args; `log_reading` stores
  unconfirmed, confirmation is the parent's reply; `alert_family` re-evaluates, no escalation,
  dedupe, amber cap 3/day; refill cap 30 days, Schedule X refused; consent enforced in every tool;
  API -> MCP over streamable HTTP, family messages via store outbox; SQLite WAL.
- 2026-11-22: BP §6.3: first severe alerts nobody; confirmed repeat 5-60 min = red; no repeat by
  15 min = amber. Weekly rule >=3 over 140/90 in 7 days. Doctor limits = per-parent rows.
- 2026-11-23 (Ph 4): HMAC demo sessions; parent-first sharing table (readings/alerts/medicines),
  only the parent edits; atomic idempotent refill decisions + demo pharmacy stub; threshold edits
  need `doctor_confirmed`, ranges + ordering. **Red alerts always reach the family** even if alerts
  aren't shared (user decision); amber respects sharing.
- 2026-11-23: Live tests opt-in (`RUN_LIVE_TESTS=1`); accuracy_test warns + estimates tokens.
- 2026-11-23 (Ph 5): demo clock = shared offset in kv (`demo_clock_offset_s`), `utcnow()` adds it;
  zero in aws mode. Scheduler jobs idempotent via DoseEvents / `store.claim(key)`. Follow-up at
  +30 min marks the dose missed (`followed_up_at`) and a later "1" still counts; taken doses use
  stock; low stock <=5 days -> `request_refill` once/med/day, not while pending or within 7 days;
  missed-doses AMBER deduped per IST day server-side; weekly summary Sun 19:00 IST, readings
  sharing respected.
- 2026-11-23: Voice = short localized phrases (en/hi, ta partial), chat text stays English.
  VOICE=mock default (browser speechSynthesis in simulator). TTS cache by hash + daily char
  budget; failures -> text only. STT transcript is parsed like typed text; spoken readings still
  need the read-back "1" (`source="voice"`). Calls only for RED on a confirmed reading; Twilio
  calls only members on the twilio channel; call script drops the parent-only 108/112 line.
  Voice notes only in the simulator for now (Telegram/WhatsApp get text).

## Session log (newest first, one line each)
- 2026-11-23: Phase 5 done — scheduler (reminders, follow-up, missed-dose amber, low-stock
  refill requests, weekly summary), demo clock, voice phrases + ElevenLabs TTS/STT (mocked
  tests) + mock/Twilio calls, simulator voice/call UI + recorder, README Voice section +
  screenshot; live mock run verified across both processes; 387 tests.
- 2026-11-23: Opt-in live tests + network guard; accuracy token report (chore branch).
- 2026-11-23: Phase 4 done (dashboard API + React dashboard + README rewrite), then red alerts
  always go out; 325 tests.
- 2026-11-22: Phases 0-3 done (setup, MCP core, reader + read-back, channels + simulator).
