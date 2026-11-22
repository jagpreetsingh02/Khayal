"""One process-wide service: channels in, conversation engine, channels out.

    channel.receive(event) -> Incoming -> ConversationEngine -> [Outgoing] -> channel.send
                                               |
                                    RuleAgent --MCP client (streamable HTTP)--> MCP server
                                                                                   |
                       family messages <- store outbox <- alert_family / notifier -+

One lock serialises the engine (per-parent state machine) across web requests, the
Telegram poller and the timeout ticker.
"""

import logging
import threading

from app.agent.rule_agent import RuleAgent
from app.channels.base import Channel
from app.channels.simulator import SimulatorChannel
from app.config import Settings, get_settings
from app.conversation.engine import ConversationEngine
from app.conversation.messages import Outgoing
from app.demo import load_sample
from app.reader import get_reader
from app.reader.base import Reader
from app.storage import get_store
from app.storage.base import Store

log = logging.getLogger(__name__)


class CareCircleService:
    def __init__(
        self,
        store: Store,
        reader: Reader,
        agent: RuleAgent,
        channels: list[Channel],
        *,
        mcp_url: str | None = None,
    ) -> None:
        self.store = store
        self.reader = reader
        self.agent = agent
        self.channels = {c.name: c for c in channels}
        self.engine = ConversationEngine(store, reader, agent)
        self.mcp_url = mcp_url
        self._lock = threading.Lock()

    @property
    def simulator(self) -> SimulatorChannel:
        return self.channels["simulator"]  # type: ignore[return-value]

    def handle(self, channel: str, event) -> list[Outgoing]:
        """An inbound event from a channel: reply to the sender, notify the family."""
        incoming = self.channels[channel].receive(event)
        if incoming is None:
            return []
        with self._lock:
            out = self.engine.handle_message(incoming)
        self.deliver(out)
        return out

    def tick(self) -> list[Outgoing]:
        """Timeouts (read-back expiry, no-repeat amber) and any undelivered family messages."""
        with self._lock:
            out = self.engine.tick()
        self.deliver(out)
        return out

    def skip_time(self, member_id: str, minutes: float) -> list[Outgoing]:
        """Simulator demo button: pretend `minutes` passed for this parent."""
        with self._lock:
            out = self.engine.skip_time(member_id, minutes)
        self.deliver(out)
        return out

    def flush_outbox(self) -> list[Outgoing]:
        """Deliver messages written to the outbox (e.g. by the dashboard API) right away."""
        with self._lock:
            out = self.engine.drain_outbox()
        self.deliver(out)
        return out

    def deliver(self, messages: list[Outgoing]) -> None:
        for msg in messages:
            channel = self.channels.get(msg.channel)
            if channel is None:
                log.warning(
                    "no %s channel configured; message to %s dropped", msg.channel, msg.to_ref
                )
                continue
            try:
                channel.send(msg)
            except Exception:
                log.exception("sending on %s to %s failed", msg.channel, msg.to_ref)


def build_service(settings: Settings | None = None) -> CareCircleService:
    """The app's service from .env: simulator always on, plus CHANNEL=telegram / twilio."""
    settings = settings or get_settings()
    store = get_store()
    channels: list[Channel] = [SimulatorChannel(load_sample)]
    if settings.channel == "telegram":
        from app.channels.telegram import TelegramChannel

        channels.append(TelegramChannel(settings.telegram_bot_token))
    elif settings.channel == "twilio":
        from app.channels.twilio_whatsapp import TwilioWhatsAppChannel

        channels.append(TwilioWhatsAppChannel.from_settings(settings))
    agent = RuleAgent(store, settings.mcp_server_url)
    return CareCircleService(
        store, get_reader(settings), agent, channels, mcp_url=settings.mcp_server_url
    )
