#!/bin/sh
# One image, three roles (see docker-compose.yml):
#   init  one-shot step before the others: checks the config; in mock mode it makes random
#         SESSION_SECRET / MCP_TOKEN_SECRET / MCP_SHARED_SECRET on first start (kept in the
#         data volume, shared by both services; a key missing from an older file is added)
#         and seeds the demo database if there is none yet.
#   api   the FastAPI app (dashboard, webhooks; the web simulator in mock mode only)
#   mcp   the MCP server (streamable HTTP, a signed token on every call)
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
    # Never overwrites a secret: keys already in the file are kept, missing ones (e.g.
    # MCP_TOKEN_SECRET in a volume from before it existed) are appended under an exclusive
    # lock, so two containers starting at once end up with the same secrets.
    python - "$SECRETS_FILE" <<'PY'
import fcntl, os, secrets, sys
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
with os.fdopen(fd, "r+") as f:
    fcntl.flock(f, fcntl.LOCK_EX)
    f.seek(0)
    have = {line.split("=", 1)[0] for line in f if "=" in line}
    keys = ("SESSION_SECRET", "MCP_TOKEN_SECRET", "MCP_SHARED_SECRET")
    made = [k for k in keys if k not in have]
    for key in made:
        f.write(f"{key}={secrets.token_urlsafe(32)}\n")
if made:
    print(f"init: made random {', '.join(made)} for mock mode")
PY
}

mock_secret() {
    # Export the secret named $1 from the generated file if .env left it empty.
    eval "current=\${$1:-}"
    if [ -z "$current" ]; then
        value="$(sed -n "s/^$1=//p" "$SECRETS_FILE")"
        export "$1=$value"
    fi
}

use_mock_secrets() {
    # Mock mode only: fill whatever .env left empty from the generated file.
    [ "$MODE" = "mock" ] || return 0
    make_mock_secrets
    mock_secret SESSION_SECRET
    mock_secret MCP_TOKEN_SECRET
    mock_secret MCP_SHARED_SECRET
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
