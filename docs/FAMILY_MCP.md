# Family MCP access (read-only)

A family member can read a parent's numbers from their own MCP client (MCP Inspector, Claude
Desktop): confirmed readings (`get_history`), the medicine list (`get_meds`) and the weekly
summary (`weekly_summary`). Only what the parent shares is visible, under the same sharing and
consent rules as the family dashboard. Every read appears in the parent's audit panel as
"Viewed by <member>". A family member cannot log, remind, alert or request a refill. Nobody
can approve one either, because there is no approve tool.

> **Demo bridge: localhost only.** `scripts/family_gateway.py` mints family tokens with the
> `MCP_TOKEN_SECRET` in your local `.env`. Whoever holds that `.env` can mint a token for
> **any** member. The gateway therefore speaks stdio only (it opens no port), refuses an
> `MCP_SERVER_URL` that is not on this machine, and refuses `APP_MODE=aws`. Never expose it
> beyond localhost, and never share the `.env`.
>
> **Production version:** OAuth 2.1 with per-person consent, where each family member signs
> in and the parent approves who may read what. It is planned and not built.

## How it works

```
MCP client (Inspector / Claude Desktop)
   │ stdio
   ▼
scripts/family_gateway.py ── 3 read-only tools, no others
   │ streamable HTTP, a fresh 60 s family token per call
   │ (sub = FAMILY_GATEWAY_MEMBER_ID, role = family,
   │  scope = readings:read meds:read summary:read)
   ▼
Khayal MCP server (localhost:8001)
   checks the token → caller = sub → family scopes only →
   that member's own family → the parent's sharing + consent →
   "viewed by" audit entry → data
```

The gateway decides nothing. The Khayal MCP server checks everything: the token, the scopes,
the family, the sharing, the consent, and the registry match of `parent_name`.

## Set up (once)

1. Put a token secret in `.env` (repo root or `backend/`), so the Khayal servers and the
   gateway share it. `scripts/start.py` uses it instead of a random one per run:

   ```sh
   python3 -c 'import secrets; print("MCP_TOKEN_SECRET=" + secrets.token_urlsafe(32))' >> .env
   ```

2. Start Khayal and find your member id:

   ```sh
   cd backend
   uv run python ../scripts/start.py --seed
   uv run python ../scripts/family_gateway.py --who        # the demo family
   # Gowda family:
   #   mem_…  Lakshmi Gowda (parent)
   #   mem_…  Ravi Gowda (family member: use this id)
   ```

   For a family other than the demo one, pass `--who CHANNEL:NUMBER` (e.g.
   `--who telegram:123456789`).

3. Add `FAMILY_GATEWAY_MEMBER_ID=mem_…` (a family member's id, not the parent's) to `.env`.

## MCP Inspector

Run this with Khayal running (Node 22+; use a current Inspector release):

```sh
npx @modelcontextprotocol/inspector uv --directory /ABSOLUTE/PATH/TO/Khayal/backend run python ../scripts/family_gateway.py
```

In the Inspector, **Connect** and then **Tools → List Tools** should show exactly
`get_history`, `get_meds` and `weekly_summary`. Call `get_history` with `range` = `30d`, then
open the family dashboard's **Audit** page as the parent: the entry reads "Viewed by Ravi
Gowda".

## Claude Desktop

Settings → Developer → Edit Config opens `claude_desktop_config.json`
(macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`, Windows:
`%APPDATA%\Claude\claude_desktop_config.json`). Add:

```json
{
  "mcpServers": {
    "khayal-family": {
      "command": "/ABSOLUTE/PATH/TO/uv",
      "args": [
        "--directory",
        "/ABSOLUTE/PATH/TO/Khayal/backend",
        "run",
        "python",
        "../scripts/family_gateway.py"
      ]
    }
  }
}
```

`which uv` prints the path to use for `command`. Apps started from the Dock may not see your
shell's `PATH`, so an absolute path is safer. Restart Claude Desktop with Khayal running, then
ask, for example, "How was my mother's BP this week?"

## Several parents in one family

Each read tool takes an optional `parent_name`. It is matched only against your family's
registry, by full name or by first name, ignoring case and extra spaces. A missing name in a
family with several parents, an unknown name or an ambiguous name returns an error that lists
the valid choices, e.g. `parent_name is not a parent in your family; use one of: Lakshmi
Gowda, Suresh Gowda.` No other free text is accepted.

## What a family member sees

| Parent's choice | `get_history` | `get_meds` | `weekly_summary` |
|---|---|---|---|
| Shares readings | yes | | yes |
| Shares medicines | | yes | medicine line included |
| Shares alerts | | | amber alerts counted (red always) |
| Consent pending or revoked | refused | refused | refused |
| Consent paused | what was already shared stays visible (as on the dashboard); nothing new is logged while paused | | |

These are the dashboard's own rules (`backend/app/sharing.py`), not a second copy.

## Limits (said plainly)

- HS256: the API, the MCP server and this gateway share one secret. It is not a third-party
  identity provider.
- No refresh and no revocation beyond the 60-second expiry. To stop the gateway, remove
  `FAMILY_GATEWAY_MEMBER_ID` or change `MCP_TOKEN_SECRET` and restart Khayal.
- The gateway trusts whoever can run it on this computer.
