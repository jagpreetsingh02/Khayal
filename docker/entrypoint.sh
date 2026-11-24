#!/bin/sh
# One image, three roles (see docker-compose.yml):
#   init  one-shot step before the others: checks the config; in mock mode it makes random
#         SESSION_SECRET / MCP_SHARED_SECRET on first start (kept in the data volume, shared by
#         both services) and seeds the demo database if there is none yet.
#   api   the FastAPI app (dashboard, webhooks; the web simulator in mock mode only)
#   mcp   the MCP server (streamable HTTP, bearer auth)
# Anything else runs as a plain command, e.g. `docker compose run --rm api sh`.
set -eu

# Docker Compose's env_file keeps the comment of an empty value as the value:
# "SESSION_SECRET=      # signs ..." arrives as "# signs ...", a 90-character "secret".
# python-dotenv (how the app reads .env outside Docker) treats it as empty; so do we.
for name in $(env | sed -n 's/^\([A-Za-z_][A-Za-z0-9_]*\)=#.*/\1/p'); do
    export "$name="
done

DATA_DIR="${KHAYAL_DATA_DIR:-/data}"
SECRETS_FILE="$DATA_DIR/mock-secrets.env"
MODE="${APP_MODE:-mock}"

make_mock_secrets() {
    # "x" mode: never overwrites, so two containers starting at once keep the same secrets.
    python - "$SECRETS_FILE" <<'PY'
import os, secrets, sys
try:
    fd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    sys.exit(0)
with os.fdopen(fd, "w") as f:
    f.write(f"SESSION_SECRET={secrets.token_urlsafe(32)}\n")
    f.write(f"MCP_SHARED_SECRET={secrets.token_urlsafe(32)}\n")
print("init: made random SESSION_SECRET and MCP_SHARED_SECRET for mock mode")
PY
}

use_mock_secrets() {
    # Mock mode only: fill whatever .env left empty from the generated file.
    [ "$MODE" = "mock" ] || return 0
    make_mock_secrets
    if [ -z "${SESSION_SECRET:-}" ]; then
        SESSION_SECRET="$(sed -n 's/^SESSION_SECRET=//p' "$SECRETS_FILE")"
        export SESSION_SECRET
    fi
    if [ -z "${MCP_SHARED_SECRET:-}" ]; then
        MCP_SHARED_SECRET="$(sed -n 's/^MCP_SHARED_SECRET=//p' "$SECRETS_FILE")"
        export MCP_SHARED_SECRET
    fi
}

check_config() {
    # The same startup check the api and mcp processes run: in dev/aws it stops with one
    # message listing every problem (missing or short secrets, webhook settings...).
    python -c 'from app.config import get_settings; from app.startup_checks import enforce
s = get_settings(); enforce(s, "api"); enforce(s, "mcp")'
}

case "${1:-api}" in
init)
    use_mock_secrets
    check_config
    db="${DATABASE_URL#sqlite:///}"
    if [ "$MODE" = "mock" ] && [ ! -f "$db" ]; then
        echo "init: no database yet, seeding the demo family (14 days of readings)"
        python /app/scripts/seed_demo.py
    fi
    echo "init: ok (APP_MODE=$MODE)"
    ;;
api)
    use_mock_secrets
    exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --no-server-header
    ;;
mcp)
    use_mock_secrets
    exec python -m mcp_server.server
    ;;
*)
    exec "$@"
    ;;
esac
