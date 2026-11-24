"""Accept now, process in the background (webhooks and the Telegram poller).

`accept` records the channel's message id (a repeated id is dropped: Twilio and Telegram
retry webhooks they think failed) and takes the sender's place in line, then hands the
message to a background task and returns, so the webhook can answer at once.

Known gap: the message is only in memory between the ack and the end of processing. If the
process crashes in that window, the message is lost (the channel will not resend it: we
acknowledged it, and its id is already recorded). A durable inbox table, written before the
ack and processed by a worker, would close this; it is future work.
"""

import asyncio
import logging

from app.channels.service import KhayalService

log = logging.getLogger(__name__)


class Inbound:
    def __init__(self, service: KhayalService) -> None:
        self.service = service
        self._tasks: set[asyncio.Task] = set()

    async def accept(self, channel: str, event, external_id: str | None) -> bool:
        """False if this id was already seen (nothing is processed)."""
        ticket = await asyncio.to_thread(self.service.accept, channel, event, external_id)
        if ticket is None:
            return False
        task = asyncio.create_task(asyncio.to_thread(self.service.process, channel, event, ticket))
        self._tasks.add(task)  # keep a reference until it finishes
        task.add_done_callback(self._tasks.discard)
        return True

    async def drain(self, timeout: float = 30) -> None:
        """On shutdown: let accepted messages finish (they were already acknowledged)."""
        if self._tasks:
            done, pending = await asyncio.wait(set(self._tasks), timeout=timeout)
            if pending:
                log.error("%d inbound messages still running at shutdown", len(pending))
