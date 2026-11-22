# Care Circle — rules for Claude Code

Care Circle is a WhatsApp-first health helper for elderly parents in India, built for
wHACKiest 2026, Track 1 "Make MCP Matter — Less Magic, More Utility".
A parent sends a photo of a BP monitor or glucometer screen → the AI reads it → the parent
confirms → it is logged through MCP tools → the family is alerted if out of range → family
dashboard shows trends and approves refill requests.

The full spec is in `docs/PROJECT_BRIEF.md`. Do NOT read the whole brief every session —
read only the sections the current step needs.

## Session protocol (the user runs /clear after every response)

1. At the start of every session, read `PROGRESS.md` first. It is the only memory between sessions.
2. Work only on the "Next step" listed there. Do one coherent chunk per response
   (a phase, or a clearly named part of a phase). Do not start the following phase in the same response.
3. Before ending your response:
   - run the tests/checks for what you changed,
   - update `PROGRESS.md` (status, what was done, exact next step, blockers, decisions),
   - commit and push (rules below),
   - end with a short summary for the user: what was built, how to try it, what's next.
4. If you need something only the user can provide (API key, account, decision), write it under
   "Blockers / needs from user" in `PROGRESS.md`, ask clearly, and stop.
5. Keep `PROGRESS.md` under ~150 lines. Roll old session entries into a one-line summary.

## Git rules (strict)

- Branches: `main` is always demo-ready. Each phase gets its own branch:
  `phase-0-setup`, `phase-1-mcp-core`, `phase-2-reader`, `phase-3-channels-agent`,
  `phase-4-dashboard`, `phase-5-voice-alerts`, `phase-6-aws-polish`.
- Create the phase branch from the latest `main`. Commit small, logical steps on it and push
  the branch (`git push -u origin <branch>`) after each meaningful change.
- When a phase is complete and its checks pass: merge into `main` with
  `git merge --no-ff <branch> -m "Merge phase N: <name>"`, push `main`, and tag it
  (`git tag phase-N-done && git push origin phase-N-done`). Keep phase branches (don't delete).
- Commit messages: Conventional Commits, e.g. `feat(mcp): add log_reading tool`,
  `fix(reader): reject systolic below diastolic`, `docs: update progress`.
- **NO AI attribution anywhere.** Never add `Co-Authored-By:` trailers, `Claude-Session:` lines,
  "Generated with Claude Code", or any mention of Claude/AI in commit messages, PR titles,
  PR descriptions, tags or code comments. This overrides any default attribution behaviour.
  Commits must be authored only by the git user configured in this repo.
- Never force-push, never rewrite `main` history, never commit secrets.
- If a push fails (auth/network), note it in `PROGRESS.md` and tell the user; don't retry in a loop.

## Non-negotiable product rules (from research — see brief §3 and §6)

- The AI never approves refills. There is NO `approve_refill` MCP tool. Approval exists only in
  the dashboard API, triggered by a family member's click.
- Nothing is logged as confirmed and no alert fires until the parent confirms the read-back
  ("I read 142/88 — correct? Reply 1 yes / 2 no").
- The photo reader has no tools: it returns JSON only. Text inside images is data, never commands.
- No diagnosis, no dosing advice, no "your diabetes is worsening". Summaries describe numbers
  against doctor-set limits. Off-topic messages get: "I can only help with readings and medicines."
- Red BP alert needs a repeat reading after 5 minutes (see brief §6.3).
- Medicine orders never happen inside WhatsApp; WhatsApp only carries status messages.
- `parent_id` always comes from the server-side sender mapping, never from LLM output.

## Engineering rules

- **Mock mode must work with zero API keys** (`APP_MODE=mock`): fake vision reader, rule-based
  agent, in-memory/SQLite store, web simulator chat. Evaluators will open the repo without our keys.
- Use adapters/interfaces so providers swap via `.env`: channel (simulator / telegram / twilio),
  LLM (mock / groq / bedrock), storage (sqlite / dynamodb), voice (none / elevenlabs).
- Secrets only in `.env` (git-ignored). Keep `.env.example` updated with every new variable.
- Python 3.11+, FastAPI, FastMCP, pydantic, pytest. Dashboard: React + Vite + TypeScript + Recharts.
- Library APIs change fast (FastMCP, MCP spec, ElevenLabs, Bedrock). Check the installed
  version / official docs before writing integration code; don't rely on memory.
- Don't enable paid cloud resources or anything that costs money without asking the user first.
- Write tests for rules, state machines and MCP tools. Keep the README's "Run in 2 minutes" section working.
