# Reliability

How messages are delivered, stored and replayed. Summary in the
[README](../README.md#safety-security-and-reliability).

- **Family messages are retried, never silently dropped.** Every family-facing message
  (alerts, refill notices, missed-dose notices, weekly summary) goes through a store outbox.
  It is marked delivered only after the channel accepted it. Failed sends retry after 30 s,
  1 min, 2 min, 5 min, then every 10 min. Red alert messages retry until delivered; anything
  else stops after 8 attempts. Final failures and every red failure are written to the audit
  log (`delivery_failed`). The dashboard marks an alert "Not delivered yet" while it retries.
- **No duplicates.** Twilio `MessageSid` and Telegram `update_id` are unique in the inbox; a
  webhook retry or a poller restart is acknowledged but not stored or processed twice.
- **Durable inbox: persist, then answer.** Webhooks verify the signature, write the message
  to the `inbox` table (that insert is the duplicate check), and only then answer. If the
  write fails, the webhook fails and the channel sends the message again. The Telegram poller
  moves its offset past an update only after storing it. A worker claims due messages with a
  60 s lease (renewed while it works), processes them, and marks them done. Errors retry after
  5 s, 30 s, 2 min and 10 min; after 5 attempts the message is `failed`, written to the audit
  log (`inbound_failed`) and kept. On start, messages left `processing` by a dead process go
  back to pending once their lease runs out and are processed. A clean shutdown finishes
  what is due first. Delivery is **at least once, not exactly once**. (The web simulator
  answers inside its own request, so it does not need the inbox.)
- **Replays are safe.** A message can be handled twice if the process dies after it acted
  and before it was marked done. Before its first side effect the engine saves the parent's
  conversation state and consent on the inbox row and marks the state with the message id;
  a replay starts again from that saved point. Each side effect is safe to repeat:

| Side effect | Why a replay does not repeat it |
|---|---|
| New reading (photo or typed) | Its id is derived from the inbox id; a replay reuses the stored reading as first read |
| Confirm / reject a reading, consent, STOP / START | Same starting state, same answer, written with the message's own arrival time |
| Family alert (amber or red) and its call | `alert_family` sends one alert per (reading, rule), and a unique index on `alerts(reading_id, rule)` enforces it in the database: **a red alert is never created twice** |
| Dose answered ("1" after a reminder) | Doses this message already marked (`responded_at` = its arrival time) are not counted again, so stock is used once |
| Warning-signs answer | Stored on the reading; one audit entry per (reading, question) |
| Medicine strip confirmed | Found by name; reminder times are a set (`create_reminder` is idempotent) |

- **What a replay can still repeat** (not made idempotent): the **reply to the parent** can be
  sent twice (the process died after sending it, before marking the message done); a replayed
  photo is **read again** by the vision model and a voice note transcribed again (cost only;
  the first stored values win); MCP tool calls of the replay are **audited again** (e.g.
  `alert_family` answering "already sent"). A message that *failed with an error* and is
  retried after newer messages from the same parent were handled runs against the newer
  state; if its reading was already answered, it does nothing.
- **Privacy in the inbox.** The payload (phone number, message text) is blanked when the
  message is done; the row keeps only channel, ids, status and times. Photos and voice notes
  are fetched when the message is processed and never stored in the inbox. Failed messages
  keep their payload for inspection. Done and failed rows are deleted after
  `INBOX_RETENTION_DAYS` (default 7). Payloads are never logged.
- **`/health`** reports inbox counts and the age of the oldest pending message, and says
  `degraded` (still 200) when that is over 5 minutes.
- **Per-parent order, parents in parallel.** One parent's messages are handled strictly in
  arrival order (a message waiting for a retry is passed by newer ones); different parents
  run concurrently, so one slow photo read does not hold up anyone else.
- **Docker:** the `api` and `mcp` containers share one SQLite file (WAL mode) on a named volume.
  That is fine for a demo on one machine; it is not a database for several hosts or replicas.
  `/health` returns `ok`, `degraded` when the MCP server is down (still 200: the dashboard
  works, readings cannot be logged) or `error` with 503 when the database does not answer.
