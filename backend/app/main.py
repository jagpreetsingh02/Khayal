"""FastAPI entry point: webhooks, web simulator and dashboard API (routes added in later phases)."""

from fastapi import FastAPI

from app.config import get_settings

app = FastAPI(title="Care Circle", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    settings = get_settings()
    return {"status": "ok", "mode": settings.app_mode}
