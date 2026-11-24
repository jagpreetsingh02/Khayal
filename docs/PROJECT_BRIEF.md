# Khayal — Project Brief (research + architecture + build plan)

Source: team research done 22 Nov 2026 (6 research areas: problem evidence, competitors,
core tech, sponsor tech, safety/regulation, business model). Figures marked **(est.)** are our
own estimates. Re-check prices on the day you use them.

Contents
1. Hackathon context and deliverables
2. Problem (evidence)
3. Solution and the research-driven design decisions
4. Architecture (target on AWS + how the prototype maps to it)
5. Components in detail
6. Rules: plausibility, vision schema, alert thresholds, bot scope, consent
7. Data model
8. Repo structure
9. Build phases with acceptance criteria
10. Configuration (.env)
11. Costs and free tiers
12. Demo script and README requirements
13. Competitors and novelty
14. Open risks to verify

---

## 1. Hackathon context and deliverables

- Event: wHACKiest 2026. Track 1 "Make MCP Matter — Less Magic, More Utility": an MCP-powered
  solution a regular person would actually use daily; useful, reliable, **controlled**
  interactions with apps people already use.
- Sponsor bonus prizes: **Best Use of ElevenLabs**, **Best Use of AWS**.
- Phase 1 (online ideathon) deadline: **24 Nov 2026, 11:59 PM IST**, via Devnovate:
  1. PPT on the official 8-slide template (no extra slides), named `TeamName_Track1`,
     Google Drive link with "Anyone with the link – Viewer".
  2. 3–5 min YouTube video (public or unlisted) explaining the PPT and idea.
  3. **GitHub repository link** — evaluated on Ideation day (26–27 Nov). A basic working
     demo of the core concept is enough; it need not be complete or production-ready.
     The repo must be accessible to evaluators (public).
- Pitch: 26–27 Nov, exactly 5 min + 3 min Q&A. The 24-hour in-person hackathon follows.

Implication for the repo: by 24 Nov, `main` must contain a runnable mock-mode demo of the
golden path + a strong README. Real integrations are a bonus where keys are available.

## 2. Problem (evidence — reuse in README/PPT)

One line: India's elderly parents manage BP and diabetes alone, and families find out too late.

| Fact | Source |
|---|---|
| 149M Indians aged 60+ in 2022 (10.5%) → 347M (20.8%) by 2050 | IIPS/UNFPA India Ageing Report 2023 |
| 36.2% of 60+ have hypertension (measured); 14.2% diagnosed diabetes | LASI Wave 1 factsheet |
| Only ~10.4% of hypertensive adults 45+ have BP under control | Bhatia et al. 2021 (LASI data) |
| Uncontrolled hypertension → 52% of India's cardiovascular deaths | WHO hypertension report 2023 |
| NCD medication adherence 51%; 37.5% with 2+ conditions | Tolley et al. 2023 (WHO-SAGE India) |
| 55.6% of rural elders with BP + diabetes non-adherent; forgetfulness a driver; family support helps | Jeyalakshmi et al. 2023 |
| 36% of older parents have a migrant child; 35% empty nesters | Sarkar et al. 2023 (LASI) |
| WhatsApp ~500M users in India | Meta Q2 2024 earnings call (TechCrunch) |
| Only ~40% of urban elders have a smartphone; 66% find digital tools "too confusing"; 5% use health/banking apps | HelpAge India 2024/2025 |
| Real example: Chennai, Apr 2024 — 82-year-old living alone, son in the US, found only after a call went unanswered | DT Next |

Honest framing: target = elders with a smartphone, or helped by a spouse/caretaker. Frame the
Chennai case as "no one was watching", not "a missed reading killed him".

## 3. Solution and research-driven design decisions

One line: Khayal turns a WhatsApp photo of any ordinary glucometer or BP monitor into a
confirmed, logged reading and a family that knows — no new app, no new device.

Features:
1. Photo vitals: BP (sys/dia/pulse) and glucose from a photo of the screen, with read-back confirmation.
2. Medicine-strip photo → medicine identified (brand → generic) → reminder created (after confirmation).
3. Tiered family alerts (amber/red) with a repeat-reading rule for BP.
4. Family-approved refill **request** → licensed partner pharmacy (outside WhatsApp).
5. Voice: parent can send voice notes; replies/summaries as voice notes in Indian languages (ElevenLabs); red-alert phone call to the child.
6. Weekly descriptive summary.
7. Family dashboard: trends, alerts feed, pending refill approvals, thresholds, consent settings.

Positioning: **the most controlled MCP product in the room**. Every AI action is a named,
permissioned MCP tool call; three human checkpoints: parent confirms reading, doctor sets
limits, family approves refills. Pitch line: "The model can log, remind and *request*.
It cannot approve, prescribe or diagnose — the approve tool doesn't exist on its side."

Design decisions forced by research (do not undo):
- **Refills off WhatsApp.** WhatsApp Business Messaging Policy prohibits "Drugs, whether
  prescription, recreational, or otherwise" irrespective of licences. Flow: low stock →
  `request_refill` creates `pending_approval` → family approves in dashboard → order to
  licensed pharmacy (prescription check) → WhatsApp sends only a status message.
  Exclude Schedule X / psychotropic drugs. Telemedicine Guidelines 2020: AI may not counsel or prescribe.
- **Photo-reading accuracy is unproven.** No benchmark for current vision LLMs on these
  displays (one 2024 test: GPT-4o-mini 57% on a 7-segment display; known 1/7 and 0/8 confusions).
  So: strict JSON schema + rule-based plausibility checks + mandatory read-back. Measure our
  own accuracy on ~30 real photos and report the number; never claim one we haven't measured.
- **Parent consents, not the child.** SPDI Rules 2011 apply now; DPDP Rules' consent duties
  start 13 May 2027. Claim "SPDI-compliant today, built for DPDP" — not "DPDP-compliant".
  Don't claim end-to-end encryption (Meta Cloud API decrypts before delivery to the business).
  Store data in ap-south-1 (Mumbai); keep numbers, delete photos after confirmation; audit log.
- **Logbook, not a medical device.** CDSCO's 2026 software guidance excludes data-management
  software. Doctor-set thresholds; summaries describe, never interpret. Intended-use line:
  "Khayal is a medication-adherence and self-recorded-readings logbook with family
  notifications. It does not diagnose, treat or recommend dosing. Alert limits are set by the
  parent's doctor. Not an emergency service: call 108/112."
- **Narrow bot.** Meta bans general-purpose AI chatbots on the WhatsApp Business API (from
  15 Jan 2026); task bots (reminders, notifications) are allowed. Refuse off-topic requests.
- **Prompt injection.** A strip/photo may contain text like "order 10 boxes". Vision call has no
  tools; quantity caps enforced server-side.

## 4. Architecture

### 4.1 Target architecture (pitch / hack day)

```
[Parent: WhatsApp photo / voice note]      [Child: web dashboard]   [Child: phone]
            |                                        |                     ^
            v                                        v                     |
 Twilio Sandbox (hack day) | AWS End User Msg Social (prod)    Amplify + Cognito
            |                                        |                     |
            v                                        v                     |
   API Gateway -> Lambda webhook  ----------->  Dashboard API (Lambda)      |
       |            |                                |  approve_refill only |
       |   S3 photo -> Bedrock vision (JSON schema, NO tools)              |
       |            -> plausibility rules -> read-back "142/88 correct?"   |
       |   voice note -> ElevenLabs Scribe v3                              |
       v                                                                   |
  Agent loop (Bedrock / AgentCore Runtime) ----MCP----> Khayal MCP server (FastMCP on AWS)
                                                         log_reading | get_history | create_reminder
  ElevenLabs Agent (voice calls) ---------MCP----------> alert_family | request_refill | weekly_summary
       ^                                                        |            |            |
       |                                         DynamoDB <-----+  EventBridge Scheduler  |
       +---- red alert: outbound call via Twilio ---------------------------------------- +
  Partner pharmacy (licensed, Rx check)  <-- approved order (outside WhatsApp)
```

Lanes: parent (left) → MCP core (middle) ← family (right). ElevenLabs = voice in/out + red-alert
call. AWS (all ap-south-1) = Lambda, API Gateway, S3, Bedrock, DynamoDB, EventBridge Scheduler,
Cognito, Amplify, CloudWatch; AWS End User Messaging Social = production WhatsApp path.
Amazon Polly has Indian voices only in Hindi and Indian English → ElevenLabs covers Tamil,
Telugu, Bengali, Marathi, Punjabi.

### 4.2 Prototype architecture (what we build first — runs locally, free)

Same boundaries, swappable adapters:

| Concern | Mock mode (no keys) | Real dev mode | Hack-day / AWS |
|---|---|---|---|
| Channel | Web simulator chat page (upload photo, see replies) | Telegram bot (free, easiest for evaluators) or Twilio WhatsApp Sandbox | Twilio Sandbox → AWS End User Messaging Social |
| Vision reader | `MockReader` (looks up `samples/labels.json` by file hash/name) | Groq API, vision model (free plan, check limits) | Bedrock vision model (verify ap-south-1 availability) |
| Agent | Deterministic rule-based agent calling MCP tools | LLM tool-calling agent (Groq/Bedrock) via MCP client | Bedrock / AgentCore |
| MCP server | FastMCP, local (streamable HTTP) | same | FastMCP on AWS (Lambda/AgentCore Gateway) |
| Storage | SQLite | SQLite | DynamoDB |
| Scheduler | APScheduler in-process | same | EventBridge Scheduler |
| Voice | off | ElevenLabs TTS/Scribe (free plan) | + ElevenLabs agent outbound call via Twilio |
| Dashboard | React + Vite, local | same | Amplify + Cognito |

Backend process: one FastAPI app (webhooks, simulator, dashboard API) + one FastMCP server
(can be mounted in the same process or run separately). The agent talks to the MCP server
through an MCP client — the MCP boundary must be real, not just function calls, because MCP
is the Track 1 judging criterion.

## 5. Components in detail

### 5.1 Channel layer
Interface: `receive(event) -> IncomingMessage(user_ref, text|photo_bytes|audio_bytes)` and
`send(user_ref, text, audio=None)`. Implementations: `simulator`, `telegram`, `twilio_whatsapp`.
- Twilio Sandbox facts: users must send `join <code>` to +1 415 523 8886; session lasts 3 days;
  max ~1 msg / 3 s; business-initiated messages limited to 3 fixed templates (Appointment
  Reminders, Order Notifications, Verification Codes). Image arrives as `MediaUrl0` (≤16 MB);
  media download may require Basic auth with account SID/token — test it.
- Inside the 24-hour customer-service window, free-form replies are fine; proactive
  alerts/reminders outside it need templates (production: utility templates `dose_reminder`,
  `bp_alert`, `refill_update`).
- Telegram: no templates, no 24h window, free; photos via `getFile` (≤20 MB); ~1 msg/s per chat.
- Sender mapping: `user_ref` → member (parent/child) resolved server-side from the DB.

### 5.2 Reader (photo / voice → data)
- Photo → vision model with a strict JSON schema (§6.2), **no tools**, temperature 0.
- Then plausibility rules (§6.1) in code. Optionally a second read; if they disagree → ask retake.
- `mmol/L` → mg/dL: × 18.
- Voice note → speech-to-text (ElevenLabs Scribe) → simple intent parse
  ("dawai le li" = dose taken; spoken numbers = reading, still needs read-back).
- Medicine strip → brand + strength from vision → fuzzy match (`rapidfuzz`) against an Indian
  medicine dataset (MIT-licensed "Indian-Medicine-Dataset" on GitHub, ~254k brands with
  composition; or govt CDCI, ~94k brands) → read back "Telma 40 = Telmisartan 40 mg. Correct?"
  For the prototype a small curated CSV of ~50 common BP/diabetes brands is fine.
- Delete the photo after confirmation (keep `image_ref` only until then).

### 5.3 Read-back state machine (per parent)
`idle` → (photo) → `awaiting_confirmation{reading_id}` → reply `1` → `confirmed` → agent acts;
reply `2` → `rejected` → ask for retake or typed value → back to `awaiting_confirmation`.
Timeout (e.g., 30 min) → discard unconfirmed reading. Repeat-reading flow for high BP:
`awaiting_repeat{first_reading_id, due_at = +5 min}` (§6.3).

### 5.4 Agent
Takes confirmed events (reading confirmed, dose reply, strip confirmed, scheduled triggers)
and decides tool calls. Allowed tools only (allowlist). In mock mode: deterministic rules
(confirmed reading → `log_reading` → evaluate thresholds → `alert_family` if needed).
In LLM mode: tool-calling model connected to the MCP server through an MCP client.
Every tool call is written to `audit_log`.

### 5.5 Khayal MCP server (FastMCP)

| Tool | Does | Guardrail |
|---|---|---|
| `log_reading(type, values, confidence, image_ref)` | Writes reading (status `unconfirmed` → `confirmed`) | `parent_id` from server context, never from LLM args |
| `get_history(range, type, response_format)` / `get_meds()` | Reads past readings (concise stats by default, `detailed` = every reading) / medicines | Read-only |
| `create_reminder(med_id, times, note?)` | Creates reminder schedule (Asia/Kolkata) | Only for confirmed medicines; note ≤ 40 chars, no links / numbers / instructions |
| `alert_family(level, reading_id)` | Sends alert to family members; red → also triggers voice call (if enabled) | No recipient or free text accepted (H4); server writes the text; rate-capped for amber; red never suppressed |
| `request_refill(med_id, qty)` | Creates `pending_approval` refill only | Qty cap (e.g. ≤ 1 month supply); Schedule X excluded |
| `weekly_summary()` | Descriptive summary text (+ audio) | Output filter blocks diagnosis/dosing words |
| ~~`approve_refill`~~ | **Does not exist as an MCP tool.** Only `POST /api/refills/{id}/approve` in the dashboard API, authenticated family member | Human in the loop by construction |

H4 (24 Nov): every tool has a title, all four annotations set explicitly (only `alert_family`
is open-world; `get_history`, `get_meds`, `weekly_summary` are read-only), an output schema
with structured content + a text summary, `isError` results with what to do, and the manifest
is pinned by hash (`backend/mcp_server/tool_manifest.sha256`). README "MCP tools" has the table.

Mention in pitch: MCP elicitation (form/URL mode) is the standards-aligned concept for this
checkpoint; we implement it as a DB state machine because our "client" is a backend.
MCP spec version as of Nov 2026: 2026-07-28 (stateless). FastMCP 4.x (4.2.4, 20 Nov 2026) —
verify actual installed API. ElevenLabs agents can attach external MCP servers over streamable
HTTP with per-tool approval modes; AWS Bedrock AgentCore Gateway can expose Lambdas as MCP tools.

### 5.6 Dashboard API + family dashboard
- API: `GET /api/parents/{id}/readings?range=14d`, `GET /api/alerts`, `GET /api/refills?status=pending_approval`,
  `POST /api/refills/{id}/approve|reject`, `GET/PUT /api/parents/{id}/thresholds`,
  `GET/PUT /api/parents/{id}/sharing` (consent/visibility), `GET /api/audit`.
- Simple auth for prototype (demo login / token); Cognito on AWS.
- UI (React + Vite + TS + Recharts): BP chart (sys/dia lines + threshold bands), glucose chart,
  alerts feed with acknowledge, pending refills with Approve/Reject, medicines + reminders,
  thresholds editor marked "set by doctor", audit log ("what the AI did"), consent status.
- Seed 14 days of realistic history (`scripts/seed_demo.py`) so charts look real.

### 5.7 Scheduler
Reminders at medicine times → message parent ("8 PM: Telma 40. Reply 1 when taken").
No reply in 30 min → mark missed; N missed doses → amber alert. Weekly summary job.
Low-stock check → `request_refill`.

### 5.8 Voice (ElevenLabs)
- TTS: Eleven v4 Turbo / Flash (cheapest) for short voice-note replies in hi/ta/te/bn/mr/pa.
  Cache audio for repeated reminder phrases.
- STT: Scribe v3 for parent voice notes.
- Red alert: outbound call to the child (ElevenLabs agent + Twilio integration, or plain TTS
  audio + Twilio call as fallback). Script: "Papa's BP reading was 190/122 twice in ten minutes.
  Please call him now."
- Voice cloning of the child's voice: NOT in scope (needs paid plan + explicit consent).

## 6. Rules

### 6.1 Plausibility (reject or ask retake)
glucose 20–600 mg/dL (1.1–33.3 mmol/L; a decimal "mg/dL" or a large "mmol/L" value = unit
mix-up) · glucometer HI / LO stored at 600 / 20 with the flag · systolic 60–260 · diastolic
30–160 · systolic > diastolic · pulse 30–200 · flag if deviates hugely from the parent's own
recent history. (Ranges widened from 70–250 / 40–150 in H4.)

### 6.2 Vision output schema
```json
{
  "device_type": "bp | glucose | unknown",
  "systolic": 0, "diastolic": 0, "pulse": 0,
  "glucose_value": 0, "glucose_unit": "mg/dL | mmol/L | null",
  "display": "HI | LO | null",
  "legible": true,
  "issues": ["glare", "angle", "partial", "blur"]
}
```
Nulls for missing fields. Extra fields are forbidden and there is no free-text field. A strip
photo returns only `{"medicine_id": <an offered id> | "unknown", "legible", "issues"}`: the
reader picks from the parent's medicines and the curated brand list, never returns OCR text. `legible=false` or any failed rule → ask for a retake with tips
(hold phone straight, avoid light reflection, fill frame with the screen).

### 6.3 Alert thresholds (defaults — editable, labelled "set by your doctor")

| Level | Trigger | Basis |
|---|---|---|
| Red | Glucose < 54 mg/dL (**fixed floor**: a doctor-set value can only be stricter) | ADA Standards of Care 2026, level 2 |
| Amber + recheck | Glucose 54–69 mg/dL (fixed floor 70); parent asked to recheck in 15 min | ADA level 1 |
| Amber | Low glucose and **no confirmed recheck within 15 min** | rule |
| Red | Any low glucose + parent answers yes to "did you faint, feel confused, or did someone have to help you?" | ADA level 3 (needs assistance) |
| Amber | Glucose > 250 mg/dL | **illustrative, clinician-set** (no authoritative source retrieved) |
| — (question first) | Confirmed BP ≥ 180/120: one yes/no warning-signs question (chest pain, breathlessness, one-sided weakness/numbness or slurred speech, severe headache, confusion, sudden vision problems) before anything else | 2025 AHA/ACC |
| Red | Warning signs: yes → family alert + call; parent told to call 108/112 | 2025 AHA/ACC (hypertensive emergency signs) |
| — (no alert) | Warning signs: no, or no reply in 5 min (audited) → rest 5 min and repeat | rule |
| Red ("urgent: contact her doctor today") | Confirmed repeat 5–60 min later still ≥ 180/120, question asked again, no signs / no reply | 2025 AHA/ACC severe hypertension without emergency signs |
| Amber | First BP ≥ 180/120 and **no confirmed repeat within 15 min** ("high reading, no repeat taken") | rule |
| Amber | 3+ BP readings above the limit (140/90) in a week | rule |
| Amber | 2+ missed doses in a day | rule |

Low-sugar guidance to the parent is only the doctor's own plan, entered at onboarding or in
Settings ("Your doctor's plan: …"); without it: "Please follow your doctor's plan for low
sugar." Every family alert says "above/below the limit her doctor set" (pronoun from
onboarding, default "their"). The weekly summary also describes the home BP average against
the 135/85 home reference (2025 AHA/ACC), descriptively.

Family alert texts describe numbers against limits only (no parent instructions such as
"please rest"); parent wording lives in the chat templates. Consent paused/revoked (§6.5):
no logging, alerts or reminders until the parent agrees again.

Why the repeat rule: a home BP telemonitoring study (JMIR 2025) found ~57% of readings
triggered alerts; raising systolic threshold 170→180 cut alerts by 54%. 108/112 is said to the
parent when warning signs are reported (and as a safety net in the repeat messages); the
family text for an asymptomatic severe repeat uses "contact her doctor today", not ambulance
wording.

### 6.4 Bot scope and language
- Only readings, medicines, reminders, refills, summaries. Off-topic → fixed refusal.
- Short messages. Number-choice replies (1/2). Templates in English + Hindi first; Tamil stretch.
- Never interpret medically. Allowed: "3 of 7 morning readings were above the limit your doctor set."

### 6.5 Consent onboarding (parent-first)
Child invites parent → parent receives a short notice (text/voice, own language) → parent replies
"Haan/Yes" → parent picks what family can see → parent can say "STOP"/pause anytime.
Store `consent_status`, `consent_at`, `consent_text_version`.

## 7. Data model (SQLite now, DynamoDB later)

- `families(id, name)`
- `members(id, family_id, role[parent|child], name, channel, channel_ref, language, consent_status, consent_at)`
- `readings(id, parent_id, type[bp|glucose], systolic, diastolic, pulse, glucose_mgdl, context, status[unconfirmed|confirmed|rejected|expired], source[photo|voice|manual], confidence, image_ref, created_at, confirmed_at)`
- `medicines(id, parent_id, brand, generic, strength, times[], stock_count, daily_dose, is_schedule_x)`
- `dose_events(id, medicine_id, due_at, status[pending|taken|missed], responded_at)`
- `thresholds(parent_id, rule, value, set_by[default|doctor])`
- `alerts(id, parent_id, level[amber|red], reason, reading_id, notified_member_ids, acknowledged_by, acknowledged_at, created_at)`
- `refills(id, medicine_id, qty, status[pending_approval|approved|rejected|sent_to_pharmacy|fulfilled], requested_by[agent], decided_by_member_id, created_at, decided_at)`
- `conversation_state(member_id, state, payload, updated_at)`
- `audit_log(id, actor[agent|member|system], tool, args_json, result_json, created_at)`

## 8. Repo structure (target)

```
khayal/
  CLAUDE.md  PROGRESS.md  README.md  .env.example  .gitignore
  .claude/settings.json
  docs/PROJECT_BRIEF.md  docs/architecture.png (export of the diagram)
  backend/
    pyproject.toml
    app/
      main.py                # FastAPI: webhooks, simulator, dashboard API
      config.py              # env + APP_MODE
      channels/              # base.py, simulator.py, telegram.py, twilio_whatsapp.py
      reader/                # schema.py, rules.py, mock_reader.py, groq_reader.py, bedrock_reader.py, medicines.py
      conversation/          # state machine (read-back, repeat BP, dose replies)
      agent/                 # rule_agent.py, llm_agent.py, mcp_client.py
      alerts/                # thresholds.py, notifier.py
      voice/                 # elevenlabs.py (tts, stt, call)
      scheduler/             # jobs.py
      storage/               # base.py, sqlite_store.py, dynamo_store.py (later)
      api/                   # dashboard routes
    mcp_server/
      server.py              # FastMCP tools (no approve_refill)
    tests/
  dashboard/                 # React + Vite + TS + Recharts
  samples/                   # meter photos + labels.json (ground truth)
  scripts/                   # seed_demo.py, accuracy_test.py
```

## 9. Build phases (one branch each; merge to main when acceptance passes)

Priority for 24 Nov: Phases 0–4 are required for the submission. Phase 5 partly
(TTS voice reply is enough). Phase 6 is for hack day.

**Phase 0 — setup** (`phase-0-setup`)
Repo skeleton (§8), `.gitignore`, `.env.example`, README skeleton (problem, solution,
architecture, "Run in 2 minutes"), `PROGRESS.md`, backend `pyproject`, dashboard Vite scaffold,
pytest + ruff configured, `.claude/settings.json` committed.
Accept: `pytest` runs (even with 1 smoke test); dashboard dev server starts; pushed; merged.

**Phase 1 — MCP core + storage** (`phase-1-mcp-core`)
SQLite store + models (§7), FastMCP server with tools (§5.5), audit logging, threshold
evaluation (§6.3), seed script.
Accept: tests for each tool, refill cannot be approved via MCP, thresholds unit-tested;
a tiny script lists tools and calls `log_reading` through an MCP client.

**Phase 2 — reader + read-back** (`phase-2-reader`)
Vision schema, rules (§6.1), MockReader + Groq reader (Bedrock stub), conversation state
machine (§5.3) incl. repeat-BP flow, medicine strip matching with a small curated CSV.
Accept: tests for rules + state machine; mock reader returns labels from `samples/labels.json`;
with a Groq key a real photo returns valid JSON.

**Phase 3 — channels + agent** (`phase-3-channels-agent`)
Channel interface; web simulator page (parent chat + child notifications side by side);
rule-based agent calling MCP tools via MCP client; Telegram adapter; Twilio WhatsApp
webhook adapter; LLM agent behind a flag.
Accept: in mock mode, golden path works end to end in the simulator:
photo → read-back → "1" → logged → (high value) repeat → red alert shown in child pane.

**Phase 4 — dashboard** (`phase-4-dashboard`)
Dashboard API (§5.6) incl. `approve_refill` endpoint; React dashboard with charts, alerts,
pending refills (Approve/Reject), thresholds, audit log; seeded 14-day data.
Accept: approve in dashboard → refill status changes → parent/child get status message
in simulator; README screenshots/GIF added.

**Phase 5 — voice + reminders** (`phase-5-voice-alerts`)
Scheduler (reminders, missed doses, low stock → request_refill, weekly summary);
ElevenLabs TTS voice-note replies + Scribe for voice notes; red-alert call (stretch).
Accept: works with keys; gracefully disabled without keys.

**Phase 6 — AWS + polish** (`phase-6-aws-polish`)
DynamoDB store, Bedrock reader/agent, deploy (Lambda/API Gateway or simplest viable),
Amplify for dashboard, CloudWatch trace screenshot; `scripts/accuracy_test.py` on 30 real photos
→ publish accuracy in README.
Accept: deployed demo URL; accuracy number in README. Ask the user before creating any paid resource.

## 10. Configuration (`.env.example`)

```
APP_MODE=mock                 # mock | dev | aws
CHANNEL=simulator             # simulator | telegram | twilio
LLM_PROVIDER=mock             # mock | groq | bedrock
STORAGE=sqlite                # sqlite | dynamodb
VOICE=none                    # none | elevenlabs
DATABASE_URL=sqlite:///./khayal.db
MCP_SERVER_URL=http://localhost:8001/mcp
GROQ_API_KEY=
GROQ_VISION_MODEL=            # check console.groq.com/docs/models (e.g. a Llama 4 Scout / Qwen vision model)
GROQ_AGENT_MODEL=             # a Groq model that supports tool calling
TELEGRAM_BOT_TOKEN=
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_WHATSAPP_FROM=whatsapp:+14155238886
ELEVENLABS_API_KEY=
ELEVENLABS_VOICE_ID=
AWS_REGION=ap-south-1
BEDROCK_MODEL_ID=
DASHBOARD_DEMO_TOKEN=change-me
TZ=Asia/Kolkata
```

## 11. Costs and free tiers (as researched 22 Nov 2026)

- **ElevenLabs:** Free plan ≈ 10,000 credits/month (~10 min of TTS), no instant voice cloning,
  no commercial licence, attribution required for published output (third-party source, Oct 2026).
  Paid Starter ₹528/month (first month ₹88 offer if still running). API: v4 Turbo/Flash TTS
  ₹3.52 per 1k chars; Scribe STT ₹17.60/hour; agent calls ₹7.04/min. → Free plan is enough for
  the demo if replies are short and audio is cached. Phone calls also need a Twilio account.
- **AWS:** new accounts: $100 credits at sign-up + up to $100 more (6 months). The **Free plan
  limits which services you can use** (Bedrock etc. may need the Paid plan); Paid plan new
  customers also get credits. A card is needed at sign-up. Set a billing alarm. Ask organisers
  for sponsor credits. Bedrock model availability in ap-south-1 is unverified.
- **Twilio:** WhatsApp Sandbox free for testing (trial credit; ~100 messages noted in research).
  Calling Indian numbers from a trial account: unverified (trial usually calls verified numbers only).
- **Groq API (dev LLM provider):** free plan with rate limits (no card needed); OpenAI-compatible
  chat API with image input, JSON mode / structured outputs and tool calling. Vision models seen
  in 2026: `meta-llama/llama-4-scout-17b-16e-instruct`, `meta-llama/llama-4-maverick-17b-128e-instruct`,
  `qwen/qwen3.6-27b` (preview, Aug 2026). Each image ≈ up to ~1k input tokens; max 5 images/request.
  Model lists change often — **check console.groq.com/docs/models before coding** and keep model IDs in `.env`.
  Groq can also act as a remote MCP client (useful later, not needed for MVP).
- **Telegram Bot API:** free.
- WhatsApp production pricing (India): utility ≈ ₹0.115/message; from 1 Oct 2026 service replies
  billed after 1,000 free/number/month.
- Est. variable cost per family per month: ₹25–50 **(est.)** → ~75–85% gross margin at ₹199.

## 12. Demo script and README requirements

Golden-path demo (simulator or Telegram/WhatsApp):
1. Amma sends BP photo → "I read 186/121, pulse 80. Correct? 1 yes / 2 no" → "1".
2. "Please rest 5 minutes and check again." (demo: shortcut button to skip the wait)
3. Repeat photo 188/122 → confirmed → red alert: child pane gets WhatsApp-style alert
   (+ ElevenLabs voice/call if enabled) → dashboard shows the spike.
4. Strip photo → "Telma 40 = Telmisartan 40 mg. Remind at 8 PM? 1/2" → reminder created.
5. Low stock → pending refill on dashboard → child taps Approve → parent gets
   "Refill approved; your pharmacy will confirm."
6. Dashboard audit log: every MCP tool call the AI made; no approve call by the AI.

README must have: one-line pitch, problem stats (§2), architecture image, MCP tool table with
guardrails, "Run in 2 minutes" (mock mode, no keys), how to enable real providers, safety
& privacy section (§3 decisions, intended-use line), screenshots/GIF, roadmap, team.

## 13. Competitors and novelty (for README / slide 6)

- Emoha (₹1,999–10,999/mo, own app, human services), Samarth (own app, verify period),
  BeatO (own connected glucometer), Medisafe (family missed-dose alerts; fully paid outside US
  since 1 Jan 2026, $4.99/mo), 2care.ai (WhatsApp chronic care, vitals + family alerts,
  cancer focus; no meter-photo reading described), NanaGPT (Devpost; WhatsApp elder companion,
  typed readings, no family alerts), Sweet (Cal Hacks 12.0 Best MCP Automation; needs Dexcom,
  no family loop), GlucoRx Vision (2020 glucometer photo reading).
- Honest USP = the combination: any ordinary meter read from a photo + read-back, reminders
  from a medicine-strip photo, tiered family alerts, family-approved refills off WhatsApp,
  Indian-language voice, one permissioned MCP tool layer shared by a chat agent and a voice agent.
- Don't claim "only Khayal" for refill approval until 1mg / PharmEasy / Apollo are checked.

## 14. Open risks to verify

Bedrock models in ap-south-1 · AWS Free-plan service limits · Twilio trial calling Indian
numbers · Twilio media URL auth · current Groq free-plan rate limits and vision model IDs · ElevenLabs free-plan
credits on the official page · FastMCP 4.x exact API · Samarth pricing period.
