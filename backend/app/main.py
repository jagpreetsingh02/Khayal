"""FastAPI entry point: web simulator, channel webhooks, the dashboard API (/api) and the
built family dashboard (/dashboard, from dashboard/dist after `npm run build`).

Run together with the MCP server: `uv run python ../scripts/start.py` (see README).
"""

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import clock
from app.api.auth import LoginLimits
from app.api.simulator import router as simulator_router
from app.channels.inbound import Inbound
from app.channels.service import KhayalService, build_service
from app.config import get_settings
from app.demo import ensure_demo_family

log = logging.getLogger(__name__)
TICK_SECONDS = 30  # read-back expiry, the 15-minute no-repeat amber, and the scheduler
DASHBOARD_DIST = Path(__file__).resolve().parents[2] / "dashboard" / "dist"
NOT_BUILT = """<!doctype html><meta charset="utf-8"><title>Khayal dashboard</title>
<body style="font-family:system-ui;max-width:40rem;margin:3rem auto;padding:0 1rem">
<h1>Dashboard not built yet</h1><p>Build it once, then reload this page:</p>
<pre>cd dashboard && npm install && npm run build</pre>
<p>Or run the dev server: <code>npm run dev</code> (http://localhost:5173/dashboard/).</p>"""


async def _ticker(service: KhayalService, every: float) -> None:
    while True:
        await asyncio.sleep(every)
        try:
            await asyncio.to_thread(service.tick)
            await asyncio.to_thread(service.run_scheduler)
        except Exception:
            log.exception("tick / scheduler failed")


# Security headers on every response. The dashboard CSP allows only our own scripts, styles,
# images and API calls (the Vite build has no inline scripts; React sets styles through the
# DOM, which CSP does not block). The simulator is a plain page with inline JS/CSS and
# browser speech, so it gets its own looser policy (it is a demo tool).
BASE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}
CSP_STRICT = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "font-src 'self'; connect-src 'self'; media-src 'self'; object-src 'none'; "
    "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
)
CSP_SIMULATOR = (
    "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; "
    "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
)
# FastAPI's /docs page loads Swagger UI from a CDN with an inline script (mock mode only).
CSP_DOCS = (
    "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "img-src 'self' data: https://fastapi.tiangolo.com; object-src 'none'; "
    "frame-ancestors 'none'"
)
DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")


def csp_for(path: str) -> str:
    if path == "/simulator" or path.startswith("/simulator/"):
        return CSP_SIMULATOR
    if path.startswith(DOCS_PATHS):
        return CSP_DOCS
    return CSP_STRICT


def _database_ok(svc: KhayalService) -> bool:
    try:
        svc.store.ping()
    except Exception:
        log.exception("health: database check failed")
        return False
    return True


def _mcp_status(svc: KhayalService) -> str:
    """reachable / unreachable (any HTTP answer counts: without the bearer it is a 401), or
    in-process when the agent holds the server object (tests)."""
    target = svc.agent.mcp
    if not isinstance(target, str):
        return "in-process"
    try:
        httpx.get(target, timeout=2)
    except httpx.HTTPError:
        return "unreachable"
    return "reachable"


def create_app(service: KhayalService | None = None, tick_seconds: float = TICK_SECONDS):
    settings = get_settings()
    if service is None:  # the real app (tests pass their own service)
        from app.startup_checks import enforce

        enforce(settings, "api")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        svc = app.state.service = service or build_service()
        inbound = app.state.inbound = Inbound(svc)
        clock.sync_from(svc.store)  # keep the demo clock across restarts
        ensure_demo_family(svc.store)
        tasks = []
        if tick_seconds:
            tasks.append(asyncio.create_task(_ticker(svc, tick_seconds)))
        poller = getattr(svc.channels.get("telegram"), "start_polling", None)
        if poller and service is None and get_settings().telegram_mode == "polling":
            tasks.append(asyncio.create_task(poller(svc, inbound)))
        yield
        for task in tasks:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        await inbound.drain()  # acknowledged messages still being processed

    # Mock mode only: API docs (/docs, /redoc, /openapi.json) and the web simulator, which
    # sends messages as any parent and resets / reseeds / moves the demo clock with no sign-in.
    # In dev and aws those routes do not exist (404).
    mock = settings.app_mode == "mock"
    docs = mock
    app = FastAPI(
        title="Khayal",
        version="0.5.0",
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
    )
    app.state.login_limits = LoginLimits()
    if mock:
        app.include_router(simulator_router)

    @app.middleware("http")
    async def headers(request: Request, call_next):
        response = await call_next(request)
        # The dashboard shows "5 min ago" against this, so it follows "advance time".
        from app.storage.models import utcnow

        response.headers["X-Demo-Now"] = utcnow().isoformat()
        for name, value in BASE_HEADERS.items():
            response.headers.setdefault(name, value)
        response.headers.setdefault("Content-Security-Policy", csp_for(request.url.path))
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.get("/voice/{name}", include_in_schema=False)
    def voice_clip(name: str) -> FileResponse:
        """Cached TTS audio (only files named by their hash; no paths)."""
        from app.voice.tts import CLIP_NAME

        path = Path(get_settings().voice_cache_dir) / name
        if not CLIP_NAME.match(name) or not path.is_file():
            raise HTTPException(404, "No such voice note")
        return FileResponse(path, media_type="audio/mpeg")

    from app.api.dashboard import public as dashboard_login
    from app.api.dashboard import router as dashboard_router
    from app.api.webhooks import router as webhooks_router

    app.include_router(webhooks_router)
    app.include_router(dashboard_login)
    app.include_router(dashboard_router)

    if (DASHBOARD_DIST / "index.html").is_file():
        app.mount("/dashboard", StaticFiles(directory=DASHBOARD_DIST, html=True), name="dashboard")
    else:

        @app.get("/dashboard", include_in_schema=False)
        @app.get("/dashboard/{rest:path}", include_in_schema=False)
        def dashboard_not_built(rest: str = "") -> HTMLResponse:
            return HTMLResponse(NOT_BUILT, status_code=503)

    @app.get("/health")
    def health(request: Request, response: Response) -> dict[str, str]:
        """ok: database and MCP server answer. degraded: the MCP server is down (the app still
        serves the dashboard; readings cannot be logged). error (503): no database."""
        svc: KhayalService | None = getattr(request.app.state, "service", None)
        database = "ok" if svc is not None and _database_ok(svc) else "error"
        mcp = _mcp_status(svc) if svc is not None else "unknown"
        status = "ok" if mcp in ("reachable", "in-process") else "degraded"
        if database != "ok":
            status, response.status_code = "error", 503
        return {"status": status, "mode": get_settings().app_mode, "database": database,
                "mcp": mcp}  # fmt: skip

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/simulator" if mock else "/dashboard/")

    return app


app = create_app()
