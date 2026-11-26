# Developing and configuration

Commands, tests, CI, the dashboard API, running with Docker, voice and reminders, real providers and
the reading-accuracy run. Back to the [README](../README.md).

## Commands, tests and CI

```bash
# from backend/
uv run pytest                                      # backend tests + dashboard build/lint check
RUN_LIVE_TESTS=1 uv run pytest -m live             # opt-in: real API tests (spends your quota)
uv run ruff check . && uv run ruff format --check .
uv run mypy                                        # types (app/, mcp_server/)
uv run python ../scripts/start.py                  # keep the current database
uv run python ../scripts/seed_demo.py              # reseed (stop the servers first)
uv run python ../scripts/mcp_client_demo.py        # list MCP tools, call log_reading (needs MCP_TOKEN_SECRET in .env; see the script)
uv run python ../scripts/family_gateway.py --who   # family gateway (local demo bridge): see docs/FAMILY_MCP.md
uv run python ../scripts/chat_demo.py --demo       # the conversation in the terminal
uv run python ../scripts/accuracy_test.py          # reader accuracy on samples/ (mock)
uv run python ../scripts/accuracy_test.py --reader groq   # real photos: warns, estimates tokens, asks
uv run python ../scripts/accuracy_test.py --reader groq --samples ../samples/generated_medical_test_images_jpg --kind meter --pace 21

# from dashboard/ (hot reload on http://localhost:5173/dashboard/, /api proxied to :8000)
npm run dev
npm run lint
```

**Tests make zero real API calls by default.** `tests/conftest.py` forces mock providers
(whatever your `.env` says) and blocks any non-localhost network connection. Tests that call
real APIs are marked `live` and run only with `RUN_LIVE_TESTS=1`.

**CI** ([.github/workflows/ci.yml](../.github/workflows/ci.yml)) runs on every push and pull
request: ruff, ruff format, mypy and pytest on Python 3.11 and 3.13 (`uv sync --locked`), the
dashboard's `npm ci`, lint and build on Node 22, and an informational `pip-audit` +
`npm audit --omit=dev` job that is allowed to fail.

Dashboard API (all routes need a signed session from `POST /api/login` (mock only) or
`POST /api/login/code/request` + `/api/login/code/verify`; `POST /api/logout` ends it): `GET /api/parents/{id}/readings?range=14d`,
`GET /api/alerts` + `POST /api/alerts/{id}/acknowledge`, `GET /api/refills?status=…`,
`POST /api/refills/{id}/approve|reject`, `GET /api/parents/{id}/medicines`,
`GET|PUT /api/parents/{id}/thresholds`, `GET|PUT /api/parents/{id}/sharing`, `GET /api/audit`.
Interactive docs at http://localhost:8000/docs (mock mode only).

## Run with Docker

Same demo, nothing to install but Docker (no keys, nothing paid):

```bash
git clone https://github.com/jagpreetsingh02/khayal.git && cd khayal
docker compose up --build -d   # first build takes a few minutes
open http://localhost:8000/simulator   # macOS; elsewhere paste it in a browser. /dashboard/ too
```

`docker compose logs -f` shows the logs; `docker compose down` stops it. To reset to the clean
demo: `docker compose down -v && docker compose up -d` (`-v` wipes the demo data; init reseeds).

Two containers from one image: `api` (port 8000, this machine only) and `mcp` (the MCP
server, reachable only from the `api` container). A one-shot `init` step seeds the demo on
first start and, in mock mode, makes random `SESSION_SECRET` / `MCP_TOKEN_SECRET` (and the
legacy `MCP_SHARED_SECRET`) that both services share through the `khayal-data` volume; an
older volume gains the missing key. `.env` is optional and read if present
(`KHAYAL_ENV_FILE=other.env` picks another file). With `APP_MODE=dev` or `aws`, nothing starts
until `SESSION_SECRET` and `MCP_TOKEN_SECRET` are set to 32+ characters (and
`MCP_AUTH_MODE=secret` is refused).
(Docker: checked on macOS with Docker Desktop from a fresh clone, 25 Nov 2026.)

## Screenshots

The screenshots are in [screenshots/](screenshots/) (gallery in
[DEMO_SCRIPT.md](DEMO_SCRIPT.md#screenshots)). Regenerate them with
`uv run --with playwright python ../scripts/screenshots.py` while the demo is running (dev-only;
uses your installed Chrome).

## Voice and reminders

**Reminders (scheduler, `app/scheduler/jobs.py`).** Every 30 seconds one idempotent pass per
consenting parent:

| Job | When | What happens |
|---|---|---|
| Reminder | at each confirmed medicine's time (IST) | "8:00 AM: time for Telma 40. Reply 1 when taken" + voice note |
| Follow-up | no "taken" reply in 30 min | the dose counts as missed; **one** gentle follow-up (a later "1" still counts) |
| Missed doses | 2+ missed in a day (doctor-set limit) | `alert_family` AMBER via MCP, at most once a day, never a call |
| Low stock | ≤ 5 days left | `request_refill` via the real MCP server → family approves on the dashboard. Never approves anything; one try per medicine per day |
| Weekly summary | Sunday 19:00 IST | the `weekly_summary` tool's descriptive text to the children (only if the parent shares readings) |

The simulator's **demo clock** (⏰ Next reminder, ⏩ 30 min, ⏩ 1 day) moves one shared clock for
the API and the MCP server, running the jobs every 5 simulated minutes. Tests drive the same
code through `Scheduler.run(now)`.

**Voice notes out (TTS).** Messages carry a short phrase in the member's language (English,
voiced with the Indian English `en-IN` locale, or Hindi; Tamil for reminders and replies, as a
stretch); the full chat text stays in English. The demo parent is set to English.

- `VOICE=mock` (default): "voice note" bubbles that your browser reads aloud with an installed
  `en-IN` voice when there is one (e.g. Rishi, Tara or Aman on macOS). No keys.
- `VOICE=elevenlabs` + `ELEVENLABS_API_KEY` + `ELEVENLABS_VOICE_ID`: real MP3 voice notes.
  Model `eleven_v4_turbo` (lists Hindi and Tamil in ElevenLabs' models overview, checked 23 Nov
  2026; `eleven_flash_v2_5` is cheaper per character). Audio is cached by text, so repeated
  reminders cost nothing, and `ELEVENLABS_DAILY_CHAR_BUDGET` (default 1500 characters/day)
  protects the free plan. No key, over budget or an API error: the message goes as text only.
  Live check 23 Nov 2026 on the free plan: premade voice **Sarah** with `language_code` `hi`
  and `en`; Scribe transcribed the Hindi clip back word for word. Free plans cannot use
  Shared Voice Library voices (such as native Hindi voices) through the API, only premade ones.
- `VOICE=none`: text only.

**Voice notes in (STT).** 🎤 in the simulator records in the browser; 🗣️ sends sample voice
notes (in English). With ElevenLabs, recordings go to Scribe (`scribe_v3`). The transcript is handled like a
typed reply: "मैंने दवा ले ली" / "I took my medicine" marks the dose taken, "My BP is 142 by 88"
becomes a reading **that still needs the parent's "1"** after the read-back, anything else gets
the fixed refusal. In mock mode only the sample voice notes are understood.

**Red-alert call.** Only a RED alert on a confirmed reading (a severe BP confirmed by a repeat,
or very low glucose) rings the family; amber and missed doses never do. `CALL_PROVIDER=mock`
(default) shows an "Incoming call" card with the spoken script in each child's simulator pane.
`CALL_PROVIDER=twilio` (untested live) calls real numbers registered on the Twilio channel only,
playing the TTS audio (needs `PUBLIC_BASE_URL`) or reading the script; set `TWILIO_CALL_FROM`.

Voice notes go to the simulator; Telegram and WhatsApp still receive the text only (sending audio
there needs a public audio URL; see the roadmap).

## Enabling real providers

- **Vision reader (Groq):** `LLM_PROVIDER=groq`, `GROQ_API_KEY`; 📎 in the simulator then uploads
  a real photo. The free tier allows about 3 photos a minute.
- **Telegram** (built and unit-tested with a mocked Bot API; not yet run live with a real bot):
  `CHANNEL=telegram` and `TELEGRAM_BOT_TOKEN` (from @BotFather). The API long-polls,
  so no public URL is needed. Send `/id` to your bot, then register yourself:
  `uv run python ../scripts/add_member.py --channel telegram --ref <id> --role parent --name "Me"`.
- **WhatsApp (Twilio Sandbox)** (built and unit-tested with signed test requests; not yet run
  live): `CHANNEL=twilio`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`,
  `PUBLIC_BASE_URL` (e.g. an ngrok https URL) and the Sandbox webhook set to
  `<PUBLIC_BASE_URL>/webhooks/twilio`. Requests without a valid `X-Twilio-Signature` are
  refused. Alerts to numbers outside the 24-hour window need `TWILIO_TEMPLATE_CONTENT_SID`.
- **Voice (ElevenLabs):** `VOICE=elevenlabs`, `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`
  (see [Voice and reminders](#voice-and-reminders)). Red-alert phone calls: `CALL_PROVIDER=twilio` + `TWILIO_CALL_FROM`.
- **Dashboard:** in mock mode, set your own `DASHBOARD_DEMO_TOKEN` before showing it to anyone.
  With `APP_MODE=dev` or `aws` the demo token is off: members sign in with a one-time code, and
  the app refuses to start without `SESSION_SECRET` and `MCP_TOKEN_SECRET`, or with
  `MCP_AUTH_MODE=secret` (see [SECURITY.md](SECURITY.md#security)).
- **Bedrock / DynamoDB:** `LLM_PROVIDER=bedrock` and `STORAGE=dynamodb` are switches only today
  (a stub reader and a "not implemented" error). See [docs/AWS_ARCHITECTURE.md](AWS_ARCHITECTURE.md).

## Reading accuracy

Measured 23 Nov 2026 with `scripts/accuracy_test.py` and the Groq reader (`qwen/qwen3.8-27b`),
exact match on every field (systolic, diastolic, pulse, or glucose value and unit):

| Set | Scored | All fields right |
|---|---|---|
| BP monitors | 13 | **11 / 13 (85%)** |
| Glucometers | 3 | 3 / 3 (too few to mean much) |
| Total | 16 | 14 / 16 (88%) |

- **These are AI-generated synthetic test images, not real photos.** We generated them to look
  like phone photos taken at home (clutter, glare, blur, angles) and labelled each by eye
  ([samples/generated_medical_test_images_jpg/](../samples/generated_medical_test_images_jpg/)).
  They are not photos of Indian home devices. **Testing on real photos of Indian home devices
  is planned**, and only that will give a real accuracy number.
- 6 more glucometer images were not scored: Groq's free daily token limit ran out mid-run.
- Both misses were wrong reads, none were refusals: pulse 108 read as 100 on a soft display
  (a person could misread it too), and a photo showing a BP monitor *and* a glucometer was read
  as the glucometer. In the app, the parent sees "I read …, correct?" and would reply "2".
  0 images were rejected as unreadable (none of them should have been).
- We could not use public real-photo datasets: the Oxford seven-segment set is no longer
  downloadable, and Roboflow sets need an account key and only label single digits (details in
  the samples folder README).
- **Real-photo harness, ready for photos:** put phone photos of real meters in a folder and run
  `uv run python ../scripts/accuracy_test.py --import <folder>` (copies them with EXIF removed),
  fill in `samples/real_photos/labels.csv` (file, type, sys, dia, pulse, glucose, unit), then
  `--reader groq`. Synthetic and real are reported separately, each with per-field exact match,
  **retake/refused** (the safe outcome) and **wrong but confident** (read back with a wrong
  number); the result goes to `samples/real_photos/RESULTS.md`. How to take and label photos:
  [samples/real_photos/README.md](../samples/real_photos/README.md). Tests never call Groq.
