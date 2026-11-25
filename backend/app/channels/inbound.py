"""The durable inbox: persist first, then answer the channel, then process (webhooks and the
Telegram poller).

    webhook / poller --accept--> inbox row (pending)  --> channel gets its 200
    worker: claim (processing, lease) --> service.process in the sender's turn
        --> done (payload blanked) | pending again after 5 s, 30 s, 2 min, 10 min | failed

- `accept` writes the inbox row; that insert is also the duplicate check (a repeated
  Twilio MessageSid / Telegram update_id is answered the same way and not stored again).
  If the write fails the webhook fails too, so the channel sends the message again.
- The worker claims due rows oldest first, takes each sender's place in line in that order
  (per-parent order, app/channels/turns.py) and processes them on its own threads, so
  different parents still run in parallel. It renews the lease of the rows it is working
  on; a row whose lease ran out (its worker died) goes back to pending and is processed
  again. Delivery is at least once, not exactly once: see ConversationEngine.handle_message
  for why a replay does not log, confirm or alert twice.
- On start: expired leases are released, done/failed rows older than INBOX_RETENTION_DAYS
  are purged (also hourly), and pending rows are processed.
- `drain` (clean shutdown): stop claiming, finish what is due and in flight. Anything left
  stays in the inbox for the next start.
- Nothing from the payload (phone numbers, message text) is ever logged.
"""

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from datetime import timedelta
from functools import partial

from app import clock
from app.channels.service import INBOX_LEASE, KhayalService
from app.config import get_settings

log = logging.getLogger(__name__)

MAX_IN_FLIGHT = 16  # claimed messages being processed or waiting for their sender's turn
POLL_EVERY = 1.0  # seconds between passes when no webhook wakes the worker
MAINTAIN_EVERY = 15.0  # renew leases, release expired ones
PURGE_EVERY = 3600.0


class Inbound:
    def __init__(self, service: KhayalService, *, poll_every: float = POLL_EVERY) -> None:
        self.service = service
        self.store = service.store
        self.poll_every = poll_every
        self._pool = ThreadPoolExecutor(MAX_IN_FLIGHT, thread_name_prefix="inbox")
        self._in_flight: dict[str, asyncio.Future] = {}
        self._wake: asyncio.Event | None = None
        self._worker: asyncio.Task | None = None
        self._last_maintain = self._last_purge = 0.0
        self._stopping = False

    # --- channel side ---

    async def accept(self, channel: str, event, external_id: str | None) -> bool:
        """Persist the message; returns False for a repeated id (nothing new to process).
        Raises if it could not be stored: the webhook must not answer 200 then."""
        row = await asyncio.to_thread(self.service.accept, channel, event, external_id)
        if row is None:
            return False
        if self._wake is not None:
            self._wake.set()
        return True

    # --- worker ---

    async def start(self) -> None:
        """Recover what an earlier run left behind, then process in the background."""
        self._wake = asyncio.Event()
        await asyncio.to_thread(self._maintain, [], True)
        self._worker = asyncio.create_task(self._run())

    async def _run(self) -> None:
        assert self._wake is not None
        while not self._stopping:
            self._wake.clear()
            try:
                await self._pump()
                if time.monotonic() - self._last_maintain >= MAINTAIN_EVERY:
                    await asyncio.to_thread(self._maintain, list(self._in_flight))
            except Exception:
                log.exception("inbox worker pass failed")
            if self._stopping:
                break
            with suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=self.poll_every)

    async def _pump(self) -> int:
        """Claim what is due (up to the free slots) and start processing it."""
        free = MAX_IN_FLIGHT - len(self._in_flight)
        if free <= 0:
            return 0
        batch = await asyncio.to_thread(self.service.claim_inbox, free)
        loop = asyncio.get_running_loop()
        for row, ticket in batch:  # tickets were taken in arrival order
            fut = loop.run_in_executor(self._pool, self.service.process, row, ticket)
            self._in_flight[row.id] = fut
            fut.add_done_callback(partial(self._done, row.id))
        return len(batch)

    def _done(self, row_id: str, _fut: asyncio.Future) -> None:
        self._in_flight.pop(row_id, None)
        if self._wake is not None:
            self._wake.set()  # a slot is free; retries that are due get picked up

    def _maintain(self, in_flight: list[str], purge: bool = False) -> None:
        """Renew our leases, release expired ones (a dead worker's), purge old rows."""
        now = clock.real_now()
        self._last_maintain = time.monotonic()
        self.store.renew_inbox_leases(in_flight, now + INBOX_LEASE)
        released = self.store.release_expired_inbox(now)
        if released:
            log.warning("%d inbox messages left processing by a stopped worker: retrying",
                        released)  # fmt: skip
        if purge or time.monotonic() - self._last_purge >= PURGE_EVERY:
            self._last_purge = time.monotonic()
            days = get_settings().inbox_retention_days
            purged = self.store.purge_inbox(now - timedelta(days=days))
            if purged:
                log.info("purged %d inbox messages older than %g days", purged, days)

    async def drain(self, timeout: float = 30) -> None:
        """Clean shutdown: stop the worker, then finish everything due and in flight."""
        if self._worker is not None:
            # Not cancelled: a pass that is in a store call finishes it first.
            self._stopping = True
            if self._wake is not None:
                self._wake.set()
            await self._worker
            self._worker = None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                started = await self._pump()
            except Exception:
                log.exception("inbox: could not claim messages at shutdown")
                started = 0
            if not started and not self._in_flight:
                break
            if self._in_flight:
                await asyncio.wait(
                    set(self._in_flight.values()),
                    timeout=max(0.0, deadline - time.monotonic()),
                    return_when=asyncio.FIRST_COMPLETED,
                )
        if self._in_flight:
            log.error("%d inbound messages still running at shutdown; they are retried on "
                      "the next start", len(self._in_flight))  # fmt: skip
        self._pool.shutdown(wait=False)
