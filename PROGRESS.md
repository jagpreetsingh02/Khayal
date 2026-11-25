# PROGRESS — Khayal (read this first every session)

Submitted 24 Nov 2026 (Devnovate). Repo changes allowed until **26 Nov 2026**; pitch after.
Spec: `docs/PROJECT_BRIEF.md` (read only the sections you need). Rules: `CLAUDE.md`.
Repo: https://github.com/jagpreetsingh02/Khayal (public; renamed, remote updated)

## Current status
- Submission (Phase 1 of judging) is in: tag **`submission-phase1`** = the exact submitted
  commit. Repo may change until **26 Nov 2026**; `main` must stay demo-ready for the pitch.
- Current work stream: **Hardening** (below). H1-H5, **S1 durable inbox** and **S2 per-call
  tokens** done and merged (tags `hardening-h1-done` … `hardening-h5-done`, `feature-s1-done`,
  `feature-s2-done`).
- Current branch: `main`
- **Next step: ask the user.** Candidates: Postgres/DynamoDB adapter (the inbox needs one
  too), schema versioning, structured logging, DPDP export/delete.
- **Pitch demo path changed (H4):** severe BP now asks the warning-signs question first:
  bp high → 1 → **2** → Skip 5 min → bp high → 1 → **2** → RED. (Answer 1 = RED at once.)

## Hardening (production) — one branch per phase, merged with --no-ff, tagged when done
- [x] **S2 tokens + family view** (`feature-s2-tokens`, tag `feature-s2-done`): API mints an
  HS256 JWT per MCP call (`mcp_server/tokens.py`: iss khayal-api, aud khayal-mcp, sub member,
  role, scope, exp 60 s, jti) with `MCP_TOKEN_SECRET`; server = FastMCP `JWTVerifier` subclass
  (+ exp/iat/jti required, lifetime <=60, known role); caller from `sub`, `_meta` cross-check
  (mismatch refused + audited `identity_mismatch`). Scopes per tool (`mcp_server/scopes.py`),
  enforced + tools/list filtered in `AccessMiddleware`; manifest pinned per role (parent,
  family). Family reads: get_history/get_meds/weekly_summary + optional `parent_name`
  (registry match), dashboard rules moved to `app/sharing.py`; audit `family_mcp:<tool>` actor
  member "Viewed by" (dashboard shows it). `MCP_AUTH_MODE=secret` mock only. Gateway
  `scripts/family_gateway.py` + `docs/FAMILY_MCP.md`. Tests +31 (red-team, gateway, dashboard).
- [x] **H1-H5, S1** (tags `hardening-h1..h5-done`, `feature-s1-done`): outbox leases/retries
  (red text forever), dedup, per-member FIFO; signed sessions, codes, rate limits, startup
  check, CSP, simulator mock-only, mypy, CI, Docker, `/health` (H1-H3); AHA/ACC warning-signs
  question, ADA 54/70, annotations/output schemas, `safe_text`, Host/Origin guard, manifest pin
  (`uv run python -m mcp_server.manifest --update`), red-team tests (H4); silent parent at
  severe BP = RED `bp_severe_no_reply` (H5); durable inbox `app/channels/inbound.py` with
  leases, retries, replay safety (S1, `test_inbox.py`). Details: README sections.

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
- Docker uses your `.env` if present: it has `LLM_PROVIDER=groq` + keys, so `docker compose up`
  would read photos with Groq. For a keyless run: `KHAYAL_ENV_FILE=.env.example docker compose up`.
- **Real photos** of Indian home devices for a real accuracy number: none yet. Public sets were
  unusable (Oxford zip gone/no licence; Roboflow needs an account key + digit-box labels only).
  Optional: a free Roboflow API key would allow a public-photo run.
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
  python -m pytest` (669 tests, zero real API calls; `RUN_LIVE_TESTS=1` adds the 2 live ones).
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
  `JWTVerifier(public_key=<secret>, algorithm="HS256", issuer, audience)` supports HMAC but
  accepts tokens WITHOUT exp (we subclass). `get_access_token()` (fastmcp.server.dependencies)
  -> `.claims/.scopes/.subject`; None in-process. Tool `auth=require_scopes()` is NOT used: on
  the in-memory transport the token is None, so every tool would be hidden (only stdio skips
  checks). FastMCP's logger has `propagate=False` (tests attach a handler). joserfc 1.8 signs.
- FastMCP 4: `mcp.run(transport="http")`; tests via `fastmcp.Client(mcp)` + `asyncio.run`;
  `_meta` = `ctx.request_context.meta`; audit + clock sync in `Middleware.on_call_tool`.
- Dashboard: Vite 8 (`base: '/dashboard/'`), React 19, TS 7, Recharts 3, oxlint (no refs/Date.now
  in render). Chart colours validated: blue `#2a78d6` + aqua `#1baf7a`; lines `linear`.
- ElevenLabs (23 Nov): TTS `/v1/text-to-speech/{voice_id}` (`eleven_v4_turbo`), STT
  `/v1/speech-to-text` (`scribe_v3`); free plan = premade voices only (Library -> 402).
- Docker (29.7, Compose v5.3): `env_file` keeps the inline comment of an EMPTY value;
  docker/entrypoint.sh blanks values starting with `#`. Test runs: `KHAYAL_ENV_FILE=<file>`,
  `KHAYAL_PORT=8010`, `-p khayal-devtest` (Desktop may need `open -a Docker`).
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
- Inbox: tests patch `app.clock.real_now` (leases/retries); `SQLiteStore.close()` takes the lock
  (closing during a worker query segfaulted). Live kill test recipe: scratch DB, MCP + uvicorn
  started directly, `CHANNEL=twilio` with a dummy token, `HTTPS_PROXY` to a closed port.

## Decisions log
- 2026-11-25 (S2): per-call token (not per session); in-process calls and secret mode keep
  `_meta` identity (no HTTP = same process). Scopes enforced in our own middleware (works on
  every transport); weekly_summary accepts readings:read or summary:read. Paused consent:
  family reads follow the dashboard rule (paused = what was shared stays visible), per "reuse
  the dashboard's rules". Family audit stores no data (`{"viewed": true}`). `ping()` uses sub
  `khayal-api:health` (lists parent tools; calls fail). Gateway refused in aws / non-local URL.
- 2026-11-24 (S1/H4): inbox uses the real clock, engine the message's demo-clock time; an
  errored message never blocks newer ones; replay snapshot restored only if nothing ran since.
  Asymptomatic severe repeat = red; low-sugar no-recheck = amber; one alert per (reading,
  rule); pronoun field (they/their default); `safe_text` rejects digits-7+, links, "@",
  markup, instruction words.
- 2026-11-22 (Ph 0-3): mock-first, SQLite now; Groq dev / Bedrock AWS; no AI attribution; no
  `approve_refill` MCP tool; identity server-side (S2: token `sub`), no parent_id args; `log_reading` stores
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
- 2026-11-25: S2 per-call tokens + family view — 638 -> 669 tests; live: start.py, gateway over
  stdio, Docker keyless (init made MCP_TOKEN_SECRET, red alert path), no token in logs.
- 2026-11-22..25: Phases 0-6 (393) · H1 409 · H2 439 · H3 449 · H4 607 · H5 614 · S1 638.
