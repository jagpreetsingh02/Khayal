# Demo script (4 minutes, mock mode)

Click-by-click script for the demo video. Everything runs locally with **no API keys**. The
messages quoted below are what the app actually sends (checked on a fresh clone, 23 Nov 2026).

## Before you record (2 minutes, off camera)

1. Use mock mode so every take is identical: in `.env` set `LLM_PROVIDER=mock`, `VOICE=mock`,
   `CALL_PROVIDER=mock` (or just `cp .env.example .env`).
2. Start fresh demo data:
   ```bash
   cd backend
   uv run python ../scripts/start.py --seed
   ```
3. Open two browser windows side by side:
   - **Left:** http://localhost:8000/simulator (parent's chat in the middle, Ravi's and Priya's
     phones on the side, the live MCP tool log below).
   - **Right:** http://localhost:8000/dashboard/ → **Use the demo access token** → `change-me` → **Continue** →
     **Ravi Gowda**.
4. Turn your browser's sound on (voice notes and the call card are read aloud by the browser in
   mock mode).

A reminder may already be in Lakshmi's chat when you open the simulator (the scheduler sends it if a
medicine time has just passed). That is fine; leave it unanswered.

### Seed data (`scripts/seed_demo.py`)

| Item | Value |
|---|---|
| Family | Gowda family |
| Parent | **Lakshmi Gowda**, Indian English voice notes, consent granted, simulator number +919800000001 |
| Children | **Ravi Gowda** (+919800000002), **Priya Gowda** (+919800000003), English |
| Readings | 14 days of morning BP (~134/84, mild rise in week 2), some evening BP, fasting and post-meal glucose, one low fasting glucose of 64 five days ago (amber alert) |
| Doctor-set limits | BP limit 140/90, glucose amber above 250 (labelled "set by the doctor"); defaults: severe BP 180/120 needs a repeat, glucose red below 54, amber below 70, 2 missed doses a day = amber |
| Medicines | **Telma 40** (Telmisartan 40 mg) 08:00, stock 6 · Glycomet 500 (Metformin) 08:30 + 20:30, stock 44 · Ecosprin 75 (Aspirin) 14:00, stock 25 · Atorva 10 (Atorvastatin) 21:30, stock 18 |
| Sample photos (🖼️) | `bp normal` 132/84 · `bp high` 186/122 · `strip telma40` · `glucose mgdl` 112 · `glucose mmol` 6.2 · `bp blurry` (unreadable) |

## The script

Times are cumulative. "Say" is the voice-over.

### 0:00 Intro (15 s)

Show the simulator.

> **Say:** "Lakshmi lives alone and manages her BP and diabetes herself. Her children, Ravi and
> Priya, live in other cities. She already owns a BP monitor. Khayal turns a WhatsApp photo of its screen into a
> confirmed reading her family can see. On the left is Lakshmi's chat; on the side, her children's
> phones; below, every MCP tool call the AI makes."

### 0:15 Normal reading: photo → read-back → logged (30 s)

1. Simulator: click **🖼️** → **bp normal**.
   Lakshmi's chat: *"I read 132/84, pulse 74 — correct? Reply 1 yes / 2 no"*
2. Click the quick reply **1**.
   Chat: *"Saved: 132/84, pulse 74. Thank you."* The MCP log shows `log_reading`.

> **Say:** "The AI reads the photo, but nothing is saved as confirmed until Lakshmi says yes. That's
> checkpoint one: the parent. The vision call has no tools; text in a photo is only data."

### 0:45 High BP → warning-signs check → repeat → RED → family alert + call (55 s)

1. Click **🖼️** → **bp high** → *"…I read 186/122, pulse 92 — correct?"* → click **1**.
   Chat: *"Saved: 186/122, pulse 92. This is at or above the limit your doctor set. Do you have
   any of these right now: chest pain, breathlessness, weakness or numbness on one side or
   slurred speech, severe headache, confusion, or sudden vision problems? Reply 1 yes / 2 no"*.
   The children get nothing yet.
2. Click **2** (no warning signs). Chat: *"Thank you. Please sit and rest for 5 minutes, then
   measure again and send a photo of the new reading…"*

> **Say:** "Following the 2025 AHA guideline, the first thing it asks is about warning signs. A
> 'yes' alerts the family at once and tells her to call 108. She says no, and one very high
> reading can be a mistake, so it asks for a repeat after five minutes."

3. Click **⏩ Skip 5 minutes**.
4. Click **🖼️** → **bp high** → click **1** → the same question again → click **2**.
   - Lakshmi: *"…The repeat reading is still at or above the limit your doctor set. I have told
     your family. Please contact your doctor today. If any of those signs start, call 108/112."*
   - Ravi and Priya: *"RED alert for Lakshmi Gowda: Repeat BP 186/122 at … is still at or above
     the limit her doctor set (180/120). No warning signs were reported. Urgent: contact her
     doctor today."* plus an **Incoming call** card that reads the alert aloud.
   - (Shorter variant: answer **1** at the first question: RED at once, *"Please call 108 or 112
     now…"*.)
4. Dashboard (right): the red alert appears on **Overview** within a few seconds. Click
   **Trends** to show the spike above the doctor's band. Click **Alerts** → **I've seen this**.

> **Say:** "The repeat is still severe, so the family gets a red alert and a phone call: contact
> her doctor today. The limits come from Lakshmi's doctor, not from the AI, and the server
> re-checks them on every alert. That's checkpoint two."

### 1:35 Medicine strip → reminders → missed dose (45 s)

1. Click **🖼️** → **strip telma40**.
   Chat: *"Telma 40 = Telmisartan 40 mg. Remind at 8 PM? Reply 1 yes / 2 no"* → click **1**.
   Chat: *"Done. I will remind you to take Telma 40 at 8:00 AM and 8:00 PM every day."*
   (Telma 40 is already on her list from the morning, so the evening time is added.)

> **Say:** "A photo of the strip becomes the generic name and a reminder, again only after she
> says yes."

2. Click **⏰ Next reminder**. The demo clock jumps to the next medicine time; Lakshmi gets e.g.
   *"8:30 AM: time for Glycomet 500. Reply 1 when taken, 2 if skipped."* with an Indian English voice note
   (click ▶ to play it).
3. Don't reply. Click **⏩ 30 min**: one gentle follow-up, *"Did you take …? Reply 1 if you have
   taken it."*
4. Repeat **⏰ Next reminder** → **⏩ 30 min** until two reminders on the same day are unanswered.
   Ravi and Priya get *"AMBER alert for Lakshmi Gowda: 2 medicine reminders were marked missed on
   <date>."* (Usually one or two rounds. Amber never rings the phone.)

> **Say:** "Reminders come as voice notes in Indian English. If she misses two in a day, the family gets an
> amber alert, not an alarm."

### 2:20 Low stock → AI requests refill → family approves (50 s)

1. Dashboard: **Medicines** → on **Telma 40** click **Simulate low stock (demo)**.
   - The MCP log (simulator) shows `request_refill`.
   - Lakshmi: *"Your Telma 40 is running low (3 left). I have asked your family to approve a refill."*
   - Ravi and Priya: *"Refill request for Lakshmi Gowda: Telma 40 x30. Approve or reject it on the
     Khayal dashboard."*
2. Dashboard: **Overview** → *Refill request waiting for you* → **Approve**.
   - Lakshmi: *"Refill approved for Telma 40 (30); your pharmacy will confirm."*
   - Priya: *"Ravi Gowda approved the refill for Telma 40 x30. Sent to the demo partner pharmacy
     (demo, no real order)."*

> **Say:** "The AI noticed the stock was low and *requested* a refill. It cannot approve it:
> there is no approve tool on the MCP server at all. Approval only exists behind Ravi's click on
> the dashboard. That's checkpoint three. The order goes to a licensed pharmacy outside WhatsApp;
> here it's a labelled stub."

If **Simulate low stock** says a refill is already pending (the scheduler requests one by itself
when 5 or fewer days are left), go straight to **Approve**.

### 3:10 Parent turns off sharing (25 s)

1. Dashboard: **Sign out** → **Use the demo access token** → `change-me` → **Continue** → **Lakshmi Gowda** (*Parent: can
   change what is shared*).
2. **Limits & sharing** → under *Sharing and consent*, switch off **Medicines**.
3. **Sign out** → sign in as **Ravi Gowda** → **Medicines** now says *"Lakshmi Gowda has not
   shared medicines and refills with the family."*

> **Say:** "Lakshmi consents, not her children, and she decides what they see. Only red alerts
> always get through, for safety."

### 3:35 Audit log (25 s)

Dashboard (as Ravi): **What the AI did**.

- Tiles: **AI tool calls**, **Refused by guardrails**, **Approvals by the AI: 0**,
  **Approvals by family: 1**.
- *Tools the AI can call*: the seven tools listed live from the MCP server and
  **approve_refill: does not exist**.
- Click a row (e.g. `request_refill`) to show its arguments and result; filter **AI** / **People**.

> **Say:** "Every tool call the AI made, every refusal, and every family decision is logged.
> Zero approvals by the AI, because it can't. Less magic, more utility: Khayal."

**End at 4:00.**

## Optional extras (if you have time)

- Type `what's the weather?` in Lakshmi's chat: *"I can only help with readings and medicines."*
- **🗣️** → *“I took my medicine”* after a reminder: marked as taken, answered with a voice note.
- **📊 Weekly summary**: the descriptive 7-day summary goes to the children.
- **Reset demo**: a fresh family with consent *pending*; type `hello` to see onboarding.
- With `LLM_PROVIDER=groq` and a key, use **📎** to upload a real photo of a meter.
