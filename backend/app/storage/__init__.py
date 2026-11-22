"""Storage adapters. Pick the implementation with STORAGE in .env (sqlite now, dynamodb later)."""

from app.config import get_settings
from app.storage.base import ConflictError, NotFoundError, Store

_store: Store | None = None


def get_store() -> Store:
    """Process-wide store built from settings on first use."""
    global _store
    if _store is None:
        settings = get_settings()
        if settings.storage != "sqlite":
            raise NotImplementedError(f"STORAGE={settings.storage} is not implemented yet")
        from app.storage.sqlite_store import SQLiteStore

        _store = SQLiteStore.from_url(settings.database_url)
    return _store


def set_store(store: Store | None) -> None:
    """Swap the process-wide store (tests, scripts)."""
    global _store
    _store = store


__all__ = ["ConflictError", "NotFoundError", "Store", "get_store", "set_store"]
