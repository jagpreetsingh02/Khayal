"""FastAPI entry point: web simulator, channel webhooks, the dashboard API (/api) and the
built family dashboard (/dashboard, from dashboard/dist after `npm run build`).

Run together with the MCP server: `uv run python ../scripts/start.py` (see README).
"""

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import clock
from app.api.simulator import router as simulator_router
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


def create_app(service: KhayalService | None = None, tick_seconds: float = TICK_SECONDS):
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        svc = app.state.service = service or build_service()
        clock.sync_from(svc.store)  # keep the demo clock across restarts
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

    app = FastAPI(title="Khayal", version="0.5.0", lifespan=lifespan)
    app.include_router(simulator_router)

    @app.middleware("http")
    async def demo_clock_header(request: Request, call_next):
        response = await call_next(request)
        # The dashboard shows "5 min ago" against this, so it follows "advance time".
        from app.storage.models import utcnow

        response.headers["X-Demo-Now"] = utcnow().isoformat()
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
    def health() -> dict[str, str]:
        settings = get_settings()
        return {"status": "ok", "mode": settings.app_mode}

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/simulator")

    return app


app = create_app()
