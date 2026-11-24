"""The scheduler (brief §5.7). `Scheduler.run(now)` is one pure-ish pass over every parent;
the API calls it every 30 s and the simulator's "advance time" calls it in 5-minute steps.
Every job is idempotent, so running it twice (or after a restart) never double-sends:

- Reminder: at each confirmed medicine's time (Asia/Kolkata) a DoseEvent is created and the
  parent gets "8:00 PM: time for Telma 40. Reply 1 when taken" (+ voice note).
- Follow-up: no reply 30 min after the reminder -> the dose counts as missed and the parent
  gets ONE gentle follow-up; a later "1" still marks it taken.
- Missed doses: when doses are marked missed, the agent calls `alert_family` (AMBER); the MCP
  server checks the doctor-set count (2+/day) and sends at most one such alert a day.
- Low stock: <= 5 days left -> `request_refill` through the real MCP server (the family is
  told and approves on the dashboard; nothing here can approve). At most one try per medicine
  per day, and none while a refill is pending or was requested in the last 7 days.
- Weekly summary: Sunday 19:00 IST, the `weekly_summary` tool's text to the family (only if
  the parent shares readings), written to the outbox like every family message.

Only parents who have agreed (consent granted) get anything.
"""

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastmcp.exceptions import ToolError

from app.agent.rule_agent import RuleAgent
from app.alerts.thresholds import effective_thresholds
from app.conversation import templates as tpl
from app.conversation.engine import ConversationEngine
from app.conversation.messages import Outgoing
from app.storage.base import Store
from app.storage.models import DoseEvent, Member, OutboxMessage, utcnow
from app.voice import phrases

log = logging.getLogger(__name__)

TZ = ZoneInfo("Asia/Kolkata")
FOLLOW_UP_AFTER = timedelta(minutes=30)
FOLLOW_UP_MAX_AGE = timedelta(hours=3)  # older unanswered doses are marked missed silently
LOW_STOCK_DAYS = 5
REFILL_COOLDOWN = timedelta(days=7)
WEEKLY_WEEKDAY = 6  # Sunday
WEEKLY_HOUR = 19


class Scheduler:
    def __init__(self, store: Store, engine: ConversationEngine, agent: RuleAgent) -> None:
        self.store = store
        self.engine = engine
        self.agent = agent

    def run(self, now: datetime | None = None) -> list[Outgoing]:
        """One pass for every parent. Returns messages to deliver (family messages written
        by the MCP server are in the store outbox; the caller drains it)."""
        now = now or utcnow()
        out: list[Outgoing] = []
        for parent in self.store.list_parents():
            out += self.run_parent(parent, now)
        return out

    def run_parent(self, parent: Member, now: datetime) -> list[Outgoing]:
        """Every job for one parent. The service runs this holding that parent's turn, so it
        never interleaves with the parent's own messages."""
        if parent.consent_status != "granted":
            return []
        out: list[Outgoing] = []
        try:
            out += self.reminders(parent, now)
            out += self.follow_ups(parent, now)
            out += self.low_stock(parent, now)
            out += self.weekly_summary(parent, now)
        except Exception:  # one family's problem must not stop the others
            log.exception("scheduler failed for parent %s", parent.id)
        return out

    # --- reminders ---

    def _today(self, now: datetime) -> datetime:
        local = now.astimezone(TZ)
        return datetime(local.year, local.month, local.day, tzinfo=TZ)

    def reminders(self, parent: Member, now: datetime) -> list[Outgoing]:
        day = self._today(now)
        existing = {
            (e.medicine_id, e.due_at)
            for e in self.store.list_dose_events(parent.id, since=day, until=day + timedelta(1))
        }
        slots: dict[str, list[str]] = defaultdict(list)
        for med in self.store.list_medicines(parent.id):
            if med.confirmed:
                for hhmm in med.times:
                    slots[hhmm].append(med.id)
        out = []
        for hhmm, med_ids in sorted(slots.items()):
            h, m = map(int, hhmm.split(":"))
            due = day.replace(hour=h, minute=m)
            if not due <= now < due + FOLLOW_UP_AFTER:
                continue
            new = [mid for mid in med_ids if (mid, due) not in existing]
            if not new:
                continue
            for mid in new:
                self.store.add_dose_event(DoseEvent(medicine_id=mid, due_at=due))
            msg = self.engine.dose_reminder(parent.id, new, hhmm)
            if msg:
                out.append(msg)
        return out

    # --- follow-ups and missed doses ---

    def follow_ups(self, parent: Member, now: datetime) -> list[Outgoing]:
        events = self.store.list_dose_events(parent.id, since=now - timedelta(days=2), until=now)
        late = [
            e
            for e in events
            if e.status == "pending"
            and e.followed_up_at is None
            and e.due_at + FOLLOW_UP_AFTER <= now
        ]
        if not late:
            return []
        by_slot: dict[datetime, list[str]] = defaultdict(list)
        for e in late:
            self.store.mark_dose_followed_up(e.id, now)
            if now - e.due_at <= FOLLOW_UP_MAX_AGE:
                by_slot[e.due_at].append(e.medicine_id)
        out = []
        for due, med_ids in sorted(by_slot.items()):
            hhmm = due.astimezone(TZ).strftime("%H:%M")
            msg = self.engine.dose_followup(parent.id, med_ids, hhmm)
            if msg:
                out.append(msg)
        if self._missed_today(parent, now) >= self._limit(parent, "missed_doses_per_day"):
            self.agent.after_missed_doses(parent)  # the server re-checks; one AMBER a day
        return out

    def _missed_today(self, parent: Member, now: datetime) -> int:
        day = self._today(now)
        events = self.store.list_dose_events(parent.id, since=day, until=day + timedelta(1))
        return sum(e.status == "missed" for e in events)

    def _limit(self, parent: Member, rule: str) -> float:
        return effective_thresholds(self.store.list_thresholds(parent.id))[rule]

    # --- low stock ---

    def low_stock(self, parent: Member, now: datetime) -> list[Outgoing]:
        out = []
        recent = self.store.list_refills(parent.id)
        lang = phrases.lang_of(parent.language)
        for med in self.store.list_medicines(parent.id):
            if not med.confirmed or med.is_schedule_x or med.daily_dose <= 0:
                continue
            if med.stock_count // med.daily_dose > LOW_STOCK_DAYS:
                continue
            if any(
                r.medicine_id == med.id
                and (r.status == "pending_approval" or now - r.created_at < REFILL_COOLDOWN)
                for r in recent
            ):
                continue
            if not self.store.claim(f"low_stock:{med.id}:{now.astimezone(TZ).date()}"):
                continue  # already tried today
            try:
                self.agent.request_refill(parent, med)
            except ToolError as exc:  # refused by the server (consent, not shared, ...)
                log.info("refill for %s not requested: %s", med.brand, exc)
                continue
            text = tpl.LOW_STOCK_ASKED.format(brand=med.brand, stock=med.stock_count)
            out.append(
                Outgoing(
                    channel=parent.channel,
                    to_ref=parent.channel_ref,
                    member_id=parent.id,
                    text=text,
                    voice=phrases.say("low_stock", lang, brand=med.brand),
                )
            )
        return out

    # --- weekly summary ---

    def weekly_summary(
        self, parent: Member, now: datetime, *, force: bool = False
    ) -> list[Outgoing]:
        local = now.astimezone(TZ)
        if not force:
            if local.weekday() != WEEKLY_WEEKDAY or local.hour < WEEKLY_HOUR:
                return []
            if not self.store.claim(f"weekly:{parent.id}:{local.date()}"):
                return []
        if not self.store.get_sharing(parent.id).readings:
            return []  # the summary is about readings; parent-first sharing applies
        try:
            text = self.agent.weekly_summary(parent)
        except Exception:
            log.exception("weekly_summary failed for parent %s", parent.id)
            return []
        if not text:
            return []
        # Family-facing, so it goes through the outbox (delivered with retries by the service).
        for child in self.store.list_members(parent.family_id, role="child"):
            # text is already headed "Weekly summary for <parent> (dates)"
            self.store.add_outbox(OutboxMessage(member_id=child.id, text=text))
        return []
