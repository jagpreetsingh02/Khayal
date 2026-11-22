"""Read-back conversation state machine (brief §5.3), one state per parent, channel-agnostic.

    idle --photo--> awaiting_confirmation --1--> confirmed -> agent acts
                                          --2--> awaiting_value --photo/typed--> (read back again)
                    (30 min without an answer -> reading expired, back to idle)
    first BP at/above 180/120 confirmed -> awaiting_repeat (repeat due after 5 min rest)
    medicine strip --> awaiting_med_confirmation --1--> reminder at 8 PM
                                                 --2--> awaiting_med_time --"21:30"--> reminder

Rules that hold on every path:
- Consent first (§6.5): until the parent agrees, only the consent notice is sent and photos
  are not read. STOP pauses at any time.
- Nothing is confirmed and no alert fires before the parent's "1". Confirmation is written
  here, server-side, via the store; no MCP tool confirms a reading.
- `parent_id` always comes from the sender mapping in the store, never from model output.
- Photos are read and discarded; only the numbers are stored.
"""

import logging
import math
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from app.agent.minimal import Agent, MinimalAgent
from app.alerts.notifier import OutboxNotifier
from app.alerts.thresholds import BP_REPEAT_MAX, BP_REPEAT_MIN
from app.conversation import parse
from app.conversation import templates as tpl
from app.conversation.messages import Incoming, Outgoing
from app.reader.base import Reader, ReaderError
from app.reader.intake import build_reading
from app.reader.schema import ImageInput
from app.reader.strips import match_strip
from app.storage.base import Store
from app.storage.models import ConversationState, Medicine, Member, ReadingType, utcnow

log = logging.getLogger(__name__)

IDLE = "idle"
AWAITING_CONFIRMATION = "awaiting_confirmation"
AWAITING_VALUE = "awaiting_value"
AWAITING_REPEAT = "awaiting_repeat"
AWAITING_MED_CONFIRMATION = "awaiting_med_confirmation"
AWAITING_MED_TIME = "awaiting_med_time"

ANSWER_TIMEOUT = timedelta(minutes=30)
DOSE_REPLY_WINDOW = timedelta(hours=3)
DEFAULT_REMINDER = "20:00"


def _iso(ts: datetime) -> str:
    return ts.isoformat()


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


class ConversationEngine:
    def __init__(
        self,
        store: Store,
        reader: Reader,
        agent: Agent | None = None,
        *,
        outbox: OutboxNotifier | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.store = store
        self.reader = reader
        self.outbox = outbox or OutboxNotifier()  # family messages sent by MCP tools
        self.agent = agent or MinimalAgent(store, self.outbox)
        self.clock = clock

    # --- public API ---

    def handle_message(self, msg: Incoming) -> list[Outgoing]:
        """One incoming message -> replies to the parent plus any family notifications."""
        now = msg.received_at or self.clock()
        member = self.store.find_member_by_channel(msg.channel, msg.sender_ref)
        if member is None:
            reply = Outgoing(
                channel=msg.channel, to_ref=msg.sender_ref, member_id=None, text=tpl.UNKNOWN_SENDER
            )
            return [reply]
        if member.role != "parent":
            return [self._to(member, tpl.NOT_A_PARENT)]
        texts = self._handle_parent(member, msg, now)
        return [self._to(member, t) for t in texts] + self._drain_family()

    def expire_stale(self, member_id: str, now: datetime | None = None) -> list[Outgoing]:
        """Apply timeouts without an incoming message (for the scheduler, Phase 5)."""
        member = self.store.get_member(member_id)
        texts = self._expire(member, now or self.clock())
        return [self._to(member, t) for t in texts]

    def dose_reminder(self, member_id: str, medicine_ids: list[str]) -> Outgoing:
        """The reminder text the scheduler sends (Phase 5); replies are handled here."""
        member = self.store.get_member(member_id)
        names = [self.store.get_medicine(m).brand for m in medicine_ids]
        return self._to(member, tpl.DOSE_REMINDER.format(names=tpl.join_names(names)))

    # --- consent, stop, timeouts ---

    def _handle_parent(self, parent: Member, msg: Incoming, now: datetime) -> list[str]:
        t = parse.normalize(msg.text)
        if parent.consent_status in ("pending", "revoked"):
            if msg.image is None and parse.is_yes(t):
                self.store.set_consent(
                    parent.id, "granted", text_version=tpl.CONSENT_TEXT_VERSION, at=now
                )
                return [tpl.CONSENT_THANKS]
            return [tpl.CONSENT_NOTICE.format(name=parent.name)]
        if parent.consent_status == "paused":
            if msg.image is None and t in parse.START:
                self.store.set_consent(parent.id, "granted", at=now)
                return [tpl.RESUMED]
            return [tpl.PAUSED]
        if msg.image is None and t in parse.STOP:
            self._discard_pending(parent)
            self.store.set_consent(parent.id, "paused", at=now)
            return [tpl.PAUSED_NOW]

        out = self._expire(parent, now)
        state = self.store.get_conversation_state(parent.id)
        return out + self._route(parent, state, msg, t, now)

    def _expire(self, parent: Member, now: datetime) -> list[str]:
        state = self.store.get_conversation_state(parent.id)
        p = dict(state.payload)
        repeat = p.get("repeat")
        changed, out = False, []
        if repeat and now > _ts(repeat["expires_at"]):
            repeat, changed = None, True
        expires = p.get("expires_at")
        if expires and now > _ts(expires):
            if state.state == AWAITING_CONFIRMATION:
                self.store.set_reading_status(p["reading_id"], "expired", at=now)
                out.append(tpl.EXPIRED.format(value=p["value"]))
            self._rest(parent, repeat)
        elif changed:
            p.pop("repeat", None)
            self._save(parent, state.state if state.state != AWAITING_REPEAT else IDLE, p, None)
        return out

    def _discard_pending(self, parent: Member) -> None:
        state = self.store.get_conversation_state(parent.id)
        if state.state == AWAITING_CONFIRMATION:
            self.store.set_reading_status(state.payload["reading_id"], "expired")
        self._save(parent, IDLE, {}, None)

    # --- routing ---

    def _route(
        self, parent: Member, state: ConversationState, msg: Incoming, t: str, now: datetime
    ) -> list[str]:
        p = state.payload
        repeat = p.get("repeat")
        if msg.image is not None:
            if state.state == AWAITING_CONFIRMATION:  # a new photo replaces the old reading
                self.store.set_reading_status(p["reading_id"], "rejected", at=now)
            return self._photo(parent, repeat, msg.image, t, now)

        if state.state == AWAITING_CONFIRMATION:
            if parse.is_yes(t):
                return self._confirm_reading(parent, p, now)
            if parse.is_no(t):
                return self._reject_reading(parent, p, now)
            typed = parse.parse_typed_reading(t)
            if typed:
                self.store.set_reading_status(p["reading_id"], "rejected", at=now)
                return self._typed(parent, repeat, typed, now)
            return [tpl.PLEASE_ANSWER.format(value=p["value"])]

        if state.state == AWAITING_MED_CONFIRMATION:
            if parse.is_yes(t):
                return self._confirm_medicine(parent, p, [DEFAULT_REMINDER])
            if parse.is_no(t):
                self._save(parent, AWAITING_MED_TIME, self._med_payload(p, now), repeat)
                return [tpl.STRIP_ASK_TIME]
            return [tpl.STRIP_PLEASE_ANSWER.format(name=p["name"])]

        if state.state == AWAITING_MED_TIME:
            if parse.is_no(t):
                self._rest(parent, repeat)
                return [tpl.NO_REMINDER]
            hhmm = parse.parse_time(t)
            if hhmm:
                return self._confirm_medicine(parent, p, [hhmm])
            return [tpl.STRIP_TIME_FORMAT]

        glucose_asked = state.state == AWAITING_VALUE and p.get("type") == "glucose"
        typed = parse.parse_typed_reading(t, bare_number_is_glucose=glucose_asked)
        if typed:
            return self._typed(parent, repeat, typed, now)
        return self._general(parent, t, now)

    def _general(self, parent: Member, t: str, now: datetime) -> list[str]:
        if parse.is_yes(t) or parse.is_no(t) or t in parse.TAKEN:
            events = [
                e
                for e in self.store.list_dose_events(
                    parent.id, since=now - DOSE_REPLY_WINDOW, until=now + timedelta(minutes=15)
                )
                if e.status == "pending"
            ]
            if not events:
                return [tpl.NOTHING_TO_CONFIRM]
            status = "missed" if parse.is_no(t) else "taken"
            for e in events:
                self.store.set_dose_status(e.id, status, at=now)
            names = sorted({self.store.get_medicine(e.medicine_id).brand for e in events})
            template = tpl.DOSE_SKIPPED if status == "missed" else tpl.DOSE_TAKEN
            return [template.format(names=tpl.join_names(names))]
        if t in parse.HELP:
            return [tpl.HELP]
        return [tpl.OFF_TOPIC]

    # --- readings ---

    def _photo(
        self, parent: Member, repeat: dict | None, image: ImageInput, t: str, now: datetime
    ) -> list[str]:
        if parse.mentions_strip(t):
            return self._strip(parent, repeat, image, now)
        try:
            meter = self.reader.read_meter(image)
        except ReaderError:
            log.warning("reader failed for parent %s", parent.id, exc_info=True)
            self._rest(parent, repeat)
            return [tpl.READ_FAILED]
        if meter.device_type == "unknown" and meter.legible:
            return self._strip(parent, repeat, image, now)  # maybe a medicine strip
        if meter.device_type == "unknown" or not meter.legible:
            self._rest(parent, repeat)
            return [tpl.retake_message(list(meter.issues))]
        shown = None
        if meter.glucose_unit == "mmol/L" and meter.glucose_value is not None:
            shown = f"{meter.glucose_value:g} mmol/L"
        return self._new_reading(
            parent,
            repeat,
            meter.device_type,
            dict(
                systolic=meter.systolic,
                diastolic=meter.diastolic,
                pulse=meter.pulse,
                glucose_value=meter.glucose_value,
                glucose_unit=meter.glucose_unit,
            ),
            source="photo",
            now=now,
            shown=shown,
        )

    def _typed(
        self, parent: Member, repeat: dict | None, typed: parse.TypedReading, now: datetime
    ) -> list[str]:
        values = typed.model_dump(exclude={"type"})
        shown = None
        if typed.glucose_unit == "mmol/L" and typed.glucose_value is not None:
            shown = f"{typed.glucose_value:g} mmol/L"
        return self._new_reading(
            parent, repeat, typed.type, values, source="manual", now=now, shown=shown
        )

    def _new_reading(
        self,
        parent: Member,
        repeat: dict | None,
        type: ReadingType,
        values: dict[str, Any],
        *,
        source: str,
        now: datetime,
        shown: str | None = None,
    ) -> list[str]:
        is_repeat = repeat is not None and type == "bp"
        if repeat is not None and is_repeat and now < _ts(repeat["due_at"]):
            minutes = math.ceil((_ts(repeat["due_at"]) - now).total_seconds() / 60)
            self._rest(parent, repeat)
            return [tpl.REPEAT_TOO_SOON.format(minutes=minutes)]
        reading, check = build_reading(
            self.store, parent.id, type, source=source, now=now, **values
        )
        if reading is None:
            log.info("implausible reading for parent %s: %s", parent.id, check.problems)
            self._rest(parent, repeat)
            return [tpl.retake_message([], prefix=tpl.IMPLAUSIBLE)]
        self.store.add_reading(reading)
        value = tpl.format_value(reading, shown)
        payload = {
            "reading_id": reading.id,
            "value": value,
            "is_repeat": is_repeat,
            "expires_at": _iso(now + ANSWER_TIMEOUT),
        }
        self._save(parent, AWAITING_CONFIRMATION, payload, repeat)
        return [tpl.read_back(value, unusual=bool(check.flags))]

    def _confirm_reading(self, parent: Member, p: dict, now: datetime) -> list[str]:
        reading = self.store.set_reading_status(p["reading_id"], "confirmed", at=now)
        outcome = self.agent.after_confirmed_reading(parent, reading)
        value = p["value"]
        if outcome.ask_repeat:  # first reading at/above the severe limit
            repeat = {
                "first_reading_id": reading.id,
                "due_at": _iso(reading.created_at + BP_REPEAT_MIN),
                "expires_at": _iso(reading.created_at + BP_REPEAT_MAX),
            }
            self._save(parent, AWAITING_REPEAT, {}, repeat)
            return [tpl.REPEAT_ASK.format(value=value)]

        repeat = None if p.get("is_repeat") else p.get("repeat")
        self._rest(parent, repeat)
        if outcome.alert_sent:
            family = "I have told your family."
        else:
            family = "I could not reach your family just now."

        if outcome.level == "red":
            lead = tpl.RED_REPEAT if outcome.rule == "bp_severe_repeat" else tpl.SAVED
            return [f"{lead.format(value=value)} {family} {tpl.EMERGENCY}"]
        if outcome.level == "amber":
            template = tpl.FAMILY_TOLD if outcome.alert_sent else tpl.FAMILY_NOT_REACHED
            return [template.format(value=value)]
        if p.get("is_repeat"):
            return [tpl.REPEAT_OK.format(value=value)]
        return [tpl.SAVED.format(value=value)]

    def _reject_reading(self, parent: Member, p: dict, now: datetime) -> list[str]:
        reading = self.store.set_reading_status(p["reading_id"], "rejected", at=now)
        payload = {"type": reading.type, "expires_at": _iso(now + ANSWER_TIMEOUT)}
        self._save(parent, AWAITING_VALUE, payload, p.get("repeat"))
        return [tpl.REJECTED]

    # --- medicine strips ---

    def _strip(
        self, parent: Member, repeat: dict | None, image: ImageInput, now: datetime
    ) -> list[str]:
        try:
            strip = self.reader.read_strip(image)
        except ReaderError:
            log.warning("strip reader failed for parent %s", parent.id, exc_info=True)
            self._rest(parent, repeat)
            return [tpl.READ_FAILED]
        if not strip.legible or not strip.brand:
            self._rest(parent, repeat)
            return [tpl.STRIP_UNREADABLE]
        match = match_strip(strip.brand, strip.strength)
        if match is None:
            self._rest(parent, repeat)
            seen = " ".join(x for x in (strip.brand, strip.strength) if x)
            return [tpl.STRIP_UNKNOWN.format(seen=seen)]
        row = match.row
        payload = {
            "name": row.display_name,
            "generic": row.generic,
            "strength": row.generic_strength,
            "label": row.generic_label,
        }
        self._save(parent, AWAITING_MED_CONFIRMATION, self._med_payload(payload, now), repeat)
        return [tpl.STRIP_READ_BACK.format(name=row.display_name, generic=row.generic_label)]

    @staticmethod
    def _med_payload(p: dict, now: datetime) -> dict:
        keep = {k: p[k] for k in ("name", "generic", "strength", "label")}
        return keep | {"expires_at": _iso(now + ANSWER_TIMEOUT)}

    def _confirm_medicine(self, parent: Member, p: dict, times: list[str]) -> list[str]:
        name = p["name"]
        med = next(
            (m for m in self.store.list_medicines(parent.id) if m.brand.lower() == name.lower()),
            None,
        )
        if med is None:
            med = self.store.add_medicine(
                Medicine(
                    parent_id=parent.id,
                    brand=name,
                    generic=p["generic"],
                    strength=p["strength"],
                    confirmed=True,
                )
            )
        elif not med.confirmed:
            med = self.store.confirm_medicine(med.id)
        self._rest(parent, p.get("repeat"))
        try:
            final = self.agent.set_reminder(parent, med.id, sorted(set(med.times) | set(times)))
        except Exception:
            log.exception("create_reminder failed for medicine %s", med.id)
            return [tpl.REMINDER_FAILED.format(name=name)]
        when = tpl.join_names([tpl.clock_12h(x) for x in final])
        return [tpl.REMINDER_SET.format(name=name, times=when)]

    # --- helpers ---

    def _save(self, parent: Member, state: str, payload: dict, repeat: dict | None) -> None:
        payload = {k: v for k, v in payload.items() if k != "repeat"}
        if repeat:
            payload["repeat"] = repeat
        self.store.set_conversation_state(
            ConversationState(member_id=parent.id, state=state, payload=payload)
        )

    def _rest(self, parent: Member, repeat: dict | None) -> None:
        """Nothing waiting for an answer: idle, or still waiting for a repeat BP reading."""
        self._save(parent, AWAITING_REPEAT if repeat else IDLE, {}, repeat)

    def _to(self, member: Member, text: str) -> Outgoing:
        return Outgoing(
            channel=member.channel, to_ref=member.channel_ref, member_id=member.id, text=text
        )

    def _drain_family(self) -> list[Outgoing]:
        sent, self.outbox.outbox[:] = list(self.outbox.outbox), []
        out = []
        for m in sent:
            member = self.store.get_member(m.member_id)
            out.append(
                Outgoing(
                    channel=member.channel,
                    to_ref=member.channel_ref,
                    member_id=member.id,
                    text=m.text,
                    kind=m.kind,
                )
            )
        return out
