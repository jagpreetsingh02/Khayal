# PROGRESS — Khayal (read this first every session)

Deadline: **24 Nov 2026, 11:59 PM IST** — PPT + video + GitHub repo link on Devnovate.
Spec: `docs/PROJECT_BRIEF.md` (read only the sections you need). Rules: `CLAUDE.md`.
Repo: https://github.com/jagpreetsingh02/khayal (public)

## Current status
- Current phase: **Phase 6 — polish + AWS design: DONE** (branch `phase-6-polish-aws`, merged to
  `main`, tag `phase-6-done`). No AWS resource created; nothing deployed.
- Current branch: `main` (chore `chore/voice-indian-english` merged, tag `voice-indian-english-done`)
- Next step: **submission support only** (deadline 24 Nov). Optional small chunks, each on its own
  branch: (a) score the 6 unscored glucose images once Groq's daily quota resets (~13k tokens,
  command in `samples/generated_medical_test_images_jpg/README.md`) and update README numbers;
  (b) real Indian-device photos into `samples/` + accuracy run; (c) AWS deploy only if the user
  approves paid resources (design in `docs/AWS_ARCHITECTURE.md`).
- Optional, still open: LLM agent via Groq tool calling behind `AGENT=rules|llm`.

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
  pytest` (391 tests, zero real API calls; `RUN_LIVE_TESTS=1` adds the 2 live ones).
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

## Session log (newest first, one line each)
- 2026-11-23: chore voice-indian-english — demo parent Kamla gets Indian English voice notes
  (both seeds), simulator picks an en-IN voice (Rishi in Chrome on macOS), label "Indian
  English", docs + 2 screenshots updated, ElevenLabs voices checked (none Indian on free plan); 392 tests.
- 2026-11-23: chore rename-khayal — product renamed Care Circle -> Khayal everywhere (UI, chat
  and voice texts, MCP server name `khayal`, meta key `khayal/sender`, default DB `khayal.db`,
  packages `khayal-backend`/`khayal-dashboard`, repo URL); brand mark CC -> K; screenshots
  regenerated (script now also captures voice-and-call.png); fresh clone checked; 391 tests.
- 2026-11-23: chore team-accuracy-voice — Swetank Kumar in Team; accuracy_test `--kind/--pace/
  --match`, per-device score, unreadable-vs-wrong split; labelled 24 AI-generated images; Groq run
  16/22 scored (BP 11/13, glucose 3/3, 0 refusals; daily TPD hit); ElevenLabs live TTS+STT; 391 tests.
- 2026-11-23: Phase 6 — fresh-clone test (found + fixed: time-of-day test failure, lockfile
  name drift, missing tzdata for Windows); README "Run it in 3 minutes"; docs/DEMO_SCRIPT.md;
  docs/AWS_ARCHITECTURE.md (built vs design); MIT LICENSE; history checked: no secrets, one
  author, no AI trailers. ElevenLabs + accuracy skipped (no voice id, no real photos). 387 tests.
- 2026-11-23: Phase 5 done — scheduler (reminders, follow-up, missed-dose amber, low-stock
  refill requests, weekly summary), demo clock, voice phrases + ElevenLabs TTS/STT (mocked
  tests) + mock/Twilio calls, simulator voice/call UI + recorder, README Voice section +
  screenshot; live mock run verified across both processes; 387 tests.
- 2026-11-23: Opt-in live tests + network guard; accuracy token report (chore branch).
- 2026-11-23: Phase 4 done (dashboard API + React dashboard + README rewrite), then red alerts
  always go out; 325 tests.
- 2026-11-22: Phases 0-3 done (setup, MCP core, reader + read-back, channels + simulator).
