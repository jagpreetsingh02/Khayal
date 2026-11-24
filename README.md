# Khayal

**A WhatsApp photo of any ordinary glucometer or BP monitor becomes a confirmed, logged reading
and a family that knows. No new app, no new device.**

## Run it in 3 minutes

**No API keys, no accounts, nothing paid.** Mock mode simulates WhatsApp, the AI reader and the
voice notes locally, while the MCP server, the guardrails and the dashboard are the real ones.

You need: **git**, **Python 3.11+**, **Node 20.19+ or 22.12+**, and **[uv](https://docs.astral.sh/uv/)**
(`pip install uv`, or `brew install uv` on macOS).

**macOS / Linux**

```bash
git clone https://github.com/jagpreetsingh02/khayal.git
cd khayal
cp .env.example .env                                  # mock mode, no keys
(cd dashboard && npm ci && npm run build)              # builds the family dashboard once
cd backend && uv sync                                  # Python deps into backend/.venv
uv run python ../scripts/start.py --seed               # 14 days of demo data, then start
```

**Windows (PowerShell)**

```powershell
git clone https://github.com/jagpreetsingh02/khayal.git
cd khayal
Copy-Item .env.example .env
cd dashboard; npm ci; npm run build; cd ..
cd backend; uv sync
uv run python ..\scripts\start.py --seed
```

(If PowerShell refuses to run `npm`, use Command Prompt with `copy` instead of `Copy-Item` and
`&&` instead of `;`.)

You should see `Khayal is running (APP_MODE=mock, CHANNEL=simulator, LLM_PROVIDER=mock)`.
Everything is on one port; **Ctrl+C** stops it.

**Then click through it** (about 2 minutes):

| Where | Click | What you should see |
|---|---|---|
| http://localhost:8000/simulator (the parent's chat, the children's phones, the live MCP log) | **🖼️** → **bp normal**, then the quick reply **1** | "I read 132/84, pulse 74 — correct?" → "Saved: 132/84, pulse 74." and a `log_reading` call in the MCP log |
| Simulator | **🖼️** → **bp high** → **1** | Asked to rest 5 minutes and measure again; the family is **not** alerted yet |
| Simulator | **⏩ Skip 5 minutes** → **🖼️** → **bp high** → **1** | **RED alert** in Ravi's and Priya's phones, plus an **Incoming call** card |
| Simulator (demo clock) | **⏰ Next reminder**, then **⏩ 30 min** without replying (repeat once if needed) | A medicine reminder with an Indian English voice note (▶), one follow-up, then an **AMBER** "2 medicine reminders were marked missed" alert to the children |
| http://localhost:8000/dashboard/ → **Use the demo access token** → `change-me` → **Continue** → **Ravi Gowda** (or **Send me a code** → **Ravi Gowda**: the 6-digit code appears on Ravi's phone in the simulator) | **Medicines** → **Simulate low stock (demo)** on Telma 40 | The AI calls `request_refill` over MCP; the children get "Refill request…" |
| Dashboard | **Overview** → **Approve** | Lakshmi's chat: "Refill approved for Telma 40 (30); your pharmacy will confirm." |
| Dashboard | **What the AI did** | Every MCP tool call; **Approvals by the AI: 0**; `approve_refill: does not exist` |

The full click-by-click video script is in [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md). Checked
on a fresh clone in an empty folder on macOS (23 Nov 2026); the Windows commands are the same
steps but have not been run on Windows.

---

Built for wHACKiest 2026, Track 1, "Make MCP Matter: Less Magic, More Utility".
The most controlled MCP product in the room: **the model can log, remind and *request*. It
cannot approve, prescribe or diagnose, because the approve tool does not exist on its side.**

> **Intended use.** Khayal is a medication-adherence and self-recorded-readings logbook with
> family notifications. It does not diagnose, treat or recommend dosing. Alert limits are set by
> the parent's doctor. Not an emergency service: call 108/112.

<p>
  <img src="docs/screenshots/dashboard-overview-phone.png" alt="Family dashboard on a phone: refill request waiting for approval, a red alert and the latest readings" width="260">
  <img src="docs/screenshots/simulator.png" alt="Web simulator: the parent's WhatsApp-style chat, a child's phone with the red alert, and the live MCP tool-call log" width="560">
</p>

## Problem

**India's elderly parents manage BP and diabetes alone, and families find out too late.**

| Fact | Source |
|---|---|
| 149M Indians aged 60+ in 2022 (10.5%), rising to 347M (20.8%) by 2050 | IIPS/UNFPA India Ageing Report 2023 |
| 36.2% of people aged 60+ have hypertension (measured); 14.2% have diagnosed diabetes | LASI Wave 1 factsheet |
| Only ~10.4% of hypertensive adults aged 45+ have their BP under control | Bhatia et al. 2021 (LASI data) |
| Uncontrolled hypertension is behind 52% of India's cardiovascular deaths | WHO hypertension report 2023 |
| Medication adherence for chronic (NCD) conditions is 51%; 37.5% have 2+ conditions | Tolley et al. 2023 (WHO-SAGE India) |
| 55.6% of rural elders with BP + diabetes are non-adherent; forgetfulness is a driver and family support helps | Jeyalakshmi et al. 2023 |
| 36% of older parents have a migrant child; 35% are empty nesters | Sarkar et al. 2023 (LASI) |
| WhatsApp has ~500M users in India | Meta Q2 2024 earnings call (TechCrunch) |
| Only ~40% of urban elders have a smartphone; 66% find digital tools "too confusing"; 5% use health or banking apps | HelpAge India 2024/2025 |

A real example: in Chennai in April 2024, an 82-year-old man living alone, with his son in the
US, was found only after a phone call went unanswered (DT Next). No one was watching.

Honest framing: Khayal is for elders who have a smartphone with WhatsApp, or a spouse or
caretaker who helps. The numbers already exist on the meter screen at home; they just never
reach the family.

## Solution

1. **Photo vitals.** The parent sends a photo of the BP monitor or glucometer screen. The reader
   extracts the numbers and reads them back ("I read 142/88. Correct? Reply 1 yes / 2 no").
   Nothing is logged as confirmed, and no alert fires, until the parent says yes.
2. **Tiered family alerts** (amber / red) against limits set by the doctor, with a
   repeat-reading rule for severe BP.
3. **Medicine-strip photo** → brand matched to a generic → daily reminder (after confirmation).
4. **Family-approved refills.** Low stock → the AI *requests* a refill → a family member approves
   on the dashboard → licensed partner pharmacy (outside WhatsApp; a labelled stub in this demo).
5. **Family dashboard:** trends, alerts, pending approvals, medicines, doctor-set limits,
   sharing and consent, and a live log of every tool call the AI made.
6. **Reminders and voice.** Medicine reminders at the parent's times, one gentle follow-up,
   missed-dose alerts, automatic low-stock refill *requests* and a weekly summary for the
   family. Voice notes in and out (Hindi and English, Tamil started), and a phone call to the
   family for a confirmed red reading.

### Three human checkpoints

| # | Who | Decides | Enforced where |
|---|---|---|---|
| 1 | **Parent** | Is this the right reading? ("1 yes / 2 no") | `log_reading` only stores `unconfirmed`; confirmation is the parent's reply, handled server-side. `alert_family` refuses unconfirmed readings. |
| 2 | **Doctor** | What counts as too high or too low | Limits are stored as `set_by=doctor`, edited on the dashboard behind "these values come from the doctor", and re-checked by the server on every alert. The AI cannot change them. |
| 3 | **Family** | Should this refill be ordered? | `request_refill` only creates `pending_approval`. Approval exists only at `POST /api/refills/{id}/approve`, after a family member's click. There is no `approve_refill` MCP tool (a test guards this). |

## Architecture

```mermaid
flowchart TB
  subgraph PARENT["Parent lane (chat)"]
    direction LR
    P1["Photo of the meter screen<br/>or a medicine strip"] --> P2["Read-back:<br/>'I read 186/121. Correct? 1 / 2'"] --> P3["Parent replies 1<br/><b>CHECKPOINT 1</b>"]
  end

  subgraph CORE["Khayal core"]
    direction LR
    R["Vision reader<br/>strict JSON, no tools"] --> E["Read-back state machine<br/>plausibility + repeat-BP rules"]
    E --> A["Agent<br/>allowlisted tools only"]
    A -->|"MCP client<br/>streamable HTTP"| T
    subgraph MCP["MCP server (own process)"]
      direction TB
      T["log_reading · get_history · get_meds<br/>create_reminder · alert_family<br/>request_refill · weekly_summary<br/><b>no approve tool</b>"]
      G["Guardrails: sender from _meta, consent,<br/>limits re-checked, quantity caps,<br/>every call written to audit_log"]
      T --- G
    end
    T --> DB[("Store: readings · alerts · refills<br/>outbox · audit_log")]
  end

  subgraph FAMILY["Family lane"]
    direction LR
    F1["Alert on WhatsApp<br/>amber / red"]
    F2["Family dashboard<br/>trends · alerts · what the AI did"]
    F3["Approve / reject refill<br/><b>CHECKPOINT 3</b>"]
    DR["Limits from the doctor<br/><b>CHECKPOINT 2</b>"]
    PH["Partner pharmacy<br/>demo stub, no real order"]
    F2 --> F3 --> PH
    DR --> F2
  end

  P1 -->|photo| R
  E -->|read-back| P2
  P3 -->|"1 = yes, confirmed"| E
  DB -->|outbox| F1
  DB --> F2
  F3 -->|"dashboard API only;<br/>parent gets a status message"| DB
```

- `backend/app/`: FastAPI app (channel webhooks, web simulator, dashboard API, built dashboard at
  `/dashboard`), the read-back conversation engine and the rule-based agent.
- `backend/mcp_server/`: the FastMCP server, run as its own process. The agent calls it through
  a real MCP client over streamable HTTP. The parent's identity travels in the request `_meta`
  set by our backend, never in tool arguments the model writes.
- `dashboard/`: React + Vite + TypeScript + Recharts family dashboard.
- Adapters picked in `.env`: channel (simulator / telegram / twilio), reader (mock / groq;
  bedrock is a stub), storage (sqlite; dynamodb planned), voice (none / mock / elevenlabs),
  calls (none / mock / twilio).
- **On AWS (design only, nothing deployed):** Bedrock for reading and the agent, AgentCore or
  Lambda for the MCP server, API Gateway, S3, DynamoDB, EventBridge Scheduler, Cognito, Amplify
  and End User Messaging Social for WhatsApp, all in Mumbai (ap-south-1). What is built versus
  designed, row by row: [docs/AWS_ARCHITECTURE.md](docs/AWS_ARCHITECTURE.md).

### MCP tools and their guardrails

| Tool | What the AI can do | Guardrails (server-side) |
|---|---|---|
| `log_reading` | Store a BP or glucose reading | Stored as **unconfirmed** only; implausible values are rejected (asks for a retake); parent resolved from `_meta`, never from arguments |
| `get_history` | Read confirmed readings (today / 7d / 14d / 30d) | Read-only; only the sender's own data |
| `get_meds` | Read medicines, reminder times and stock | Read-only |
| `create_reminder` | Set daily reminder times | Only for medicines the parent confirmed; `HH:MM` validated |
| `alert_family` | Tell the family a limit was crossed | Reading must be confirmed; server re-evaluates the doctor's limits; cannot escalate amber→red; first severe BP needs a repeat; amber capped at 3/day (red never capped); deduped per reading; family sees server-written text only; amber is held back if the parent does not share alerts, but **red always reaches the family**; only red on a confirmed reading may ring the family |
| `request_refill` | Ask the family to approve a refill | Creates `pending_approval` only; capped at one month's supply; Schedule X refused; one pending per medicine; refused if the parent does not share medicines |
| `weekly_summary` | Descriptive 7-day summary | Output filter blocks diagnosis and dosing words |
| ~~`approve_refill`~~ | **Does not exist** | Approval lives only in the dashboard API, triggered by a family member |

All tools refuse a parent whose consent is pending, paused or revoked. Every call, including
refusals, is written to `audit_log` and shown as "What the AI did" in the simulator and dashboard.

## More of the demo

After the quick start above (full script with timings: [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md)):

- **Medicine strip:** 🖼️ → `strip telma40` → "Telma 40 = Telmisartan 40 mg. Remind at 8 PM?" →
  **1** → reminder set.
- **Parent-first sharing:** on the dashboard, **Sign out** → sign in as **Lakshmi** (the parent) →
  **Limits & sharing** → turn off *Medicines*. Signed in as Ravi again, medicines and refills
  say "not shared". (Turning off *Alerts* hides amber alerts; red alerts still reach the family.)
- **Voice:** 🗣️ → “I took my medicine” after a reminder marks the dose taken and answers with a
  voice note. **📊 Weekly summary** sends the 7-day summary to the children.
- **Narrow bot:** type `what's the weather?` → "I can only help with readings and medicines."
- **Onboarding:** **Reset demo** starts a fresh family with consent *pending*: `hello` →
  consent notice → **1**.
- Approving a refill twice is safe; the first click wins.

## Screenshots

![Simulator: Indian English voice-note reminder, the parent's English voice-note replies, and an Incoming call card for the red alert in each child's pane](docs/screenshots/voice-and-call.png)

| Trends with doctor-set bands | What the AI did |
|---|---|
| ![BP and glucose charts over 14 days with amber and red bands set by the doctor](docs/screenshots/dashboard-trends.png) | ![Audit log: AI tool calls, refusals, zero approvals by the AI, one approval by family](docs/screenshots/dashboard-audit.png) |

| Refill history (phone) | Sharing and limits (parent's view) | Alerts |
|---|---|---|
| ![Refill approved by Ravi and sent to the demo pharmacy](docs/screenshots/dashboard-refills-phone.png) | ![Consent status, sharing switches and limit editor labelled set by your doctor](docs/screenshots/dashboard-sharing-phone.png) | ![Alerts feed with red and amber levels](docs/screenshots/dashboard-alerts.png) |

Regenerate them with `uv run --with playwright python ../scripts/screenshots.py` while the demo is
running (dev-only; uses your installed Chrome).

## Developing

```bash
# from backend/
uv run pytest                                      # backend tests + dashboard build/lint check
RUN_LIVE_TESTS=1 uv run pytest -m live             # opt-in: real API tests (spends your quota)
uv run ruff check . && uv run ruff format --check .
uv run python ../scripts/start.py                  # keep the current database
uv run python ../scripts/seed_demo.py              # reseed (stop the servers first)
uv run python ../scripts/mcp_client_demo.py        # list MCP tools, call log_reading (needs MCP_SHARED_SECRET; see the script)
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

Dashboard API (all routes need a signed session from `POST /api/login` (mock only) or
`POST /api/login/code/request` + `/api/login/code/verify`; `POST /api/logout` ends it): `GET /api/parents/{id}/readings?range=14d`,
`GET /api/alerts` + `POST /api/alerts/{id}/acknowledge`, `GET /api/refills?status=…`,
`POST /api/refills/{id}/approve|reject`, `GET /api/parents/{id}/medicines`,
`GET|PUT /api/parents/{id}/thresholds`, `GET|PUT /api/parents/{id}/sharing`, `GET /api/audit`.
Interactive docs at http://localhost:8000/docs (mock mode only).

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
- **Telegram:** `CHANNEL=telegram` and `TELEGRAM_BOT_TOKEN` (from @BotFather). The API long-polls,
  so no public URL is needed. Send `/id` to your bot, then register yourself:
  `uv run python ../scripts/add_member.py --channel telegram --ref <id> --role parent --name "Me"`.
- **WhatsApp (Twilio Sandbox):** `CHANNEL=twilio`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`,
  `PUBLIC_BASE_URL` (e.g. an ngrok https URL) and the Sandbox webhook set to
  `<PUBLIC_BASE_URL>/webhooks/twilio`. Requests without a valid `X-Twilio-Signature` are
  refused. Alerts to numbers outside the 24-hour window need `TWILIO_TEMPLATE_CONTENT_SID`.
- **Voice (ElevenLabs):** `VOICE=elevenlabs`, `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`
  (see Voice and reminders). Red-alert phone calls: `CALL_PROVIDER=twilio` + `TWILIO_CALL_FROM`.
- **Dashboard:** in mock mode, set your own `DASHBOARD_DEMO_TOKEN` before showing it to anyone.
  With `APP_MODE=dev` or `aws` the demo token is off: members sign in with a one-time code, and
  the app refuses to start without `SESSION_SECRET` and `MCP_SHARED_SECRET` (see Security).
- **Bedrock / DynamoDB:** `LLM_PROVIDER=bedrock` and `STORAGE=dynamodb` are switches only today
  (a stub reader and a "not implemented" error). See [docs/AWS_ARCHITECTURE.md](docs/AWS_ARCHITECTURE.md).

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
  ([samples/generated_medical_test_images_jpg/](samples/generated_medical_test_images_jpg/)).
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

## Safety and privacy

- **A logbook, not a medical device.** Summaries and alerts describe numbers against limits the
  doctor set; they never interpret, diagnose or suggest doses (CDSCO's 2026 software guidance
  excludes data-management software). Red alerts always say "If you feel unwell, call 108/112".
- **Reading accuracy is not proven on real photos, so every reading is read back.** Strict JSON
  schema, rule-based plausibility checks and mandatory parent confirmation. See
  [Reading accuracy](#reading-accuracy) for the one measurement we have, on AI-generated images.
- **Parent consents, not the child.** The parent agrees in chat, can reply STOP at any time, and
  chooses what the family sees (readings, alerts, medicines). Children cannot change it. One
  exception for safety: red alerts always reach the family, even if alerts are not shared.
  While paused, nothing is logged, alerted or reminded. SPDI-compliant today, built for DPDP (consent
  duties start 13 May 2027). We do not claim end-to-end encryption.
- **Refills happen off WhatsApp.** WhatsApp's policy prohibits drug sales, so chat only carries
  status messages. Orders go to a licensed pharmacy that checks the prescription (a clearly
  labelled stub here). Schedule X medicines are excluded; quantities are capped server-side.
- **Narrow bot.** Only readings, medicines, reminders and refills; anything else gets a fixed
  refusal (general-purpose AI chatbots are banned on the WhatsApp Business API).
- **Prompt injection.** Text inside a photo is data. The vision call has no tools, and every
  limit and cap is enforced by the server, not by the prompt.
- **Data minimisation.** Photos and voice recordings are read and dropped; only the numbers
  (and the transcript shown in the chat) are kept. Every AI tool
  call and every family decision is in the audit log. The MCP server binds to localhost.

## Security

What is protected today:

- **Dashboard sessions** are signed with `SESSION_SECRET` (HMAC-SHA256: member, issued-at,
  expiry, session version). They last `SESSION_TTL_HOURS` (12 h). Expired, tampered or wrongly
  signed sessions get 401. **Sign out** ends every session of that member on every device.
- **Sign-in.** `APP_MODE=mock`: demo token or one-time code. `APP_MODE=dev`/`aws`: one-time code
  only. The 6-digit code goes to the member's own chat (simulator, Telegram or WhatsApp) and is
  stored only as a keyed hash. It lasts 5 minutes, allows 5 tries and works once; a new request
  cancels the old code. The answer is the same whether or not the member exists.
- **Rate limits:** 5 failed sign-ins per IP per 5 minutes, then 429 with `Retry-After`. Code
  requests are limited to 3 per member and 10 per IP per 15 minutes. Every sign-in, failed
  attempt, code request and sign-out is in the audit log, never with the code.
- **Code messages expire with the code.** They are never retried after 5 minutes or retried
  forever. Their text becomes `[code removed]` once delivered or expired, and errors from the
  channel are scrubbed before they reach logs or `delivery_failed`.
- **MCP server:** every request needs `Authorization: Bearer <MCP_SHARED_SECRET>` (FastMCP
  token verifier, constant-time compare), and the server binds to localhost. That is why the
  sender in `_meta` can be trusted: only our API holds the secret. `scripts/start.py` makes a
  random secret per run when it is empty.
- **Unsafe production config is refused.** With `APP_MODE=dev`/`aws`, the app stops with one
  message listing every problem: missing or short secrets, Twilio without `PUBLIC_BASE_URL` or
  `TWILIO_AUTH_TOKEN`, and a Telegram webhook without `TELEGRAM_WEBHOOK_SECRET`.
- **The web simulator is mock-only.** `/simulator` and its API (send as a parent, reset, reseed,
  demo clock, weekly summary now) and the dashboard's "Simulate low stock (demo)" button exist
  only with `APP_MODE=mock`; in `dev` and `aws` they return 404 and `/` opens the dashboard.
  The simulator has no sign-in, which is fine only for a local demo.
- **Headers:** `X-Content-Type-Options`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`
  and a strict CSP on the dashboard and API (only our own scripts). `/docs` and `/openapi.json`
  are on only in mock mode.
- **Alert texts say when the reading was taken** ("BP 198/127 at 2:14 PM, 24 Nov"), so a red
  alert delivered late after retries never reads as if it just happened.

Still demo-grade (not ready for real patients yet):

- **Rate limits live in memory** in one API process. They reset on restart, and several
  processes behind a load balancer would each count separately.
- **Members sign in with a member ID** (in mock mode with the default token, they pick a name).
  A real deployment should look members up by phone number or invite link (Cognito on AWS).
- **The session token sits in the browser's localStorage**, not an HttpOnly cookie, so an XSS
  bug could read it; the strict CSP is the mitigation.
- **No TLS in the app.** HTTPS belongs to the proxy or load balancer in front of it. The demo
  runs on plain HTTP on localhost.
- **Not externally tested:** no penetration test or security review has been done.

## Reliability

- **Family messages are retried, never silently dropped.** Every family-facing message
  (alerts, refill notices, missed-dose notices, weekly summary) goes through a store outbox.
  It is marked delivered only after the channel accepted it. Failed sends retry after 30 s,
  1 min, 2 min, 5 min, then every 10 min. Red alert messages retry until delivered; anything
  else stops after 8 attempts. Final failures and every red failure are written to the audit
  log (`delivery_failed`). The dashboard marks an alert "Not delivered yet" while it retries.
- **No duplicates.** Twilio `MessageSid` and Telegram `update_id` are recorded; a webhook
  retry or a poller restart is acknowledged but not processed twice. Webhooks verify the
  signature, answer at once and process the message in the background.
- **Per-parent order, parents in parallel.** One parent's messages are handled strictly in
  arrival order; different parents run concurrently, so one slow photo read does not hold up
  anyone else.
- **Known gap:** a message is only in memory between the webhook's answer and the end of
  processing; a crash in that window loses it. A durable inbox is future work.

## Roadmap

- [x] Phase 0: repo setup, scaffolds, smoke tests
- [x] Phase 1: MCP core and storage (tools, thresholds, audit log)
- [x] Phase 2: photo reader and read-back confirmation
- [x] Phase 3: channels and agent, golden path in the web simulator
- [x] Phase 4: family dashboard and refill approval
- [x] Phase 5: reminder scheduler, low-stock requests, weekly summary, voice notes, red-alert call
- [x] Phase 6: 3-minute quick start checked on a fresh clone, demo script, AWS production design
  ([docs/AWS_ARCHITECTURE.md](docs/AWS_ARCHITECTURE.md)), MIT licence
- [x] First accuracy run on 16 AI-generated test images (see [Reading accuracy](#reading-accuracy))
- [ ] Measured reading accuracy on labelled real photos of Indian home devices
- [ ] AWS deployment in Mumbai (ap-south-1): Bedrock reader and agent, DynamoDB store,
  EventBridge Scheduler, Cognito, Amplify, End User Messaging Social for WhatsApp
- [ ] LLM tool-calling agent behind the same MCP allowlist; voice notes on Telegram/WhatsApp

## Team

**Team SHERRR**:

- Jagpreet Singh ([github.com/jagpreetsingh02](https://github.com/jagpreetsingh02))
- Swetank Kumar

## License

[MIT](LICENSE)
