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

- `backend/app/`: FastAPI app (channel webhooks, web simulator, dashboard API)
- `backend/mcp_server/`: FastMCP server with the Care Circle tools
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
uv run python ../scripts/seed_demo.py              # demo family + 14 days of readings
uv run uvicorn app.main:app --reload --port 8000   # http://localhost:8000/health
uv run python -m mcp_server.server                 # MCP server on http://localhost:8001/mcp
uv run python ../scripts/mcp_client_demo.py        # (new terminal) list MCP tools, call log_reading

# Dashboard (new terminal)
cd dashboard
npm install
npm run dev                     # http://localhost:5173
```

## Roadmap

- [x] Phase 0: repo setup, scaffolds, smoke tests
- [ ] Phase 1: MCP core and storage (tools, thresholds, audit log)
- [ ] Phase 2: photo reader and read-back confirmation
- [ ] Phase 3: channels and agent, golden path in the web simulator
- [ ] Phase 4: family dashboard and refill approval
- [ ] Phase 5: voice replies and reminders
- [ ] Phase 6: AWS deployment, polish and measured reading accuracy
