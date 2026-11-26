# MCP tools and their guardrails

The full tool table, with annotations, output schemas and scopes. Summary in the
[README](../README.md#the-mcp-design). Security details: [SECURITY.md](SECURITY.md).

"Read vs act" is visible in `tools/list`: every tool has a human title and **all four
annotations set explicitly** (the MCP spec defaults are "destructive" and "open world", which
would be wrong for every tool here). R/O = `readOnlyHint`, Destr. = `destructiveHint`,
Idem. = `idempotentHint`, Open = `openWorldHint`.

| Tool (title) | R/O | Destr. | Idem. | Open | What the AI can do | Guardrails (server-side) |
|---|---|---|---|---|---|---|
| `log_reading` (Log a meter reading (unconfirmed)) | no | no | no | no | Store a BP or glucose reading | Stored as **unconfirmed** only; implausible values come back as a tool error that says what to do ("Systolic 420 is outside 60-260; ask the parent to retake the photo"); HI/LO and mg/dL vs mmol/L handled; parent = the signed token's `sub` (`_meta` only cross-checked), never from arguments |
| `get_history` (Read confirmed readings) | **yes** | no | yes | no | Confirmed readings, today / 7d / 14d / 30d | `response_format`: `concise` (default: count, latest, lowest, highest, average) or `detailed` (every reading); a parent reads only their own; a **family token** reads its own family's parent, only if readings are shared |
| `get_meds` (Read the medicine list) | **yes** | no | yes | no | Medicines, reminder times, stock | A parent: own medicines; a family token: only if the parent shares medicines |
| `create_reminder` (Set daily reminder times) | no | no | yes | no | Set daily reminder times, optional short note | Only for medicines the parent confirmed; `HH:MM` validated; the note (40 chars max) refuses links, phone numbers and instructions |
| `alert_family` (Alert the family (server-checked)) | no | no | yes | **yes** | Tell the family a limit was crossed | Takes only `level` and `reading_id`: **no recipient, no text**. Reading must be confirmed; server re-evaluates the doctor's limits and writes the message; cannot escalate amber→red; severe BP: warning-signs question first, then repeat; amber capped at 3/day (red never); one alert per (reading, rule); amber held back if the parent does not share alerts, but **red always reaches the family**; recipients only from the family registry |
| `request_refill` (Request a refill for family approval) | no | no | yes | no | Ask the family to approve a refill | Creates `pending_approval` only; capped at one month's supply; Schedule X refused; one pending per medicine; refused if the parent does not share medicines |
| `weekly_summary` (Weekly summary (descriptive)) | **yes** | no | yes | no | Descriptive 7-day summary | No recipient or text; home BP average described against 135/85; output filter blocks diagnosis and dosing words; for a family token, medicine and amber-alert lines follow the parent's sharing |
| ~~`approve_refill`~~ | | | | | **Does not exist** | Approval lives only in the dashboard API, triggered by a family member |

Every tool publishes an **output schema**; results carry `structuredContent` that matches it
plus a one-line text summary. Errors come back as `isError` results the model can act on.
Inputs are validated strictly; unknown arguments are refused. All tools refuse a parent whose
consent is pending, paused or revoked. The three read tools take one optional argument,
`parent_name`, for a family member whose family has more than one parent: it is matched only
against the registry (full name or first name), and an unknown or ambiguous name returns an
error listing the valid choices. Who may call what is set by **scopes** (see
[Scopes per role](#scopes-per-role)): a family token lists and calls only the three read tools. Every call,
including refusals, is written to `audit_log` and shown as "What the AI did" in the simulator and dashboard. The whole manifest
(names, descriptions, schemas, annotations) is pinned by hash (see [SECURITY.md](SECURITY.md#security)).

## Scopes per role

From [SECURITY.md](SECURITY.md#security):

- **Roles and scopes, enforced by the server.** Parent token: `readings:write`,
  `readings:read`, `meds:read`, `reminders:write`, `alerts:send`, `refills:request`. Family
  token: `readings:read`, `meds:read`, `summary:read`. Each tool declares the scope it needs
  (`backend/mcp_server/scopes.py`) and refuses a caller without it; scopes beyond the role are
  dropped whatever the token says; a tool without a declared scope is never listed or callable.
  `tools/list` shows only what the token may call: a family token never sees `log_reading`,
  `create_reminder`, `alert_family` or `request_refill`, and nobody sees an approve tool
  because none exists.
