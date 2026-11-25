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
- Messages that carry a secret (dashboard sign-in codes, `sensitive=True`) expire with the
  code: never claimed after `expires_at`, never retried past it, never retried forever. Their
  text is blanked once delivered, expired or given up, and is kept out of logs and audit_log.
- Direct replies to the parent's own message are sent at once, inside the parent's turn
  (so replies keep their order), and are not retried.
- Inbound messages from Twilio / Telegram: written to the store inbox before the channel
  gets its answer (`accept`), then claimed with a lease and processed (`claim_inbox`,
  `process`; the worker is app/channels/inbound.py). Failures retry after 5 s, 30 s, 2 min
  and 10 min; after 5 attempts the message is failed, audited (tool=inbound_failed) and
  kept. At-least-once: a replayed message is handled from its saved starting point (see
  ConversationEngine.handle_message). The payload is blanked once done.
"""

import json
import logging
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial
from zoneinfo import ZoneInfo

from app import clock
from app.agent.rule_agent import RuleAgent
from app.alerts.thresholds import EMERGENCY_LINE
from app.channels.base import Channel, ChannelError
from app.channels.simulator import SimulatorChannel
from app.channels.turns import Ticket, Turns
from app.config import Settings, get_settings
from app.conversation.engine import ConversationEngine, Replay, outbox_to_outgoing
from app.conversation.messages import Outgoing
from app.demo import load_sample
from app.reader import get_reader
from app.reader.base import Reader
from app.scheduler import Scheduler
from app.storage import get_store
from app.storage.base import NotFoundError, Store
from app.storage.models import AuditEntry, InboxMessage, OutboxMessage, utcnow
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

# Inbox processing
INBOX_LEASE = timedelta(seconds=60)  # renewed while processing; a crashed worker's expires
INBOX_RETRY_DELAYS = (5, 30, 120, 600)  # seconds after attempt 1, 2, 3, 4
INBOX_MAX_ATTEMPTS = 5  # then failed (kept for inspection, audited)


def inbox_retry_delay(attempts: int) -> timedelta:
    """Wait before processing an inbox message again, after `attempts` failed attempts."""
    delays = INBOX_RETRY_DELAYS
    return timedelta(seconds=delays[min(max(attempts, 1), len(delays)) - 1])


def scrub_error(error: str) -> str:
    """An error kept in the inbox / audit log: no phone numbers or long digit runs."""
    return re.sub(r"\+?\d{7,}", "[number]", error)[:300]


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

    def accept(self, channel: str, event, external_id: str | None) -> InboxMessage | None:
        """Webhook / poller: write the message to the inbox before the channel gets its
        answer. The insert is the duplicate check: None means this id was seen before (a
        retry) and nothing new will be processed. A message without an id is stored too."""
        if external_id is None:
            log.warning("%s message without an id: stored; a resend would not be recognised",
                        channel)  # fmt: skip
        row = InboxMessage(
            channel=channel,
            external_id=external_id,
            payload={"event": event, "at": utcnow().isoformat()},  # app clock, for the engine
            received_at=clock.real_now(),
        )
        stored = self.store.add_inbox(row)
        if stored is None:
            log.info("duplicate %s message %s ignored", channel, external_id)
        return stored

    def claim_inbox(self, limit: int = 20) -> list[tuple[InboxMessage, Ticket]]:
        """Claim due inbox messages (oldest first) and take each sender's place in line in
        that order, so one parent's messages are handled in arrival order."""
        claimed = []
        for row in self.store.claim_inbox(clock.real_now(), INBOX_LEASE, limit):
            if not row.payload or "event" not in row.payload:
                self._inbox_failed(row, "no payload to process", final=True)
                continue
            try:
                ticket = self.reserve(row.channel, row.payload["event"])
            except Exception as exc:  # a malformed event: retried, then failed
                self._inbox_failed(row, f"{type(exc).__name__}: {exc}")
                continue
            claimed.append((row, ticket))
        return claimed

    def process(self, row: InboxMessage, ticket: Ticket) -> bool:
        """One processing attempt of a claimed inbox message (in the sender's turn). True
        when done; on an error it is retried later or, after 5 attempts, failed."""
        assert row.payload is not None
        replay = Replay(row.id, row.snapshot, partial(self.store.save_inbox_snapshot, row.id))
        try:
            at = datetime.fromisoformat(row.payload["at"])
            self.handle(row.channel, row.payload["event"], ticket, replay=replay, at=at)
        except Exception as exc:
            if not isinstance(exc, ChannelError):
                log.exception("inbound %s (%s) failed", row.id, row.channel)
            self._inbox_failed(row, f"{type(exc).__name__}: {exc}")
            return False
        try:
            self.store.finish_inbox(row.id, clock.real_now())
        except Exception:  # its lease runs out and it is replayed safely
            log.exception("inbound %s processed but not marked done", row.id)
            return False
        return True

    def run_inbox(self) -> int:
        """Process every due inbox message now, one at a time in this thread (the Telegram
        poller's `poll_once`, tests, scripts). Returns how many were attempted."""
        count = 0
        while batch := self.claim_inbox():
            for row, ticket in batch:
                self.process(row, ticket)
                count += 1
        return count

    def _inbox_failed(self, row: InboxMessage, error: str, *, final: bool = False) -> None:
        at = clock.real_now()
        error = scrub_error(error)
        final = final or row.attempts >= INBOX_MAX_ATTEMPTS
        retry_at = None if final else at + inbox_retry_delay(row.attempts)
        self.store.fail_inbox(row.id, error, retry_at, at)
        if retry_at is not None:
            log.warning("inbound %s (%s) failed (attempt %d), retry at %s: %s",
                        row.id, row.channel, row.attempts, retry_at.isoformat(), error)  # fmt: skip
            return
        log.error("inbound %s (%s) failed for good after %d attempts: %s",
                  row.id, row.channel, row.attempts, error)  # fmt: skip
        event = (row.payload or {}).get("event")
        member = self._sender(row.channel, event) if event is not None else None
        self.store.add_audit(
            AuditEntry(
                actor="system",
                tool="inbound_failed",
                args_json=json.dumps(
                    {"inbox_id": row.id, "channel": row.channel, "external_id": row.external_id}
                ),
                result_json=json.dumps({"error": error, "attempts": row.attempts}),
                parent_id=member.id if member and member.role == "parent" else None,
                created_at=utcnow(),
            )
        )

    def _sender(self, channel: str, event):
        adapter = self.channels.get(channel)
        try:
            ref = adapter.sender_of(event) if adapter else None
        except Exception:
            return None
        return self.store.find_member_by_channel(channel, ref) if ref else None

    def handle(
        self,
        channel: str,
        event,
        ticket: Ticket | None = None,
        *,
        replay: Replay | None = None,
        at: datetime | None = None,
    ) -> list[Outgoing]:
        """An inbound event from a channel: reply to the sender, notify the family.
        `replay`/`at`: an inbox message (may be handled again) and when it arrived."""
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
            if at is not None:
                incoming = incoming.model_copy(update={"received_at": at})
            out = self.engine.handle_message(incoming, replay=replay)
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
            out += self._for_member(member_id, wait, partial(self.engine.expire_stale, member_id))
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
            out += self._for_member(parent.id, wait, partial(self._scheduler_job, parent.id, now))
        return out + self.flush_outbox()

    def _scheduler_job(self, parent_id: str, now: datetime) -> list[Outgoing]:
        # re-read inside the turn: a STOP handled just before must win
        return self.scheduler.run_parent(self.store.get_member(parent_id), now)

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
        """Send every due outbox message (family alerts, refill notices, summaries, sign-in
        codes...). Only messages a channel accepted are marked delivered; the rest are
        retried. Returns the messages delivered by this call."""
        sent: list[Outgoing] = []
        for row in self.store.expire_outbox(now or utcnow()):
            log.warning("outbox %s (%s) to %s expired undelivered", row.id, row.kind,
                        row.member_id)  # fmt: skip
        for _ in range(100):  # bounded: failed rows are not due again in this call
            at = now or utcnow()
            rows = self.store.claim_outbox(at, OUTBOX_LEASE, limit=OUTBOX_BATCH)
            if not rows:
                break
            for row in rows:
                try:
                    msg = outbox_to_outgoing(row, self.store.get_member(row.member_id))
                except NotFoundError:
                    self._delivery_failed(row, "member not found", at)
                    continue
                [result] = self.deliver([msg], secret=row.text if row.sensitive else None)
                if result.ok:
                    self.store.mark_outbox_delivered(row.id, at=at)
                    sent.append(msg)
                else:
                    self._delivery_failed(row, result.error or "unknown error", at)
        return sent

    def _delivery_failed(self, row: OutboxMessage, error: str, at: datetime) -> None:
        retry_at = at + retry_delay(row.attempts)
        final = not row.retries_forever and (
            row.attempts >= MAX_ATTEMPTS
            or (row.expires_at is not None and retry_at >= row.expires_at)  # never past expiry
        )
        self.store.mark_outbox_failed(row.id, error, None if final else retry_at, at=at)
        if not (final or row.level == "red"):
            log.warning(
                "outbox %s to %s failed (attempt %d), retry at %s: %s",
                row.id, row.member_id, row.attempts, retry_at, error,
            )  # fmt: skip
            return
        log.error(
            "outbox %s (%s, level %s) to %s failed (attempt %d)%s: %s",
            row.id, row.kind, row.level, row.member_id, row.attempts,
            " - giving up" if final else f", retry at {retry_at.isoformat()}", error,
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
                        "next_attempt_at": None if final else retry_at.isoformat(),
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

    def deliver(self, messages: list[Outgoing], *, secret: str | None = None) -> list[Delivery]:
        """Send each message now; returns one result per message (in order). Failures are
        logged here; outbox messages are retried by flush_outbox, direct replies are not.
        `secret`: text that must not reach logs or audit_log (scrubbed from errors)."""
        results = []
        for msg in messages:
            error = self._send_one(msg)
            if error and secret is not None:
                error = scrub_secret(error, secret)
            if error:
                # member id, not the address: phone numbers stay out of logs
                log.warning("sending %s on %s to %s failed: %s", msg.kind, msg.channel,
                            msg.member_id or "an unregistered sender", error)  # fmt: skip
            results.append(Delivery(ok=error is None, error=error))
        return results


def scrub_secret(error: str, secret: str) -> str:
    """A channel error may echo the message body: drop it and any 4+ digit run (a code)."""
    if secret:
        error = error.replace(secret, "[message]")
    return re.sub(r"\d{4,}", "[digits]", error)


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
    agent = RuleAgent(store, settings.mcp_server_url, secret=settings.mcp_shared_secret)
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
