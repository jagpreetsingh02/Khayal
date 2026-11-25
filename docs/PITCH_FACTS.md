# Pitch facts

Facts only, each with where it is proved. Paths are from the repo root; test names are in
`backend/tests/`. Checked 25 Nov 2026 at tag `pitch-ready`.

## MCP tools per role

| Role (token) | Scopes | Tools it lists and may call |
|---|---|---|
| Parent (the API's agent, acting for one parent) | `readings:write`, `readings:read`, `meds:read`, `reminders:write`, `alerts:send`, `refills:request` | `log_reading`, `get_history`, `get_meds`, `create_reminder`, `alert_family`, `request_refill`, `weekly_summary` (7) |
| Family member (read-only) | `readings:read`, `meds:read`, `summary:read` | `get_history`, `get_meds`, `weekly_summary` (3) |
| Anyone | none | No approve tool exists. Refill approval is only `POST /api/refills/{id}/approve`, after a family member's click |

- Table: `backend/mcp_server/scopes.py`. Pinned per role: `backend/mcp_server/tool_manifest.sha256`.
- Proved by `test_tools_list_per_role_over_http_matches_the_pin`,
  `test_every_registered_tool_declares_a_scope`,
  `test_a_family_token_is_refused_by_every_parent_only_tool`,
  `test_no_approve_tool_and_request_refill_only_creates_pending`,
  `test_mcp_tools_endpoint_shows_no_approve_tool`.

## Tests

- **671 pass, 2 skipped** (the 2 live API tests run only with `RUN_LIVE_TESTS=1`), in 28 test
  files: `cd backend && uv run pytest`. No test calls a real API: `tests/conftest.py` forces
  mock providers and fails any non-local network connection (`test_network_guard.py`).
- Red-team suite: 121 tests in `test_redteam.py`, mapped to incident classes in the README
  section "Threats we test for".
- CI on every push: ruff, ruff format, mypy, tests on Python 3.11 and 3.13, dashboard lint and
  build, dependency audit (`.github/workflows/ci.yml`).

## Rules

### The parent confirms every reading
- A reading is stored `unconfirmed` and read back ("I read 132/84, pulse 74 — correct? Reply 1
  yes / 2 no"); only the parent's "1" confirms it, and no alert fires before that.
  `test_log_reading_stores_unconfirmed_with_read_back`, `test_reply_1_confirms_server_side`,
  `test_no_alert_before_parent_confirms`, `test_high_reading_alerts_nobody_until_confirmed`.

### Severe BP (default limit 180/120, set per parent by the doctor)
| Situation | Result | Test |
|---|---|---|
| First confirmed severe reading | Warning-signs question first (chest pain, breathlessness, one-sided weakness or slurred speech, severe headache, confusion, sudden vision problems); nobody is alerted yet | `test_first_severe_bp_asks_the_warning_signs_question_first` |
| Answer 1 (yes) | RED to the family at once; the parent is told to call 108/112 | `test_yes_to_warning_signs_is_red_at_once_and_says_108` |
| Answer 2 (no) | Rest 5 minutes and repeat | `test_no_keeps_the_rest_and_repeat_flow` |
| Repeat 5-60 minutes later still severe, no warning signs | RED "Urgent: contact her doctor today" | `test_asymptomatic_repeat_alerts_family_with_doctor_today_wording` |
| Answered no, no repeat within 15 minutes | AMBER | `test_answered_no_and_no_repeat_is_still_amber` |
| No answer and no repeat within 15 minutes (silent parent) | RED `bp_severe_no_reply` ("Please call her now") | `test_no_reply_and_no_repeat_is_red_at_15_minutes_and_not_before`, `test_red_no_reply_alert_takes_the_normal_red_path` |

Code: `backend/app/alerts/thresholds.py`, `backend/app/conversation/engine.py`. Source of the
rules: 2025 AHA/ACC blood-pressure guideline (README "Clinical rules").

### Glucose (ADA Standards of Care 2026)
| Situation | Result | Test |
|---|---|---|
| Below 54 mg/dL (fixed floor) | RED to the family | `test_red_low_glucose_also_asks_the_question`, `test_meter_lo_is_red_and_hi_is_amber` |
| 54-69 mg/dL (fixed floor 70) | AMBER and a recheck request in 15 minutes | `test_low_glucose_sends_the_doctors_plan_and_asks_for_a_recheck` |
| No recheck within 15 minutes | AMBER to the family | `test_no_recheck_in_15_minutes_alerts_the_family` |
| Fainted, confused or needed help | RED whatever the number | `test_fainting_or_needing_help_is_red_whatever_the_number` |
| A setting tries to loosen 54 / 70 | Refused (only stricter limits are kept) | `test_glucose_floors_cannot_be_loosened_by_a_setting` |

### Alerts, medicines, refills
- Amber: at most 3 per parent per 24 hours. Red is never capped
  (`test_amber_is_rate_capped_but_red_is_not`; `backend/mcp_server/guardrails.py`).
- Red always reaches the family, even when the parent does not share alerts
  (`test_red_always_reaches_the_family_even_when_alerts_not_shared`).
- Red alert messages are retried until delivered; other messages stop after 8 attempts
  (`test_red_alert_messages_never_give_up`,
  `test_non_red_message_stops_after_8_attempts_and_is_audited`).
- 2 missed doses in a day: AMBER, at most once a day (`test_missed_doses_alert_without_reading`).
- Refill requests: `pending_approval` only, at most 30 days' supply, Schedule X refused
  (`test_refill_quantity_cap`, `test_refill_schedule_x_excluded`).
- Every tool refuses a parent whose consent is pending, paused or revoked
  (`test_every_tool_refuses_a_parent_without_consent`).
- Off-topic messages get "I can only help with readings and medicines."
  (`test_off_topic_gets_the_fixed_refusal`).

### MCP identity and access
- Every MCP call carries a 60-second HS256 token (`sub` = member, role, scopes); the caller is
  the token's `sub`, and `_meta` naming anyone else is refused and audited
  (`backend/mcp_server/tokens.py`, `identity.py`; `test_bad_tokens_get_401_before_any_tool`,
  `test_meta_that_disagrees_with_the_token_is_refused_and_audited`).
- A family member reads only their own family's parent, only what that parent shares, and each
  read is audited "Viewed by <member>" (`backend/app/sharing.py`;
  `test_a_family_token_reads_its_own_parent_and_never_the_other_family`,
  `test_family_reads_follow_the_parents_sharing`,
  `test_each_family_read_is_audited_as_viewed_by_the_member`).

## Reading accuracy

- 14 of 16 **AI-generated** test images read with every field exact (BP 11/13, glucometer
  3/3), Groq reader, 23 Nov 2026 (README "Reading accuracy"). Not real photos of Indian home
  devices; no real-photo accuracy number exists yet. The real-photo harness is built
  (`scripts/accuracy_test.py --import`).

## Built, demo-only, planned

| Capability | Status |
|---|---|
| Read-back flow, clinical rules, alerts, reminders, missed doses, low-stock refill requests, weekly summary | Built |
| MCP server with 7 tools, per-call tokens, role scopes, per-role manifest pins, audit log | Built |
| Family dashboard: trends, alerts, refill approval, limits, sharing and consent, audit | Built |
| Outbox retries, durable inbox with leases and safe replays, `/health` | Built |
| Docker Compose (two containers, one SQLite file) | Built (one machine only) |
| Telegram and WhatsApp (Twilio Sandbox) channels | Built, unit-tested; not run live |
| ElevenLabs voice notes (TTS / STT) | Built; checked live once (23 Nov 2026) |
| Red-alert phone call via Twilio | Built; not run live |
| Web simulator, demo clock, demo token sign-in, "Simulate low stock", demo pharmacy | Demo-only (mock mode) |
| Family gateway (`scripts/family_gateway.py`) | Demo-only local bridge |
| Rule-based agent | Built; an LLM tool-calling agent is planned |
| Bedrock reader, DynamoDB store | Stubs only |
| AWS deployment (`docs/AWS_ARCHITECTURE.md`) | Design only, nothing deployed |
| Postgres / DynamoDB adapter, schema migrations, JSON logging, DPDP export / delete | Planned |
| Family MCP access via OAuth 2.1 with per-person consent | Planned |

Source: README "Production readiness".

## Known limits

- Reading accuracy is not measured on real photos.
- SQLite on one machine; not for several hosts.
- MCP tokens use HS256 shared by our own two processes; no refresh or revocation beyond the
  60-second expiry; `jti` is not tracked, so a token can be reused within its minute.
- The family gateway trusts whoever holds the local `.env`; it is a localhost demo bridge.
- Dashboard rate limits are in memory in one process; the session token is in localStorage;
  no TLS in the app; no external security review or penetration test.
- Message handling is at least once, not exactly once: after a crash the reply to the parent
  can be sent twice (README "Reliability").
- Not audited for SPDI / DPDP compliance; DPDP export / delete is not built.
- Windows commands in the README have not been run on Windows.
- Simulator "Skip 5 minutes" moves the parent's recent data back in time, so alerts sent
  after a skip show the earlier time (demo only).
