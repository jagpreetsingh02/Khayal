# PROGRESS — Care Circle (read this first every session)

Deadline: **24 Nov 2026, 11:59 PM IST** — PPT + video + GitHub repo link on Devnovate.
Spec: `docs/PROJECT_BRIEF.md` (read only the sections you need). Rules: `CLAUDE.md`.

## Current status
- Current phase: **Phase 0 — setup** (not started)
- Current branch: `main`
- Next step: Phase 0 — create repo skeleton, README skeleton, configs, `.env.example`,
  backend + dashboard scaffolds, smoke test; push `phase-0-setup`; merge to `main`; tag `phase-0-done`.

## Phase checklist
- [ ] Phase 0 — setup (`phase-0-setup`)
- [ ] Phase 1 — MCP core + storage (`phase-1-mcp-core`)
- [ ] Phase 2 — reader + read-back (`phase-2-reader`)
- [ ] Phase 3 — channels + agent, golden path in simulator (`phase-3-channels-agent`)
- [ ] Phase 4 — dashboard + approve refill (`phase-4-dashboard`)
- [ ] Phase 5 — voice + reminders (`phase-5-voice-alerts`)
- [ ] Phase 6 — AWS + polish + accuracy test (`phase-6-aws-polish`)

## Blockers / needs from user
- (none yet)

## Keys / accounts available
- Groq: no · Telegram bot: no · Twilio: no · ElevenLabs: no · AWS: no

## Decisions log
- 2026-11-22: Mock mode first (no keys needed); SQLite locally, DynamoDB later.
- 2026-11-22: No `approve_refill` MCP tool; approval only via dashboard API.
- 2026-11-22: No AI attribution in commits/PRs.
- 2026-11-22: Dev LLM provider = Groq (vision + agent), not Gemini. Bedrock stays the AWS target.

## Session log (newest first, one line each)
- 2026-11-22: Kit created (CLAUDE.md, brief, progress). Build not started.
