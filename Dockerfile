# syntax=docker/dockerfile:1.7
# One image for both services (API and MCP server); docker-compose.yml picks the role.
#   docker compose up --build        (see README, "Run with Docker")

# --- Stage 1: the family dashboard (static files) ---
FROM node:22-slim AS dashboard
WORKDIR /src
COPY dashboard/package.json dashboard/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
COPY dashboard/ ./
RUN npm run build

# --- Stage 2: backend (FastAPI app + FastMCP server) ---
FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/backend/.venv \
    PATH=/app/backend/.venv/bin:$PATH \
    APP_MODE=mock \
    DATABASE_URL=sqlite:////data/khayal.db \
    VOICE_CACHE_DIR=/data/voice_cache

WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-install-project

COPY backend/app ./app
COPY backend/mcp_server ./mcp_server
COPY scripts/seed_demo.py /app/scripts/seed_demo.py
COPY samples/ /app/samples/
COPY --from=dashboard /src/dist /app/dashboard/dist
COPY --chmod=755 docker/entrypoint.sh /usr/local/bin/khayal-entrypoint

# Non-root. /data holds the SQLite file, the voice cache and (mock mode) generated secrets;
# a new named volume copies this directory's owner.
RUN python -m compileall -q app mcp_server \
    && useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin khayal \
    && mkdir -p /data \
    && chown khayal:khayal /data
USER khayal

EXPOSE 8000 8001
# The API's /health: 200 for ok or degraded (MCP down), 503 without a database.
# The mcp service overrides this in docker-compose.yml.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"]

ENTRYPOINT ["khayal-entrypoint"]
CMD ["api"]
