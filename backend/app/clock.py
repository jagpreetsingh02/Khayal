"""One clock for the whole app, with a demo offset ("advance time" in the simulator).

`app.storage.models.utcnow()` adds the offset, so every timestamp, timeout, reminder and
summary moves together. The offset lives in the store (kv `demo_clock_offset_s`) so the API
and the separate MCP server process agree: the API sets it, the MCP server re-reads it on
each tool call (`sync_from`). Always zero when APP_MODE=aws.
"""

from datetime import UTC, datetime, timedelta

OFFSET_KEY = "demo_clock_offset_s"
_offset = timedelta(0)


def offset() -> timedelta:
    return _offset


def set_offset(value: timedelta) -> None:
    global _offset
    _offset = value


def real_now() -> datetime:
    return datetime.now(UTC)


def sync_from(store) -> timedelta:
    """Load the shared offset (another process may have advanced it)."""
    from app.config import get_settings

    if get_settings().app_mode == "aws":
        set_offset(timedelta(0))
        return _offset
    raw = store.get_kv(OFFSET_KEY)
    set_offset(timedelta(seconds=float(raw)) if raw else timedelta(0))
    return _offset


def advance(store, delta: timedelta) -> timedelta:
    """Move the demo clock forward by `delta` and share it with the other process."""
    sync_from(store)
    set_offset(_offset + delta)
    store.set_kv(OFFSET_KEY, str(_offset.total_seconds()))
    return _offset


def set_shared(store, value: timedelta) -> None:
    """Set the offset here and for the other process (tests, demo tools)."""
    set_offset(value)
    store.set_kv(OFFSET_KEY, str(value.total_seconds()))


def reset(store) -> None:
    set_offset(timedelta(0))
    store.set_kv(OFFSET_KEY, "0")
