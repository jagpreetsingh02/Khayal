"""FastAPI entry point: web simulator, channel webhooks and (Phase 4) the dashboard API.

Run together with the MCP server: `uv run python ../scripts/start.py` (see README).
"""

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from app.api.simulator import router as simulator_router
from app.channels.service import CareCircleService, build_service
from app.config import get_settings
from app.demo import ensure_demo_family

log = logging.getLogger(__name__)
TICK_SECONDS = 30  # read-back expiry and the 15-minute no-repeat amber


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

    app = FastAPI(title="Care Circle", version="0.3.0", lifespan=lifespan)
    app.include_router(simulator_router)

    from app.api.webhooks import router as webhooks_router

    app.include_router(webhooks_router)

    @app.get("/health")
    def health() -> dict[str, str]:
        settings = get_settings()
        return {"status": "ok", "mode": settings.app_mode}

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/simulator")

    return app


app = create_app()
