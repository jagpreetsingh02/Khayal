# PROGRESS — Khayal (read this first every session)

Submitted 24 Nov 2026 (Devnovate). Repo changes allowed until **26 Nov 2026**; pitch after.
Spec: `docs/PROJECT_BRIEF.md` (read only the sections you need). Rules: `CLAUDE.md`.
Repo: https://github.com/jagpreetsingh02/Khayal (public; renamed, remote updated)

## Current status
- Submission (Phase 1 of judging) is in: tag **`submission-phase1`** = the exact submitted
  commit. Repo may change until **26 Nov 2026**; `main` must stay demo-ready for the pitch.
- Current work stream: **Hardening** (below). H1-H4 done and merged (tags
  `hardening-h1-done` … `hardening-h4-done`). CI green on `main`.
- Current branch: `main`
- **Next step: ask the user.** H4 item 5 (durable inbox: webhook payloads into an inbox table
  with unique provider message id before the 200, processed from it, retried on startup) was
  **not started** — deferred to keep `main` stable for the 26 Nov pitch. Other candidates:
  Postgres/DynamoDB adapter, schema versioning, structured logging, DPDP export/delete.
- **Pitch demo path changed (H4):** severe BP now asks the warning-signs question first:
  bp high → 1 → **2** → Skip 5 min → bp high → 1 → **2** → RED. (Answer 1 = RED at once.)

## Hardening (production) — one branch per phase, merged with --no-ff, tagged when done
- [x] **H1-H3** (tags `hardening-h1/h2/h3-done`): outbox leases + retries (red text forever),
  inbound dedup, per-member FIFO; signed sessions, codes, rate limits, MCP bearer, startup check,
  CSP; simulator mock-only, mypy 0, CI (3.11+3.13, dashboard, audit), Docker + Compose,
  `/health`. Details: README "Security", "Reliability", "Production readiness".
- [x] **H4 clinical + MCP** (`hardening-h4-mcp`, tag `hardening-h4-done`): 2025 AHA/ACC
  warning-signs question at severe BP (yes = RED + 108/112; no / no reply 5 min (audited) =
  rest + repeat; asked again at the repeat; asymptomatic repeat = RED "Urgent: contact her
  doctor today"); ADA 2026 glucose floors 54/70 (settings only stricter), 54-69 amber + recheck
  15 min (none = amber `glucose_low_no_recheck`), faint/confused/help = RED; doctor's low-sugar
  plan (dashboard Settings card, `/api/parents/{id}/care-plan`, `add_member --low-sugar-plan`);
  "limit her doctor set" via `Member.pronoun` (seeds: she); weekly home average vs 135/85.
  MCP: titles, all 4 annotations, output schemas + text summary (`ToolResult`), actionable
  `ToolError`s, `get_history.response_format`, `alert_family(level, reading_id)` only,
  `create_reminder.note`, `strict_input_validation`. Plausibility 60-260/30-160, HI/LO, unit
  mix-ups. Reader: no free text, strips return an offered id only (`cat:<slug>` / `med_...`).
  Names/medicines/notes/plan checked at write time (`app/storage/safe_text.py`). Host/Origin
  guard (`mcp_server/http_guard.py`). Manifest pin (`mcp_server/manifest.py`,
  `tool_manifest.sha256`; update: `uv run python -m mcp_server.manifest --update`).
  Tests: `test_redteam.py`, `test_clinical.py`, `test_tool_metadata.py`; 449 -> 607 (+2 live skipped).
  Real-photo harness: `accuracy_test.py --import/--real`, `samples/real_photos/`.

## Phase checklist
- [x] Phase 0 — setup · [x] Phase 1 — MCP core + storage · [x] Phase 2 — reader + read-back
- [x] Phase 3 — channels + agent · [x] Phase 4 — dashboard + approve refill
- [x] Phase 5 — voice + reminders · [x] Phase 6 — quick start, demo script, AWS design, hygiene

## Blockers / needs from user
- **Indian English on ElevenLabs:** no premade voice is Indian (all US/UK/AU); library voices
  (e.g. Muskaan, Hindi) get HTTP 402 `paid_plan_required` on the free plan (checked 23 Nov).
  Either `VOICE=mock` locally (browser en-IN voice) or a paid plan + an Indian voice id.
- Local only: in `.env` set `DATABASE_URL=sqlite:///./khayal.db` and rename
  `backend/care_circle.db` (+ `-wal`/`-shm`) to `khayal.db*` with servers stopped, or reseed.
  (GitHub repo rename to `Khayal` is done.)
- Docker uses your `.env` if present: it has `LLM_PROVIDER=groq` + keys, so `docker compose up`
  would read photos with Groq. For a keyless run: `KHAYAL_ENV_FILE=.env.example docker compose up`.
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
  python -m pytest` (607 tests, zero real API calls; `RUN_LIVE_TESTS=1` adds the 2 live ones).
  Local venv was moved from ~/Downloads/care-circle, so `.venv/bin/pytest` has a stale shebang:
  use `python -m pytest` (or `uv sync --reinstall` to fix the scripts).
- **Demo: `uv run python ../scripts/start.py --seed`** -> http://localhost:8000/simulator and
  /dashboard/ ("Use the demo access token" -> `change-me`, or "Send me a code": the code shows
  in that member's simulator phone). Local `.env` has a custom demo token. Simulator: ⏰ Next reminder / ⏩ 30 min / ⏩ 1 day (demo clock),
  📊 Weekly summary, 🗣️ sample voice notes, 🎤 recorder, ⏩ Skip 5 minutes, Reset demo.
- Background runs: start with `.venv/bin/python ../scripts/start.py` (not `uv run`) so stopping
  the task stops the servers; after stopping, check ports 8000/8001 are free.
- Screenshots: demo running, then `uv run --with playwright python ../scripts/screenshots.py`
  (installed Chrome via `channel="chrome"`).

## Tech notes (verified versions)
- uv backend (Python 3.14 local, >=3.11). FastAPI 0.165, **FastMCP 4.2.4**, pydantic 2.14,
  pydantic-settings 2.16 (python-dotenv comes with it), pytest 9.2, ruff 0.17, httpx 0.28,
  tzdata (needed on Windows: no system tz db; check with `PYTHONTZPATH= uv run pytest`).
- FastMCP 4 auth: subclass `fastmcp.server.auth.TokenVerifier` (`async verify_token` ->
  `AccessToken | None`), pass `FastMCP(auth=...)` -> 401 before tools; client `Client(url,
  auth="<token>")` sends Bearer (in-memory transport rejects `auth`). itsdangerous not installed.
- FastMCP 4: `mcp.run(transport="http")`; tests via `fastmcp.Client(mcp)` + `asyncio.run`;
  `_meta` = `ctx.request_context.meta`; audit + clock sync in `Middleware.on_call_tool`.
- Dashboard: Vite 8 (`base: '/dashboard/'`), React 19, TS 7, Recharts 3, oxlint (no refs/Date.now
  in render). Chart colours validated: blue `#2a78d6` + aqua `#1baf7a`; lines `linear`.
- ElevenLabs (checked 23 Nov): TTS `POST /v1/text-to-speech/{voice_id}?output_format=mp3_44100_64`
  (`xi-api-key`; body text, model_id `eleven_v4_turbo`, language_code hi/en); STT
  `POST /v1/speech-to-text` (`scribe_v3`, reply language ISO 639-3, we send no hint). Free plan:
  premade voices only (Library voices -> 402), ~1 credit/char.
- Docker (29.7, Compose v5.3): Compose `env_file` keeps the inline comment of an EMPTY value
  (`X=   # note` -> "# note"); non-empty values are fine. docker/entrypoint.sh blanks values
  starting with `#`. Build: `docker compose build` (3 services share one image `khayal:local`).
  Desktop may need starting: `open -a Docker`. Test runs: `KHAYAL_ENV_FILE=<file>`,
  `KHAYAL_PORT=8010`, `-p khayal-devtest`.
- CI actions (checked 24 Nov): checkout@v7, setup-node@v7, **setup-uv has no moving major tag**
  -> `astral-sh/setup-uv@v10.4.0`. mypy 2.4.1. FastAPI 0.165 includes routers lazily
  (`app.routes` shows `_IncludedRouter`), so test routes by requests, not introspection.
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
- 2026-11-24 (H4): asymptomatic severe repeat stays **red** (rings family) with "contact her
  doctor today" wording; no-recheck after a low sugar is **amber** (respects sharing, cap).
  One alert per (reading, rule) via `alerts.rule`; `alert_family` sends the most urgent unsent
  candidate. Symptom answers stored on `readings.symptoms` by the engine only. Strip candidates
  = parent's medicines + curated brand list (else a fresh family could never add one). Family
  pronoun field (default they/their) rather than inferring from names. `safe_text` rejects
  digits-7+, links, "@", markup, instruction words (system/ignore/send/approve/...).
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
- 2026-11-23 (Ph 5-6, chores): live tests opt-in; nothing runs on AWS (Bedrock/DynamoDB = stubs);
  demo clock = kv offset added by `utcnow()` (zero in aws); scheduler jobs idempotent; follow-up
  +30 min marks missed; low stock <=5 days -> one `request_refill`/med/day; missed-doses AMBER
  once per IST day; weekly summary Sun 19:00 IST. Voice = short en/hi phrases, VOICE=mock
  default, TTS cache + daily budget, STT parsed like text (still needs "1"), calls only for RED
  on a confirmed reading. Accuracy only on AI-generated images, labelled so. Demo parent in
  English, en-IN voice. Name **Khayal** (old commits/tags keep Care Circle).
- 2026-11-24 (H1-H3): red retry-forever = alert *text*; red calls stop at 8. Busy parents
  skipped by ticker. Logout = per-member session version; session times use the real clock,
  codes/outbox the demo clock. Simulator + low-stock demo mock-only (404 elsewhere). Docker
  `init` writes mock secrets once; dev/aws startup check exits 1 on unsafe config.

## Session log (newest first, one line each)
- 2026-11-24: Hardening H4 — clinical rules, MCP metadata, red-team suite (mutation-checked),
  manifest pin (same hash on 3.11/3.12/3.14), real-photo harness; start.py demo walked over
  HTTP (mock providers, scratch DB): read-back, severe → question → repeat → RED, refill
  approve, MCP 403/401. Durable inbox deferred.
- 2026-11-24: Hardening H1 (409 tests), H2 (439), H3 (449; CI + Docker checked locally).
- 2026-11-22/23: Phases 0-6 + chores done (387 tests at phase 6, 393 after chores).
