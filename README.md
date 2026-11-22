# Care Circle

**A WhatsApp photo of any ordinary glucometer or BP monitor becomes a confirmed, logged reading
and a family that knows. No new app, no new device.**

Built for wHACKiest 2026, Track 1, "Make MCP Matter: Less Magic, More Utility".

> Care Circle is a medication-adherence and self-recorded-readings logbook with family
> notifications. It does not diagnose, treat or recommend dosing. Alert limits are set by the
> parent's doctor. Not an emergency service: call 108/112.

## Problem

Elderly parents in India take readings at home on ordinary BP monitors and glucometers, but the
numbers stay on the device screen. Family members living elsewhere find out late or not at all,
and medicine refills run out unnoticed. Existing apps expect the parent to install something new
or buy a connected device.

_(Evidence and figures to be added.)_

## Solution

1. **Photo vitals.** The parent sends a photo of the meter screen. The reader extracts BP or
   glucose and reads it back ("I read 142/88. Correct? Reply 1 yes / 2 no"). Nothing is logged
   until the parent confirms.
2. **Tiered family alerts** (amber/red) against doctor-set limits, with a repeat-reading rule for BP.
3. **Medicine-strip photo** → medicine identified → reminder created (after confirmation).
4. **Family-approved refill requests.** The AI can only *request* a refill. Approval happens on
   the family dashboard, and orders go to a licensed pharmacy outside WhatsApp.
5. **Voice** notes in and out in Indian languages, and a weekly descriptive summary.
6. **Family dashboard** with trends, alerts, pending refill approvals, thresholds and consent settings.

**Why MCP:** every AI action is a named, permissioned MCP tool call. There are three human
checkpoints: the parent confirms the reading, the doctor sets the limits and the family approves
refills. The model can log, remind and request. It cannot approve, prescribe or diagnose, because
the approve tool does not exist on its side.

## Architecture

_Diagram to be added (`docs/architecture.png`)._

- `backend/app/`: FastAPI app (channel webhooks, web simulator, dashboard API), the read-back
  conversation and a rule-based agent
- `backend/mcp_server/`: FastMCP server with the Care Circle tools, run as its own process; the
  agent calls it through an MCP client over streamable HTTP, with the parent's identity in `_meta`
- `dashboard/`: React + Vite + TypeScript family dashboard
- Swappable adapters chosen in `.env`: channel (simulator / telegram / twilio), LLM
  (mock / groq / bedrock), storage (sqlite / dynamodb), voice (none / elevenlabs)

## Run in 2 minutes

Mock mode needs **no API keys**.

Prerequisites: Python 3.11+, [uv](https://docs.astral.sh/uv/) (`pip install uv`), Node 20+.

```bash
git clone https://github.com/jagpreetsingh02/care-circle.git
cd care-circle
cp .env.example .env            # defaults to APP_MODE=mock

# Backend
cd backend
uv sync
uv run pytest                   # tests
uv run python ../scripts/start.py   # MCP server (:8001) + API (:8000) together
```

Open **http://localhost:8000/simulator** and try the golden path (all mock, no keys):

1. Type `hello` → consent notice → tap **1**.
2. 🖼️ → `bp high` → "I read 186/122 — correct?" → **1** → asked to rest 5 minutes and repeat
   (the family is *not* alerted on a first severe reading).
3. **⏩ Skip 5 minutes** → 🖼️ `bp high` again → **1** → **RED alert** in both children's phones.
4. 🖼️ `strip telma40` → "Telma 40 = Telmisartan 40 mg. Remind at 8 PM?" → **1** → reminder set.
5. Type `what's the weather?` → "I can only help with readings and medicines."

Open **What the AI did** to watch each MCP tool call (`alert_family`, `create_reminder`) arrive
from the audit log. Variant: after step 2 press Skip three times instead: with no repeat in
15 minutes the children get an AMBER "no repeat taken" alert. **Reset demo** starts over.
With `LLM_PROVIDER=groq` and `GROQ_API_KEY` in `.env`, 📎 uploads a real photo to the vision model.

```bash
# more (from backend/)
uv run python ../scripts/mcp_client_demo.py        # list MCP tools, call log_reading (server running)
uv run python ../scripts/chat_demo.py --demo       # the same conversation in the terminal
uv run python ../scripts/accuracy_test.py          # reader accuracy on samples/ (--reader groq for real)
uv run python ../scripts/seed_demo.py              # 14 days of history (stop the servers first)

# Dashboard (new terminal)
cd dashboard
npm install
npm run dev                     # http://localhost:5173
```

### Real channels (optional)

- **Telegram:** `CHANNEL=telegram` and `TELEGRAM_BOT_TOKEN` (from @BotFather). The API long-polls,
  so no public URL is needed. Send `/id` to your bot, then register yourself:
  `uv run python ../scripts/add_member.py --channel telegram --ref <id> --role parent --name "Me"`.
  Alerts for your parent account still show in the simulator's family panes.
- **WhatsApp (Twilio Sandbox):** `CHANNEL=twilio`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`,
  `PUBLIC_BASE_URL` (e.g. an ngrok https URL) and the Sandbox webhook set to
  `<PUBLIC_BASE_URL>/webhooks/twilio`. Requests without a valid `X-Twilio-Signature` are
  refused. Messages are sent at most one per 3 seconds; alerts to numbers outside the 24-hour
  window need `TWILIO_TEMPLATE_CONTENT_SID` (a Sandbox template). Register numbers with
  `add_member.py --channel twilio --ref +91...`.

## Roadmap

- [x] Phase 0: repo setup, scaffolds, smoke tests
- [x] Phase 1: MCP core and storage (tools, thresholds, audit log)
- [x] Phase 2: photo reader and read-back confirmation
- [x] Phase 3: channels and agent, golden path in the web simulator
- [ ] Phase 4: family dashboard and refill approval
- [ ] Phase 5: voice replies and reminders
- [ ] Phase 6: AWS deployment, polish and measured reading accuracy
