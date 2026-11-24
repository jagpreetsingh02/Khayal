"""Read-back conversation state machine (brief §5.3), one state per parent, channel-agnostic.

    idle --photo--> awaiting_confirmation --1--> confirmed -> agent acts
                                          --2--> awaiting_value --photo/typed--> (read back again)
                    (30 min without an answer -> reading expired, back to idle)
    BP at/above 180/120 confirmed -> awaiting_symptoms (2025 AHA/ACC): one yes/no warning-signs
        question before anything else. 1 = RED to family at once + "call 108/112".
        2 (or no reply in 5 min, audited) -> awaiting_repeat: rest 5 minutes and repeat.
        Confirmed repeat still severe -> the question again; 2 / no reply -> RED "urgent:
        contact the doctor today". No confirmed repeat 15 minutes after the first -> amber.
    glucose below 70 confirmed (ADA 2026) -> family alert (< 54 red, 54-69 amber), the
        doctor's own low-sugar plan, a request to recheck in 15 minutes, then
        awaiting_symptoms: "did you faint / feel confused / need help?" 1 = RED.
        54-69 with no confirmed recheck in 15 minutes -> amber "no recheck" (tick()).
    medicine strip --> awaiting_med_confirmation --1--> reminder at 8 PM
                                                 --2--> awaiting_med_time --"21:30"--> reminder

Rules that hold on every path:
- Consent first (§6.5): until the parent agrees, only the consent notice is sent and photos
  are not read. STOP pauses at any time; while paused or revoked nothing is logged,
  alerted or reminded (the MCP tools refuse such parents as well).
- Nothing is confirmed and no alert fires before the parent's "1". Confirmation is written
  here, server-side, via the store; no MCP tool confirms a reading.
- `parent_id` always comes from the sender mapping in the store, never from model output.
- Photos are read and discarded; only the numbers are stored.
"""

import json
import logging
import math
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from app.agent.rule_agent import Agent
from app.alerts.evaluate import evaluate_confirmed_reading
from app.alerts.thresholds import (
    BP_REPEAT_MAX,
    BP_REPEAT_MIN,
    BP_REPEAT_WAIT,
    GLUCOSE_RECHECK_WAIT,
    SYMPTOM_WAIT,
)
from app.conversation import parse
from app.conversation import templates as tpl
from app.conversation.messages import Incoming, Outgoing, Voice
from app.reader.base import Reader, ReaderError
from app.reader.intake import build_reading
from app.reader.schema import UNKNOWN_MEDICINE, ImageInput, StripCandidate
from app.reader.strips import catalog_id, catalog_label, find_catalog, load_brands
from app.storage.base import Store
from app.storage.models import (
    AuditEntry,
    ConversationState,
    DoseStatus,
    Medicine,
    Member,
    OutboxMessage,
    ReadingSource,
    ReadingType,
    SymptomAnswer,
    utcnow,
)
from app.voice import phrases

log = logging.getLogger(__name__)

IDLE = "idle"
AWAITING_CONFIRMATION = "awaiting_confirmation"
AWAITING_VALUE = "awaiting_value"
AWAITING_REPEAT = "awaiting_repeat"
AWAITING_SYMPTOMS = "awaiting_symptoms"
AWAITING_MED_CONFIRMATION = "awaiting_med_confirmation"
AWAITING_MED_TIME = "awaiting_med_time"

ANSWER_TIMEOUT = timedelta(minutes=30)
DOSE_REPLY_WINDOW = timedelta(hours=3)
DEFAULT_REMINDER = "20:00"
# Timers that outlive a single question: the repeat BP and the glucose recheck.
TIMER_KEYS = ("repeat", "recheck")
LOW_GLUCOSE_RULES = ("glucose_red_below", "glucose_amber_below")


def _timers(payload: dict) -> dict:
    return {k: payload[k] for k in TIMER_KEYS if payload.get(k)}


class Said(str):
    """A reply text plus the short phrase to speak if the parent used a voice note."""

    voice: Voice | None

    def __new__(cls, text: str, voice: Voice | None = None) -> "Said":
        obj = super().__new__(cls, text)
        obj.voice = voice
        return obj


def _iso(ts: datetime) -> str:
    return ts.isoformat()


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


class ConversationEngine:
    def __init__(
        self,
        store: Store,
        reader: Reader,
        agent: Agent,
        *,
        clock: Callable[[], datetime] = utcnow,
        drain_family: bool = True,
    ) -> None:
        self.store = store
        self.reader = reader
        self.agent = agent  # calls the MCP tools; family messages come back via store outbox
        self.clock = clock
        # Standalone (tests, the CLI chat demo): family messages from the outbox are returned
        # with the replies. Inside KhayalService (drain_family=False) the service claims,
        # sends and retries them itself, so a failed send is never lost.
        self.drain_family = drain_family

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
        if msg.via_voice and not (msg.text or "").strip() and msg.image is None:
            lang = phrases.lang_of(member.language)
            said = Said(tpl.VOICE_UNCLEAR, phrases.say("not_understood", lang))
            return [self._to(member, said, said.voice)]
        texts = self._handle_parent(member, msg, now)
        return [self._to(member, t, self._reply_voice(member, msg, t)) for t in texts] + (
            self._family()
        )

    @staticmethod
    def _reply_voice(member: Member, msg: Incoming, text: str) -> Voice | None:
        """Answer a voice note with a voice note: the matching phrase in the parent's
        language, or the reply itself for English speakers. Text-only otherwise."""
        if not msg.via_voice:
            return None
        voice = getattr(text, "voice", None)
        if voice is not None:
            return voice
        if phrases.lang_of(member.language) == "en":
            return Voice(text=str(text), lang="en")
        return None

    def expire_stale(self, member_id: str, now: datetime | None = None) -> list[Outgoing]:
        """Apply timeouts for one parent without an incoming message."""
        member = self.store.get_member(member_id)
        if member.consent_status != "granted":
            return []
        texts = self._due(member, now or self.clock())
        return [self._to(member, t) for t in texts] + self._family()

    def members_with_timeouts(self) -> list[str]:
        """Parents with a read-back or a repeat-BP wait that can time out."""
        return [
            s.member_id
            for s in self.store.list_conversation_states()
            if s.payload.get("expires_at") or _timers(s.payload)
        ]

    def tick(self, now: datetime | None = None) -> list[Outgoing]:
        """Timeouts for every parent (run every ~30 s): unanswered read-backs expire, and a
        first severe BP without a repeat after 15 minutes sends amber to the family."""
        now = now or self.clock()
        out: list[Outgoing] = []
        for member_id in self.members_with_timeouts():
            out += self.expire_stale(member_id, now)
        return out + self._family()

    def dose_reminder(
        self, member_id: str, medicine_ids: list[str], hhmm: str | None = None
    ) -> Outgoing | None:
        """The reminder the scheduler sends at a medicine time (with a voice note); replies
        are handled here. None when the parent has not agreed or has paused (§6.5)."""
        return self._dose_message(member_id, medicine_ids, hhmm, tpl.DOSE_REMINDER, "dose_reminder")

    def dose_followup(self, member_id: str, medicine_ids: list[str], hhmm: str) -> Outgoing | None:
        """The one gentle follow-up 30 minutes after a reminder without a "taken" reply."""
        return self._dose_message(member_id, medicine_ids, hhmm, tpl.DOSE_FOLLOWUP, "dose_followup")

    def _dose_message(
        self, member_id: str, medicine_ids: list[str], hhmm: str | None, template: str, phrase: str
    ) -> Outgoing | None:
        member = self.store.get_member(member_id)
        if member.consent_status != "granted":
            return None
        names = tpl.join_names([self.store.get_medicine(m).brand for m in medicine_ids])
        lang = phrases.lang_of(member.language)
        if hhmm:
            text = template.format(time=tpl.clock_12h(hhmm), names=names)
        else:  # no slot given (tests / manual use)
            text = template.split(": ", 1)[-1].format(names=names).capitalize()
        voice = phrases.say(
            phrase, lang, names=names, time=phrases.time_words(hhmm or "00:00", lang)
        )
        return self._to(member, text, voice if hhmm else None)

    def skip_time(self, member_id: str, minutes: float) -> list[Outgoing]:
        """Simulator demo only: move this parent's recent data `minutes` into the past, as
        if that much time had passed, then run the timeouts."""
        delta = timedelta(minutes=minutes)
        self.store.shift_parent_times(member_id, -delta)
        state = self.store.get_conversation_state(member_id)

        def shift(p: dict) -> dict:
            out = {}
            for k, v in p.items():
                if isinstance(v, dict):
                    v = shift(v)
                elif k.endswith("_at") and isinstance(v, str):
                    v = _iso(_ts(v) - delta)
                out[k] = v
            return out

        self.store.set_conversation_state(
            state.model_copy(update={"payload": shift(state.payload)})
        )
        return self.expire_stale(member_id)  # this parent only; others keep their own timing

    # --- consent, stop, timeouts ---

    def _handle_parent(self, parent: Member, msg: Incoming, now: datetime) -> list[str]:
        t = parse.normalize_speech(msg.text) if msg.via_voice else parse.normalize(msg.text)
        source: ReadingSource = "voice" if msg.via_voice else "manual"
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

        out = self._due(parent, now)
        state = self.store.get_conversation_state(parent.id)
        return out + self._route(parent, state, msg, t, now, source)

    def _due(self, parent: Member, now: datetime) -> list[str]:
        return (
            self._symptom_timeout(parent, now)
            + self._missed_repeat(parent, now)
            + self._missed_recheck(parent, now)
            + self._expire(parent, now)
        )

    def _symptom_timeout(self, parent: Member, now: datetime) -> list[str]:
        """No answer to the warning-signs question in 5 minutes: carry on as if "no" (rest
        and repeat), and note it in the audit log."""
        state = self.store.get_conversation_state(parent.id)
        if state.state != AWAITING_SYMPTOMS or now <= _ts(state.payload["expires_at"]):
            return []
        return self._symptom_answer(parent, state.payload, "no_reply", now)

    def _missed_repeat(self, parent: Member, now: datetime) -> list[str]:
        """15 minutes after a first severe BP without a confirmed repeat: amber to family."""
        state = self.store.get_conversation_state(parent.id)
        timers = _timers(state.payload)
        repeat = timers.get("repeat")
        if not repeat or repeat.get("amber_done") or now < _ts(repeat["amber_at"]):
            return []
        p = state.payload
        reading_back = state.state == AWAITING_CONFIRMATION and p.get("is_repeat")
        if reading_back and p.get("type", "bp") == "bp":
            return []  # the repeat is being read back; it decides
        reading = self.store.get_reading(repeat["first_reading_id"])
        outcome = self.agent.after_missed_repeat(parent, reading)
        timers["repeat"] = repeat | {"amber_done": True}
        self._save(parent, state.state, state.payload, timers)
        template = tpl.NO_REPEAT_FAMILY_TOLD if outcome.alert_sent else tpl.NO_REPEAT_NOT_REACHED
        if outcome.not_shared:
            template = tpl.NO_REPEAT_NOT_SHARED
        return [template.format(value=repeat.get("value", ""))]

    def _missed_recheck(self, parent: Member, now: datetime) -> list[str]:
        """15 minutes after a low (54-69) sugar without a confirmed recheck: amber to family."""
        state = self.store.get_conversation_state(parent.id)
        timers = _timers(state.payload)
        recheck = timers.get("recheck")
        if not recheck or recheck.get("alert_done") or now < _ts(recheck["alert_at"]):
            return []
        p = state.payload
        reading_back = state.state == AWAITING_CONFIRMATION and p.get("is_repeat")
        if reading_back and p.get("type") == "glucose":
            return []  # the recheck is being read back; it decides
        reading = self.store.get_reading(recheck["first_reading_id"])
        outcome = self.agent.after_missed_recheck(parent, reading)
        timers["recheck"] = recheck | {"alert_done": True}
        self._save(parent, state.state, state.payload, timers)
        template = tpl.NO_RECHECK_FAMILY_TOLD if outcome.alert_sent else tpl.NO_RECHECK_NOT_REACHED
        if outcome.not_shared:
            template = tpl.NO_RECHECK_NOT_SHARED
        return [template.format(value=recheck.get("value", ""))]

    def _expire(self, parent: Member, now: datetime) -> list[str]:
        state = self.store.get_conversation_state(parent.id)
        p = dict(state.payload)
        timers = _timers(p)
        changed, out = False, []
        for key, timer in list(timers.items()):
            if now > _ts(timer["expires_at"]):
                timers.pop(key)
                changed = True
        expires = p.get("expires_at")
        if expires and now > _ts(expires) and state.state != AWAITING_SYMPTOMS:
            if state.state == AWAITING_CONFIRMATION:
                self.store.set_reading_status(p["reading_id"], "expired", at=now)
                out.append(tpl.EXPIRED.format(value=p["value"]))
            self._rest(parent, timers)
        elif changed:
            new_state = state.state
            if new_state == AWAITING_REPEAT and not timers.get("repeat"):
                new_state = IDLE
            self._save(parent, new_state, p, timers)
        return out

    def _discard_pending(self, parent: Member) -> None:
        state = self.store.get_conversation_state(parent.id)
        if state.state == AWAITING_CONFIRMATION:
            self.store.set_reading_status(state.payload["reading_id"], "expired")
        self._save(parent, IDLE, {}, None)

    # --- routing ---

    def _route(
        self,
        parent: Member,
        state: ConversationState,
        msg: Incoming,
        t: str,
        now: datetime,
        source: ReadingSource = "manual",
    ) -> list[str]:
        p = state.payload
        repeat = _timers(p)
        if state.state == AWAITING_SYMPTOMS:
            if msg.image is None and parse.is_yes(t):
                return self._symptom_answer(parent, p, "yes", now)
            if msg.image is None and parse.is_no(t):
                return self._symptom_answer(parent, p, "no", now)
            lang = phrases.lang_of(parent.language)
            if p.get("kind") == "glucose":  # e.g. the recheck arrived: carry on with it
                out = self._symptom_answer(parent, p, "no_reply", now)
                fresh = self.store.get_conversation_state(parent.id)
                return out + self._route(parent, fresh, msg, t, now, source)
            return [Said(tpl.SYMPTOM_PLEASE_ANSWER, phrases.say("symptom_check_bp", lang))]
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
                return self._typed(parent, repeat, typed, now, source)
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
            return self._typed(parent, repeat, typed, now, source)
        return self._general(parent, t, now)

    def _general(self, parent: Member, t: str, now: datetime) -> list[str]:
        lang = phrases.lang_of(parent.language)
        if parse.is_yes(t) or parse.is_no(t) or t in parse.TAKEN:
            events = [
                e
                for e in self.store.list_dose_events(
                    parent.id, since=now - DOSE_REPLY_WINDOW, until=now + timedelta(minutes=15)
                )
                # unanswered: still pending, or marked missed by the 30-min follow-up
                if e.status == "pending" or (e.followed_up_at and e.responded_at is None)
            ]
            if not events:
                return [tpl.NOTHING_TO_CONFIRM]
            status: DoseStatus = "missed" if parse.is_no(t) else "taken"
            for e in events:
                self.store.set_dose_status(e.id, status, at=now)
                if status == "taken":
                    self._use_stock(e.medicine_id)
            names = tpl.join_names(
                sorted({self.store.get_medicine(e.medicine_id).brand for e in events})
            )
            template = tpl.DOSE_SKIPPED if status == "missed" else tpl.DOSE_TAKEN
            phrase = "dose_skipped" if status == "missed" else "dose_taken"
            return [Said(template.format(names=names), phrases.say(phrase, lang, names=names))]
        if t in parse.HELP:
            return [tpl.HELP]
        return [Said(tpl.OFF_TOPIC, phrases.say("off_topic", lang))]

    def _use_stock(self, medicine_id: str) -> None:
        """A dose taken uses one dose's worth of stock (daily dose spread over the times)."""
        med = self.store.get_medicine(medicine_id)
        per_dose = max(1, round(med.daily_dose / max(1, len(med.times))))
        self.store.set_medicine_stock(med.id, max(0, med.stock_count - per_dose))

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
        self,
        parent: Member,
        repeat: dict | None,
        typed: parse.TypedReading,
        now: datetime,
        source: ReadingSource = "manual",
    ) -> list[str]:
        """A typed reading, or a spoken one (source="voice"): same read-back either way."""
        values = typed.model_dump(exclude={"type"})
        shown = None
        if typed.glucose_unit == "mmol/L" and typed.glucose_value is not None:
            shown = f"{typed.glucose_value:g} mmol/L"
        return self._new_reading(
            parent, repeat, typed.type, values, source=source, now=now, shown=shown
        )

    def _new_reading(
        self,
        parent: Member,
        timers: dict | None,
        type: ReadingType,
        values: dict[str, Any],
        *,
        source: ReadingSource,
        now: datetime,
        shown: str | None = None,
    ) -> list[str]:
        timers = dict(timers or {})
        repeat = timers.get("repeat") if type == "bp" else None
        is_repeat = bool(repeat) or (type == "glucose" and bool(timers.get("recheck")))
        if repeat and now < _ts(repeat["due_at"]):
            minutes = math.ceil((_ts(repeat["due_at"]) - now).total_seconds() / 60)
            self._rest(parent, timers)
            return [tpl.REPEAT_TOO_SOON.format(minutes=minutes)]
        reading, check = build_reading(
            self.store, parent.id, type, source=source, now=now, **values
        )
        if reading is None:
            log.info("implausible reading for parent %s: %s", parent.id, check.problems)
            self._rest(parent, timers)
            return [tpl.retake_message([], prefix=tpl.IMPLAUSIBLE)]
        self.store.add_reading(reading)
        value = tpl.format_value(reading, shown)
        payload = {
            "reading_id": reading.id,
            "value": value,
            "type": type,
            "is_repeat": is_repeat,
            "expires_at": _iso(now + ANSWER_TIMEOUT),
        }
        self._save(parent, AWAITING_CONFIRMATION, payload, timers)
        lang = phrases.lang_of(parent.language)
        spoken = phrases.spoken_reading(
            reading.type,
            lang,
            systolic=reading.systolic,
            diastolic=reading.diastolic,
            glucose=(shown or reading.display or f"{reading.glucose_mgdl:g}")
            if reading.glucose_mgdl
            else None,
        )
        voice = phrases.say("read_back", lang, spoken=spoken)
        return [Said(tpl.read_back(value, unusual=bool(check.flags)), voice)]

    def _confirm_reading(self, parent: Member, p: dict, now: datetime) -> list[str]:
        reading = self.store.set_reading_status(p["reading_id"], "confirmed", at=now)
        timers = _timers(p)
        if p.get("is_repeat"):  # this reading answers the repeat / recheck it was waiting for
            timers.pop("repeat" if reading.type == "bp" else "recheck", None)
        value = p["value"]
        lang = phrases.lang_of(parent.language)
        evaluation = evaluate_confirmed_reading(self.store, reading)

        # Severe BP: the warning-signs question comes before anything else (2025 AHA/ACC).
        if evaluation.rule in ("bp_severe_first", "bp_severe_repeat"):
            stage = "bp_repeat" if evaluation.rule == "bp_severe_repeat" else "bp_first"
            template = tpl.SYMPTOM_CHECK_BP_REPEAT if stage == "bp_repeat" else tpl.SYMPTOM_CHECK_BP
            self._ask_symptoms(parent, reading.id, value, stage, now, timers)
            return [Said(template.format(value=value), phrases.say("symptom_check_bp", lang))]

        outcome = self.agent.after_confirmed_reading(parent, reading)
        family = tpl.family_line(outcome.alert_sent, outcome.not_shared)

        # Low sugar (ADA 2026): family alert, the doctor's plan, recheck, then one question.
        if evaluation.rule in LOW_GLUCOSE_RULES:
            plan = tpl.plan_line(self.store.get_low_sugar_plan(parent.id))
            if evaluation.rule == "glucose_amber_below":
                timers["recheck"] = {
                    "first_reading_id": reading.id,
                    "value": value,
                    "alert_at": _iso(reading.created_at + GLUCOSE_RECHECK_WAIT),
                    "expires_at": _iso(reading.created_at + BP_REPEAT_MAX),
                }
                template = tpl.FAMILY_TOLD if outcome.alert_sent else tpl.FAMILY_NOT_REACHED
                if outcome.not_shared:
                    template = tpl.FAMILY_NOT_SHARED
                first = f"{template.format(value=value)} {plan} {tpl.RECHECK_ASK}"
            else:
                first = tpl.LOW_RED.format(value=value, family=family, plan=plan)
            self._ask_symptoms(parent, reading.id, value, "glucose", now, timers)
            return [
                Said(first, phrases.say("low_sugar_plan", lang)),
                Said(tpl.LOW_SUGAR_QUESTION, phrases.say("symptom_check_low", lang)),
            ]

        self._rest(parent, timers)
        if outcome.level == "red":
            return [f"{tpl.SAVED.format(value=value)} {family} {tpl.EMERGENCY}"]
        if outcome.level == "amber":
            template = tpl.FAMILY_TOLD if outcome.alert_sent else tpl.FAMILY_NOT_REACHED
            if outcome.not_shared:
                template = tpl.FAMILY_NOT_SHARED
            return [template.format(value=value)]
        if p.get("is_repeat"):
            template = tpl.REPEAT_OK if reading.type == "bp" else tpl.RECHECK_OK
            return [Said(template.format(value=value), self._saved_voice(parent))]
        return [Said(tpl.SAVED.format(value=value), self._saved_voice(parent))]

    def _ask_symptoms(
        self, parent: Member, reading_id: str, value: str, kind: str, now: datetime, timers: dict
    ) -> None:
        payload = {
            "reading_id": reading_id,
            "value": value,
            "kind": kind,  # bp_first | bp_repeat | glucose
            "asked_at": _iso(now),
            "expires_at": _iso(now + SYMPTOM_WAIT),
        }
        self._save(parent, AWAITING_SYMPTOMS, payload, timers)

    def _symptom_answer(
        self, parent: Member, p: dict, answer: SymptomAnswer, now: datetime
    ) -> list[str]:
        """The parent's own yes / no (or no reply in 5 minutes). Stored server-side on the
        reading, where alert_family reads it; a model can never set it."""
        reading = self.store.set_reading_symptoms(p["reading_id"], answer)
        kind = p.get("kind", "bp_first")
        self._audit_symptoms(parent, reading.id, kind, answer, now)
        timers = _timers(p)
        lang = phrases.lang_of(parent.language)
        value = p["value"]

        if kind == "glucose":
            if answer != "yes":
                self._rest(parent, timers)
                if answer == "no_reply":
                    return []
                return [Said(tpl.LOW_SUGAR_NO_SIGNS, phrases.say("low_sugar_plan", lang))]
            timers.pop("recheck", None)  # RED now; the family takes it from here
            self._rest(parent, timers)
            outcome = self.agent.after_confirmed_reading(parent, reading)
            plan = tpl.plan_line(self.store.get_low_sugar_plan(parent.id))
            family = tpl.family_line(outcome.alert_sent, outcome.not_shared)
            text = tpl.LOW_SUGAR_HELP.format(family=family, plan=plan)
            return [Said(text, phrases.say("low_sugar_help", lang))]

        if answer == "yes":  # warning signs: RED at once, no repeat
            timers.pop("repeat", None)
            self._rest(parent, timers)
            outcome = self.agent.after_confirmed_reading(parent, reading)
            family = tpl.family_line(outcome.alert_sent, outcome.not_shared)
            return [Said(tpl.BP_CALL_NOW.format(family=family), phrases.say("call_108", lang))]

        if kind == "bp_first":  # no signs (or no reply): rest and repeat, as before
            timers["repeat"] = {
                "first_reading_id": reading.id,
                "value": value,
                "due_at": _iso(reading.created_at + BP_REPEAT_MIN),
                "amber_at": _iso(reading.created_at + BP_REPEAT_WAIT),
                "expires_at": _iso(reading.created_at + BP_REPEAT_MAX),
            }
            self._rest(parent, timers)
            template = tpl.REPEAT_ASK_NO_REPLY if answer == "no_reply" else tpl.REPEAT_ASK
            return [Said(template, phrases.say("rest_repeat", lang))]

        # A confirmed repeat, still severe, without warning signs: RED "contact the doctor
        # today" (no ambulance wording for the family).
        self._rest(parent, timers)
        outcome = self.agent.after_confirmed_reading(parent, reading)
        family = tpl.family_line(outcome.alert_sent, outcome.not_shared)
        text = tpl.RED_REPEAT_URGENT.format(value=value, family=family)
        return [Said(text, phrases.say("contact_doctor", lang))]

    def _audit_symptoms(
        self, parent: Member, reading_id: str, kind: str, answer: SymptomAnswer, now: datetime
    ) -> None:
        self.store.add_audit(
            AuditEntry(
                actor="system" if answer == "no_reply" else "member",
                tool="conversation:symptom_check",
                args_json=json.dumps({"reading_id": reading_id, "question": kind}),
                result_json=json.dumps(
                    {"answer": answer}
                    | ({"note": "no reply within 5 minutes"} if answer == "no_reply" else {})
                ),
                parent_id=parent.id,
                created_at=now,
            )
        )

    @staticmethod
    def _saved_voice(parent: Member) -> Voice:
        return phrases.say("saved", phrases.lang_of(parent.language))

    def _reject_reading(self, parent: Member, p: dict, now: datetime) -> list[str]:
        reading = self.store.set_reading_status(p["reading_id"], "rejected", at=now)
        payload = {"type": reading.type, "expires_at": _iso(now + ANSWER_TIMEOUT)}
        self._save(parent, AWAITING_VALUE, payload, _timers(p))
        return [tpl.REJECTED]

    # --- medicine strips ---

    def _strip_candidates(self, parent: Member) -> list[StripCandidate]:
        """What a strip photo may turn into: the parent's own medicines, then the curated
        brand list. Labels come from our data, never from the photo."""
        own = [
            StripCandidate(id=m.id, label=f"{m.brand} ({m.generic} {m.strength})")
            for m in self.store.list_medicines(parent.id)
        ]
        names = {m.label.split(" (")[0].lower() for m in own}
        catalog = [
            StripCandidate(id=catalog_id(r), label=catalog_label(r))
            for r in load_brands()
            if r.display_name.lower() not in names
        ]
        return own + catalog

    def _strip(
        self, parent: Member, timers: dict | None, image: ImageInput, now: datetime
    ) -> list[str]:
        try:
            strip = self.reader.read_strip(image, self._strip_candidates(parent))
        except ReaderError:
            log.warning("strip reader failed for parent %s", parent.id, exc_info=True)
            self._rest(parent, timers)
            return [tpl.READ_FAILED]
        if not strip.legible:
            self._rest(parent, timers)
            return [tpl.STRIP_UNREADABLE]
        payload: dict[str, str] | None = None
        if strip.medicine_id.startswith("med_"):
            med = next(
                (m for m in self.store.list_medicines(parent.id) if m.id == strip.medicine_id),
                None,
            )
            if med is not None:
                label = f"{med.generic} {med.strength}"
                payload = {
                    "name": med.brand,
                    "generic": med.generic,
                    "strength": med.strength,
                    "label": label,
                }
        elif strip.medicine_id != UNKNOWN_MEDICINE:
            row = find_catalog(strip.medicine_id)
            if row is not None:
                payload = {
                    "name": row.display_name,
                    "generic": row.generic,
                    "strength": row.generic_strength,
                    "label": row.generic_label,
                }
        if payload is None:
            self._rest(parent, timers)
            return [tpl.STRIP_UNKNOWN]
        self._save(parent, AWAITING_MED_CONFIRMATION, self._med_payload(payload, now), timers)
        return [tpl.STRIP_READ_BACK.format(name=payload["name"], generic=payload["label"])]

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
        self._rest(parent, _timers(p))
        try:
            final = self.agent.set_reminder(parent, med.id, sorted(set(med.times) | set(times)))
        except Exception:
            log.exception("create_reminder failed for medicine %s", med.id)
            return [tpl.REMINDER_FAILED.format(name=name)]
        when = tpl.join_names([tpl.clock_12h(x) for x in final])
        return [tpl.REMINDER_SET.format(name=name, times=when)]

    # --- helpers ---

    def _save(self, parent: Member, state: str, payload: dict, timers: dict | None) -> None:
        """`timers` = the running repeat-BP / glucose-recheck timers ({} or None: none)."""
        payload = {k: v for k, v in payload.items() if k not in TIMER_KEYS}
        payload |= {k: v for k, v in (timers or {}).items() if k in TIMER_KEYS and v}
        self.store.set_conversation_state(
            ConversationState(member_id=parent.id, state=state, payload=payload)
        )

    def _rest(self, parent: Member, timers: dict | None) -> None:
        """Nothing waiting for an answer: idle, or still waiting for a repeat BP reading
        (a glucose recheck timer runs in the background while idle)."""
        timers = timers or {}
        self._save(parent, AWAITING_REPEAT if timers.get("repeat") else IDLE, {}, timers)

    def _to(self, member: Member, text: str, voice: Voice | None = None) -> Outgoing:
        return Outgoing(
            channel=member.channel,
            to_ref=member.channel_ref,
            member_id=member.id,
            text=str(text),
            voice=voice,
        )

    def _family(self) -> list[Outgoing]:
        return self.drain_outbox() if self.drain_family else []

    def drain_outbox(self) -> list[Outgoing]:
        """Standalone use only (no channels): take every due outbox message and hand it to
        the caller, which counts as delivery. The service delivers with retries instead."""
        out = []
        for m in self.store.claim_outbox(self.clock(), timedelta(minutes=2)):
            out.append(outbox_to_outgoing(m, self.store.get_member(m.member_id)))
            self.store.mark_outbox_delivered(m.id)
        return out


def outbox_to_outgoing(m: OutboxMessage, member: Member) -> Outgoing:
    """An outbox row (written by the MCP server or the dashboard) as a channel message."""
    voice = Voice(text=m.voice_text, lang=m.voice_lang or "en") if m.voice_text else None
    return Outgoing(
        channel=member.channel,
        to_ref=member.channel_ref,
        member_id=member.id,
        text=m.text,
        kind=m.kind,
        voice=voice,
    )
