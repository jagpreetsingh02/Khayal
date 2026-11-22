"""One process-wide service: channels in, conversation engine, channels out.

    channel.receive(event) -> Incoming (voice note -> STT -> text) -> ConversationEngine
        -> [Outgoing] -> voice note (TTS) -> channel.send      | voice_call -> CallProvider
                                               |
                                    RuleAgent --MCP client (streamable HTTP)--> MCP server
                                                                                   |
                       family messages <- store outbox <- alert_family / notifier -+
    Scheduler.run(now): reminders, follow-ups, missed doses, low stock, weekly summary.

One lock serialises the engine (per-parent state machine) across web requests, the
Telegram poller, the ticker and the simulator's "advance time".
"""

import logging
import re
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app import clock
from app.agent.rule_agent import RuleAgent
from app.alerts.thresholds import EMERGENCY_LINE
from app.channels.base import Channel
from app.channels.simulator import SimulatorChannel
from app.config import Settings, get_settings
from app.conversation.engine import ConversationEngine
from app.conversation.messages import Outgoing
from app.demo import load_sample
from app.reader import get_reader
from app.reader.base import Reader
from app.scheduler import Scheduler
from app.storage import get_store
from app.storage.base import Store
from app.storage.models import utcnow
from app.voice import phrases
from app.voice.calls import CallProvider, get_call_provider
from app.voice.stt import STT, get_stt
from app.voice.tts import TTS, get_tts

log = logging.getLogger(__name__)
TZ = ZoneInfo("Asia/Kolkata")
ADVANCE_STEP = timedelta(minutes=5)  # "advance time" runs the jobs every 5 simulated minutes
RED_ALERT = re.compile(r"^RED alert for (?P<parent>.+?): (?P<reason>.*)$", re.S)


class CareCircleService:
    def __init__(
        self,
        store: Store,
        reader: Reader,
        agent: RuleAgent,
        channels: list[Channel],
        *,
        mcp_url: str | None = None,
        tts: TTS | None = None,
        stt: STT | None = None,
        calls: CallProvider | None = None,
    ) -> None:
        self.store = store
        self.reader = reader
        self.agent = agent
        self.channels = {c.name: c for c in channels}
        self.engine = ConversationEngine(store, reader, agent)
        self.scheduler = Scheduler(store, self.engine, agent)
        self.mcp_url = mcp_url
        self.tts = tts
        self.stt = stt
        self.calls = calls
        self._lock = threading.Lock()

    @property
    def simulator(self) -> SimulatorChannel:
        return self.channels["simulator"]  # type: ignore[return-value]

    def handle(self, channel: str, event) -> list[Outgoing]:
        """An inbound event from a channel: reply to the sender, notify the family."""
        adapter = self.channels[channel]
        incoming = adapter.receive(event)
        if incoming is None:
            return []
        if incoming.audio is not None:  # voice note -> text (or None: "not understood")
            text = self.stt.transcribe(incoming.audio) if self.stt else None
            incoming = incoming.model_copy(update={"text": text, "via_voice": True, "audio": None})
            annotate = getattr(adapter, "set_transcript", None)
            if annotate:
                annotate(incoming.sender_ref, text)
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

    def run_scheduler(self, now: datetime | None = None) -> list[Outgoing]:
        """One scheduler pass (reminders, follow-ups, missed doses, low stock, summary)."""
        with self._lock:
            out = self.scheduler.run(now) + self.engine.drain_outbox()
        self.deliver(out)
        return out

    def advance_time(self, minutes: float) -> list[Outgoing]:
        """Simulator demo: move the shared demo clock forward, running the timeouts and the
        scheduler every 5 simulated minutes so nothing is skipped."""
        remaining = timedelta(minutes=minutes)
        out: list[Outgoing] = []
        while remaining > timedelta(0):
            step = min(ADVANCE_STEP, remaining)
            clock.advance(self.store, step)
            remaining -= step
            out += self.tick()
            out += self.run_scheduler()
        return out

    def minutes_to_next_reminder(self, now: datetime | None = None) -> float | None:
        """Minutes until the next medicine reminder of any consenting parent (+1 min)."""
        now = (now or utcnow()).astimezone(TZ)
        best = None
        for parent in self.store.list_parents():
            if parent.consent_status != "granted":
                continue
            for med in self.store.list_medicines(parent.id):
                if not med.confirmed:
                    continue
                for hhmm in med.times:
                    h, m = map(int, hhmm.split(":"))
                    due = now.replace(hour=h, minute=m, second=0, microsecond=0)
                    if due <= now:
                        due += timedelta(days=1)
                    if best is None or due < best:
                        best = due
        return None if best is None else (best - now).total_seconds() / 60 + 1

    def send_weekly_summary(self, parent_id: str) -> list[Outgoing]:
        """Simulator demo: the weekly summary now, instead of waiting for Sunday 19:00."""
        parent = self.store.get_member(parent_id)
        with self._lock:
            out = self.scheduler.weekly_summary(parent, utcnow(), force=True)
        self.deliver(out)
        return out

    # --- delivery: voice notes and calls ---

    def _with_voice(self, msg: Outgoing) -> Outgoing:
        if msg.voice is None:
            return msg
        if self.tts is None:
            return msg.model_copy(update={"voice": None})  # voice off: text only
        url = self.tts.speak(msg.voice)
        if url is None and self.tts.name != "mock":
            return msg.model_copy(update={"voice": None})  # no key/budget/error: text only
        return msg.model_copy(update={"audio_url": url, "voice_mock": url is None})

    def _place_call(self, msg: Outgoing) -> None:
        if self.calls is None or msg.member_id is None:
            return
        member = self.store.get_member(msg.member_id)
        m = RED_ALERT.match(msg.text)
        parent, reason = (m["parent"], m["reason"]) if m else ("your family member", msg.text)
        reason = reason.replace(EMERGENCY_LINE, "").strip()  # that line is for the parent
        lang = phrases.lang_of(member.language)
        script = phrases.say("red_call", lang, parent=parent, reason=reason)
        url = self.tts.speak(script) if self.tts else None
        mock_voice = self.tts is not None and self.tts.name == "mock"
        try:
            self.calls.call(member, script, url, mock_voice)
        except Exception:
            log.exception("call to %s failed", member.id)

    def deliver(self, messages: list[Outgoing]) -> None:
        for msg in messages:
            if msg.kind == "voice_call":
                self._place_call(msg)
                continue
            msg = self._with_voice(msg)
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
    simulator = channels[0]
    return CareCircleService(
        store,
        get_reader(settings),
        agent,
        channels,
        mcp_url=settings.mcp_server_url,
        tts=get_tts(settings, store),
        stt=get_stt(settings),
        calls=get_call_provider(settings, simulator),
    )
