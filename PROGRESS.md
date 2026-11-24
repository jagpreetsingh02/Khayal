# PROGRESS — Khayal (read this first every session)

Submitted 24 Nov 2026 (Devnovate). Repo changes allowed until **26 Nov 2026**; pitch after.
Spec: `docs/PROJECT_BRIEF.md` (read only the sections you need). Rules: `CLAUDE.md`.
Repo: https://github.com/jagpreetsingh02/khayal (public)

## Current status
- Submission (Phase 1 of judging) is in: tag **`submission-phase1`** = the exact submitted
  commit. Repo may change until **26 Nov 2026**; `main` must stay demo-ready for the pitch.
- Current work stream: **Hardening** (below). H1 done and merged (tag `hardening-h1-done`).
- Current branch: `main`
- **Next step: H2 (security)** on branch `hardening-h2-security` (scope to be given by the user).

## Hardening (production) — one branch per phase, merged with --no-ff, tagged when done
- [x] **H1 safety bugs** (`hardening-h1-safety`, tag `hardening-h1-done`): outbox claim/lease
  + retries (30 s, 1 m, 2 m, 5 m, then 10 m; red alert messages forever, others stop at 8;
  final + red failures -> audit `delivery_failed` + ERROR log); weekly summary via outbox;
  dashboard "Not delivered yet"; inbound dedup (`inbound_seen`: Twilio MessageSid, Telegram
  update_id, webhook + polling); webhooks ack then process in background; per-member FIFO
  turns (`app/channels/turns.py`) replace the global lock.
  **Known gap:** crash between webhook ack and end of processing loses that message (id
  already recorded; channel won't resend). Durable inbox = future work. Proactive *parent*
  messages (reminders, follow-ups, low-stock note) are still sent directly, not retried.
- [ ] **H2 security** (`hardening-h2-security`)
- [ ] **H3 types, CI, Docker** (`hardening-h3-ci`)

## Phase checklist
- [x] Phase 0 — setup · [x] Phase 1 — MCP core + storage · [x] Phase 2 — reader + read-back
- [x] Phase 3 — channels + agent · [x] Phase 4 — dashboard + approve refill
- [x] Phase 5 — voice + reminders · [x] Phase 6 — quick start, demo script, AWS design, hygiene

## Blockers / needs from user
- **Indian English on ElevenLabs:** no premade voice is Indian (all US/UK/AU); library voices
  (e.g. Muskaan, Hindi) get HTTP 402 `paid_plan_required` on the free plan (checked 23 Nov).
  Either `VOICE=mock` locally (browser en-IN voice) or a paid plan + an Indian voice id.
- **Rename the GitHub repo** `care-circle` -> `khayal` (Settings -> General); docs already use
  the new URL. Locally: in `.env` set `DATABASE_URL=sqlite:///./khayal.db` and rename
  `backend/care_circle.db` (+ `-wal`/`-shm`) to `khayal.db*` with servers stopped, or reseed.
- **Real photos** of Indian home devices for a real accuracy number: none yet. Public sets were
  unusable (Oxford zip gone/no licence; Roboflow needs an account key + digit-box labels only).
  Optional: a free Roboflow API key would allow a public-photo run.
- ElevenLabs key lacks `user_read` permission (plan/credit usage can't be read via API; harmless).
- GitHub repo description + topics (suggested in the Phase 6 summary; set in repo settings).
- AWS: deploy only if the user approves paid resources; Bedrock model + AgentCore availability in
  ap-south-1 unconfirmed.
- Set your own `DASHBOARD_DEMO_TOKEN` before sharing a running dashboard (still `change-me`).
- Optional: Telegram bot token; Twilio WhatsApp/calls never tested live.

## Keys / accounts available
- Groq: **yes** (vision verified live; daily quota used up 23 Nov by test runs, fixed since)
  · Telegram bot: no · Twilio: in `.env`, unverified · ElevenLabs: **live-checked 23 Nov**, free
  plan, voice Sarah `EXAVITQu4vr4xnSDxMaL` in local `.env`, `VOICE=elevenlabs` locally · AWS: no

## How to run (current)
- Once: `cd dashboard && npm ci && npm run build` (Node 20.19+/22.12+). Backend: `cd backend && uv sync && uv run
  python -m pytest` (409 tests, zero real API calls; `RUN_LIVE_TESTS=1` adds the 2 live ones).
  Local venv was moved from ~/Downloads/care-circle, so `.venv/bin/pytest` has a stale shebang:
  use `python -m pytest` (or `uv sync --reinstall` to fix the scripts).
- **Demo: `uv run python ../scripts/start.py --seed`** -> http://localhost:8000/simulator and
  /dashboard/ (token `change-me`). Simulator: ⏰ Next reminder / ⏩ 30 min / ⏩ 1 day (demo clock),
  📊 Weekly summary, 🗣️ sample voice notes, 🎤 recorder, ⏩ Skip 5 minutes, Reset demo.
- Background runs: start with `.venv/bin/python ../scripts/start.py` (not `uv run`) so stopping
  the task stops the servers; after stopping, check ports 8000/8001 are free.
- Screenshots: demo running, then `uv run --with playwright python ../scripts/screenshots.py`
  (installed Chrome via `channel="chrome"`).

## Tech notes (verified versions)
- uv backend (Python 3.14 local, >=3.11). FastAPI 0.165, **FastMCP 4.2.4**, pydantic 2.14,
  pydantic-settings 2.16 (python-dotenv comes with it), pytest 9.2, ruff 0.17, httpx 0.28,
  tzdata (needed on Windows: no system tz db; check with `PYTHONTZPATH= uv run pytest`).
- FastMCP 4: `mcp.run(transport="http")`; tests via `fastmcp.Client(mcp)` + `asyncio.run`;
  `_meta` = `ctx.request_context.meta`; audit + clock sync in `Middleware.on_call_tool`.
- Dashboard: Vite 8 (`base: '/dashboard/'`), React 19, TS 7, Recharts 3, oxlint (no refs/Date.now
  in render). Chart colours validated: blue `#2a78d6` + aqua `#1baf7a`; lines `linear`.
- ElevenLabs (docs checked 23 Nov 2026): TTS `POST /v1/text-to-speech/{voice_id}?output_format=
  mp3_44100_64`, header `xi-api-key`, body `{text, model_id, language_code}`; `eleven_v4_turbo`
  lists hi + ta; `eleven_flash_v2_5` cheaper (Tamil unconfirmed); `eleven_turbo_v2_5` deprecated.
  STT `POST /v1/speech-to-text` multipart `file` + `model_id=scribe_v3` -> `text` (scribe_v1
  deprecated). Free plan ~10k credits/month; TTS ~1 credit/char. Live 23 Nov: free plan gets
  HTTP 402 `paid_plan_required` for Shared Library voices via API (premade only); TTS
  `language_code` takes `hi`/`en`; Scribe reply `language_code` is ISO 639-3 (`hin`, `eng`) and
  accepts `hi` or `hin` as a hint (we send none: auto-detect). Sarah/Alice/Brian all round-trip
  Hindi exactly.
- Tests: `tests/conftest.py` forces mock providers and blocks non-localhost sockets; `live`
  marker skipped unless RUN_LIVE_TESTS=1 (env or .env). Scheduler tests pin the shared clock with
  `clock.set_shared(store, offset)` (the MCP middleware re-syncs from the store each call).
- Code map: storage `app/storage/*` (kv table: demo clock, job claims, TTS budget), clock
  `app/clock.py`, scheduler `app/scheduler/jobs.py`, voice `app/voice/{phrases,tts,stt,calls}.py`,
  thresholds `app/alerts/thresholds.py`, MCP `mcp_server/*`, reader `app/reader/*`, chat
  `app/conversation/*` (`parse.normalize_speech`, `engine.Said`), agent `app/agent/rule_agent.py`,
  channels `app/channels/*` (`service.run_scheduler/advance_time/deliver`), APIs `app/api/*`.
- Guardrail gotcha: `alert_family` reasons must avoid blocked words (e.g. "dose", "doses").
- Delivery: `Channel.send` must raise on failure (Twilio raises outside the 24 h window w/o
  template, so the outbox retries). Engine standalone (`drain_family=True`: tests, chat_demo)
  marks outbox rows delivered when returned; inside the service it never touches the outbox.
  SQLite 3.50 (`UPDATE ... RETURNING` for claims). Calls: `CallProvider.can_call(member)`.

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
- 2026-11-23 (Ph 6): branch named `phase-6-polish-aws` (user's request). Docs state plainly
  that nothing runs on AWS; Bedrock reader and DynamoDB store are switches/stubs only.
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

- 2026-11-23: Accuracy published only on AI-generated images, labelled "AI-generated synthetic",
  never "real" (user's fallback). Voice = premade Sarah (Hindi verified, calm; library voices
  need a paid plan).

- 2026-11-23: Demo parent speaks English (`language="en"`), voiced en-IN (Indian English) in
  the simulator (explicit en-IN voice pick) and Twilio `<Say>`; Hindi stays per member.
- 2026-11-23: Product name is **Khayal** (was Care Circle); old commit messages and tags keep
  the old name (history not rewritten). No CARE_CIRCLE env vars existed; only the DB default changed.

- 2026-11-24 (H1): red retry-forever applies to alert *text* messages; red voice calls stop at
  8 attempts (hours of repeated calls is worse than a retried text). Background ticker /
  scheduler skip a parent who is busy (retry next 30 s pass); demo buttons wait their turn.
  Outbox needs no member turn (atomic claim). Alerts are stored before their outbox rows
  (rows carry `alert_id` + `level`). Twilio `send` is synchronous (slot booked under a lock,
  sleep + HTTP outside it); queue/`drain()` removed.

## Session log (newest first, one line each)
- 2026-11-24: Hardening H1 — tag `submission-phase1`; outbox retries + lease claims, inbound
  dedup + background webhooks, per-member FIFO turns; 16 new tests (393 -> 409); demo checked
  in mock mode (photo, read-back, red alert + call cards delivered).
- 2026-11-23: chores — Gowda family names, English voice samples, Indian English voice,
  rename to Khayal, accuracy tooling + Groq run (16/22 scored), team; 393 tests.
- 2026-11-22/23: Phases 0-6 done (setup, MCP core, reader, channels, dashboard, voice +
  reminders, polish + AWS design); fresh-clone checked; 387 tests at phase 6.
