"""FastAPI entry point: web simulator, channel webhooks, the dashboard API (/api) and the
built family dashboard (/dashboard, from dashboard/dist after `npm run build`).

Run together with the MCP server: `uv run python ../scripts/start.py` (see README).
"""

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.simulator import router as simulator_router
from app.channels.service import CareCircleService, build_service
from app.config import get_settings
from app.demo import ensure_demo_family

log = logging.getLogger(__name__)
TICK_SECONDS = 30  # read-back expiry and the 15-minute no-repeat amber
DASHBOARD_DIST = Path(__file__).resolve().parents[2] / "dashboard" / "dist"
NOT_BUILT = """<!doctype html><meta charset="utf-8"><title>Care Circle dashboard</title>
<body style="font-family:system-ui;max-width:40rem;margin:3rem auto;padding:0 1rem">
<h1>Dashboard not built yet</h1><p>Build it once, then reload this page:</p>
<pre>cd dashboard && npm install && npm run build</pre>
<p>Or run the dev server: <code>npm run dev</code> (http://localhost:5173/dashboard/).</p>"""


async def _ticker(service: CareCircleService, every: float) -> None:
    while True:
        await asyncio.sleep(every)
        try:
            await asyncio.to_thread(service.tick)
        except Exception:
            log.exception("timeout tick failed")


def create_app(service: CareCircleService | None = None, tick_seconds: float = TICK_SECONDS):
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        svc = app.state.service = service or build_service()
        ensure_demo_family(svc.store)
        tasks = []
        if tick_seconds:
            tasks.append(asyncio.create_task(_ticker(svc, tick_seconds)))
        poller = getattr(svc.channels.get("telegram"), "start_polling", None)
        if poller and service is None and get_settings().telegram_mode == "polling":
            tasks.append(asyncio.create_task(poller(svc)))
        yield
        for task in tasks:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title="Care Circle", version="0.4.0", lifespan=lifespan)
    app.include_router(simulator_router)

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
    def health() -> dict[str, str]:
        settings = get_settings()
        return {"status": "ok", "mode": settings.app_mode}

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/simulator")

    return app


app = create_app()
