"""One process-wide service: channels in, conversation engine, channels out.

    channel.receive(event) -> Incoming (voice note -> STT -> text) -> ConversationEngine
        -> [Outgoing] -> voice note (TTS) -> channel.send      | voice_call -> CallProvider
                                               |
                                    RuleAgent --MCP client (streamable HTTP)--> MCP server
                                                                                   |
                       family messages <- store outbox <- alert_family / notifier -+
    Scheduler.run(now): reminders, follow-ups, missed doses, low stock, weekly summary.

Reliability (see README "Reliability"):
- Per-parent order: every message, timeout and scheduler pass for a parent runs in that
  parent's turn (app/channels/turns.py), strictly in arrival order. Different parents run
  in parallel, so a slow photo (vision call) or voice note (STT) only delays its own sender.
  The background ticker skips a parent who is busy right now and catches up 30 s later.
- Family messages: always via the store outbox. A sender claims due rows with a lease, sends
  them, and marks each delivered only after the channel accepted it. Failures retry with
  backoff (30 s, 1 m, 2 m, 5 m, then every 10 m); red alert messages retry until delivered,
  everything else stops after 8 attempts. Final failures and every red failure go to
  audit_log (tool=delivery_failed) and the ERROR log. The outbox needs no per-member turn:
  the claim is atomic, so two senders never send the same row.
- Direct replies to the parent's own message are sent at once, inside the parent's turn
  (so replies keep their order), and are not retried.
"""

import json
import logging
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app import clock
from app.agent.rule_agent import RuleAgent
from app.alerts.thresholds import EMERGENCY_LINE
from app.channels.base import Channel, ChannelError
from app.channels.simulator import SimulatorChannel
from app.channels.turns import Ticket, Turns
from app.config import Settings, get_settings
from app.conversation.engine import ConversationEngine, outbox_to_outgoing
from app.conversation.messages import Outgoing
from app.demo import load_sample
from app.reader import get_reader
from app.reader.base import Reader
from app.scheduler import Scheduler
from app.storage import get_store
from app.storage.base import NotFoundError, Store
from app.storage.models import AuditEntry, OutboxMessage, utcnow
from app.voice import phrases
from app.voice.calls import CallProvider, get_call_provider
from app.voice.stt import STT, get_stt
from app.voice.tts import TTS, get_tts

log = logging.getLogger(__name__)
TZ = ZoneInfo("Asia/Kolkata")
ADVANCE_STEP = timedelta(minutes=5)  # "advance time" runs the jobs every 5 simulated minutes
RED_ALERT = re.compile(r"^RED alert for (?P<parent>.+?): (?P<reason>.*)$", re.S)

# Outbox delivery
OUTBOX_LEASE = timedelta(minutes=2)  # a crashed sender's claim expires after this
OUTBOX_BATCH = 10  # claimed at a time, so a rate-limited channel finishes within the lease
RETRY_DELAYS = (30, 60, 120, 300)  # seconds after attempt 1, 2, 3, 4
RETRY_EVERY = 600  # then every 10 minutes
MAX_ATTEMPTS = 8  # except red alert messages, which retry until delivered


def retry_delay(attempts: int) -> timedelta:
    """Wait before the next try, after `attempts` failed attempts."""
    seconds = RETRY_DELAYS[attempts - 1] if 1 <= attempts <= len(RETRY_DELAYS) else RETRY_EVERY
    return timedelta(seconds=seconds)


@dataclass(frozen=True)
class Delivery:
    ok: bool
    error: str | None = None


class KhayalService:
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
        self.engine = ConversationEngine(store, reader, agent, drain_family=False)
        self.scheduler = Scheduler(store, self.engine, agent)
        self.mcp_url = mcp_url
        self.tts = tts
        self.stt = stt
        self.calls = calls
        self.turns = Turns()

    @property
    def simulator(self) -> SimulatorChannel:
        return self.channels["simulator"]  # type: ignore[return-value]

    # --- inbound ---

    def reserve(self, channel: str, event) -> Ticket:
        """Take the sender's place in line (cheap: no network). Messages of one parent are
        processed in the order their tickets were taken."""
        adapter = self.channels.get(channel)
        ref = adapter.sender_of(event) if adapter else None
        member = self.store.find_member_by_channel(channel, ref) if ref else None
        return self.turns.reserve(member.id if member else f"{channel}:{ref or '?'}")

    def accept(self, channel: str, event, external_id: str | None) -> Ticket | None:
        """Webhook / poller: record the channel's message id and take a place in line.
        None means this id was seen before (a retry): do not process it again."""
        if external_id is None:
            log.warning("%s message without an id: processed without duplicate check", channel)
        elif not self.store.mark_inbound_seen(channel, external_id):
            log.info("duplicate %s message %s ignored", channel, external_id)
            return None
        return self.reserve(channel, event)

    def process(self, channel: str, event, ticket: Ticket) -> None:
        """Background processing of an accepted message; errors are logged, not raised."""
        try:
            self.handle(channel, event, ticket)
        except ChannelError as exc:
            log.warning("%s inbound failed: %s", channel, exc)
        except Exception:
            log.exception("%s inbound failed", channel)

    def handle(self, channel: str, event, ticket: Ticket | None = None) -> list[Outgoing]:
        """An inbound event from a channel: reply to the sender, notify the family."""
        ticket = ticket or self.reserve(channel, event)
        with self.turns.turn(ticket):  # this sender only; other parents run in parallel
            adapter = self.channels[channel]
            incoming = adapter.receive(event)  # may download the photo
            if incoming is None:
                return []
            if incoming.audio is not None:  # voice note -> text (or None: "not understood")
                text = self.stt.transcribe(incoming.audio) if self.stt else None
                incoming = incoming.model_copy(
                    update={"text": text, "via_voice": True, "audio": None}
                )
                annotate = getattr(adapter, "set_transcript", None)
                if annotate:
                    annotate(incoming.sender_ref, text)
            out = self.engine.handle_message(incoming)
            self.deliver(out)  # replies, before this parent's next message is handled
        return out + self.flush_outbox()

    # --- timeouts, scheduler, demo controls ---

    @contextmanager
    def _member_turn(self, member_id: str, wait: bool) -> Iterator[bool]:
        if wait:
            with self.turns.hold(member_id):
                yield True
        else:
            with self.turns.try_hold(member_id) as got:
                if not got:
                    log.debug("%s is busy; their timers run on the next pass", member_id)
                yield got

    def _for_member(
        self, member_id: str, wait: bool, job: Callable[[], list[Outgoing]]
    ) -> list[Outgoing]:
        with self._member_turn(member_id, wait) as got:
            if not got:
                return []
            try:
                out = job()
            except Exception:  # one parent's problem must not stop the others
                log.exception("background job failed for %s", member_id)
                return []
            self.deliver(out)
            return out

    def tick(self, *, wait: bool = False) -> list[Outgoing]:
        """Timeouts (read-back expiry, no-repeat amber) and any undelivered family messages."""
        out: list[Outgoing] = []
        for member_id in self.engine.members_with_timeouts():
            out += self._for_member(
                member_id, wait, lambda m=member_id: self.engine.expire_stale(m)
            )
        return out + self.flush_outbox()

    def skip_time(self, member_id: str, minutes: float) -> list[Outgoing]:
        """Simulator demo button: pretend `minutes` passed for this parent."""
        out = self._for_member(member_id, True, lambda: self.engine.skip_time(member_id, minutes))
        return out + self.flush_outbox()

    def run_scheduler(self, now: datetime | None = None, *, wait: bool = False) -> list[Outgoing]:
        """One scheduler pass (reminders, follow-ups, missed doses, low stock, summary),
        each parent in their own turn."""
        now = now or utcnow()
        out: list[Outgoing] = []
        for parent in self.store.list_parents():
            if parent.consent_status != "granted":
                continue
            out += self._for_member(
                parent.id,
                wait,
                # re-read inside the turn: a STOP handled just before must win
                lambda pid=parent.id: self.scheduler.run_parent(self.store.get_member(pid), now),
            )
        return out + self.flush_outbox()

    def advance_time(self, minutes: float) -> list[Outgoing]:
        """Simulator demo: move the shared demo clock forward, running the timeouts and the
        scheduler every 5 simulated minutes so nothing is skipped."""
        remaining = timedelta(minutes=minutes)
        out: list[Outgoing] = []
        while remaining > timedelta(0):
            step = min(ADVANCE_STEP, remaining)
            clock.advance(self.store, step)
            remaining -= step
            out += self.tick(wait=True)
            out += self.run_scheduler(wait=True)
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
        out = self._for_member(
            parent_id,
            True,
            lambda: self.scheduler.weekly_summary(
                self.store.get_member(parent_id), utcnow(), force=True
            ),
        )
        return out + self.flush_outbox()

    # --- family messages: the outbox ---

    def flush_outbox(self, now: datetime | None = None) -> list[Outgoing]:
        """Send every due outbox message (family alerts, refill notices, summaries...).
        Only messages a channel accepted are marked delivered; the rest are retried.
        Returns the messages delivered by this call."""
        sent: list[Outgoing] = []
        for _ in range(100):  # bounded: failed rows are not due again in this call
            at = now or utcnow()
            rows = self.store.claim_outbox(at, OUTBOX_LEASE, limit=OUTBOX_BATCH)
            if not rows:
                break
            for row in rows:
                try:
                    msg = outbox_to_outgoing(row, self.store.get_member(row.member_id))
                except NotFoundError:
                    msg, result = None, Delivery(False, "member not found")
                else:
                    [result] = self.deliver([msg])
                if result.ok and msg is not None:
                    self.store.mark_outbox_delivered(row.id, at=at)
                    sent.append(msg)
                else:
                    self._delivery_failed(row, result.error or "unknown error", at)
        return sent

    def _delivery_failed(self, row: OutboxMessage, error: str, at: datetime) -> None:
        final = not row.retries_forever and row.attempts >= MAX_ATTEMPTS
        next_at = None if final else at + retry_delay(row.attempts)
        self.store.mark_outbox_failed(row.id, error, next_at, at=at)
        if not (final or row.level == "red"):
            log.warning(
                "outbox %s to %s failed (attempt %d), retry at %s: %s",
                row.id, row.member_id, row.attempts, next_at, error,
            )  # fmt: skip
            return
        log.error(
            "outbox %s (%s, level %s) to %s failed (attempt %d)%s: %s",
            row.id, row.kind, row.level, row.member_id, row.attempts,
            " - giving up" if final else f", retry at {next_at.isoformat()}", error,
        )  # fmt: skip
        self.store.add_audit(
            AuditEntry(
                actor="system",
                tool="delivery_failed",
                args_json=json.dumps(
                    {
                        "outbox_id": row.id,
                        "member_id": row.member_id,
                        "kind": row.kind,
                        "level": row.level,
                        "alert_id": row.alert_id,
                    }
                ),
                result_json=json.dumps(
                    {
                        "error": error,
                        "attempts": row.attempts,
                        "gave_up": final,
                        "next_attempt_at": next_at.isoformat() if next_at else None,
                    }
                ),
                parent_id=self._parent_of(row),
                created_at=at,
            )
        )

    def _parent_of(self, row: OutboxMessage) -> str | None:
        """Which parent an outbox message is about (for the audit log), if we can tell."""
        try:
            if row.alert_id:
                return self.store.get_alert(row.alert_id).parent_id
            member = self.store.get_member(row.member_id)
        except NotFoundError:
            return None
        if member.role == "parent":
            return member.id
        parents = self.store.list_members(member.family_id, role="parent")
        return parents[0].id if len(parents) == 1 else None

    # --- delivery: channels, voice notes and calls ---

    def _with_voice(self, msg: Outgoing) -> Outgoing:
        if msg.voice is None:
            return msg
        if self.tts is None:
            return msg.model_copy(update={"voice": None})  # voice off: text only
        try:
            url = self.tts.speak(msg.voice)
        except Exception:
            log.exception("TTS failed; sending text only")
            return msg.model_copy(update={"voice": None})
        if url is None and self.tts.name != "mock":
            return msg.model_copy(update={"voice": None})  # no key/budget/error: text only
        return msg.model_copy(update={"audio_url": url, "voice_mock": url is None})

    def _place_call(self, msg: Outgoing) -> str | None:
        """Ring a family member for a red alert. Returns an error, or None when done (or
        when this member cannot be called at all: nothing to retry)."""
        if self.calls is None or msg.member_id is None:
            return None
        member = self.store.get_member(msg.member_id)
        if not self.calls.can_call(member):
            log.info("no %s call for %s: not reachable by phone", self.calls.name, member.id)
            return None
        m = RED_ALERT.match(msg.text)
        parent, reason = (m["parent"], m["reason"]) if m else ("your family member", msg.text)
        reason = reason.replace(EMERGENCY_LINE, "").strip()  # that line is for the parent
        lang = phrases.lang_of(member.language)
        script = phrases.say("red_call", lang, parent=parent, reason=reason)
        url = self.tts.speak(script) if self.tts else None
        mock_voice = self.tts is not None and self.tts.name == "mock"
        try:
            placed = self.calls.call(member, script, url, mock_voice)
        except Exception as exc:
            log.exception("call to %s failed", member.id)
            return f"call failed: {type(exc).__name__}"
        return None if placed else "call not placed"

    def _send_one(self, msg: Outgoing) -> str | None:
        if msg.kind == "voice_call":
            return self._place_call(msg)
        channel = self.channels.get(msg.channel)
        if channel is None:
            return f"no {msg.channel} channel configured"
        msg = self._with_voice(msg)
        try:
            channel.send(msg)
        except Exception as exc:
            return f"{type(exc).__name__}: {exc}"[:300]
        return None

    def deliver(self, messages: list[Outgoing]) -> list[Delivery]:
        """Send each message now; returns one result per message (in order). Failures are
        logged here; outbox messages are retried by flush_outbox, direct replies are not."""
        results = []
        for msg in messages:
            error = self._send_one(msg)
            if error:
                log.warning("sending %s on %s to %s failed: %s", msg.kind, msg.channel,
                            msg.to_ref, error)  # fmt: skip
            results.append(Delivery(ok=error is None, error=error))
        return results


def build_service(settings: Settings | None = None) -> KhayalService:
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
    return KhayalService(
        store,
        get_reader(settings),
        agent,
        channels,
        mcp_url=settings.mcp_server_url,
        tts=get_tts(settings, store),
        stt=get_stt(settings),
        calls=get_call_provider(settings, simulator),
    )
