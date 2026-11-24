"""One-time sign-in codes for the dashboard (the production sign-in; demo token = mock only).

- 6 random digits (`secrets`), sent to the member on their own channel (simulator /
  Telegram / WhatsApp) through the normal outbox, as a `sensitive` message that expires with
  the code: never retried after 5 minutes, never retried forever, text blanked to
  "[code removed]" once delivered or expired (app/channels/service.py).
- Stored only as HMAC-SHA256(key derived from SESSION_SECRET, member id + code), so a
  database copy alone cannot be brute-forced over the 10^6 codes.
- Expires after 5 minutes, at most 5 verify attempts, single use, and a new request revokes
  the older code (and drops its undelivered message).
- Times use the app clock (the demo clock in mock mode), like the outbox that carries them.
"""

import hashlib
import hmac
import secrets
from datetime import timedelta

from app.api.auth import derived_key
from app.storage import NotFoundError
from app.storage.base import Store
from app.storage.models import LoginCode, Member, OutboxMessage, utcnow

CODE_TTL = timedelta(minutes=5)
MAX_TRIES = 5
CODE_DIGITS = 6


def code_hash(member_id: str, code: str) -> str:
    msg = f"{member_id}:{code}".encode()
    return hmac.new(derived_key("login-code"), msg, hashlib.sha256).hexdigest()


def code_message(code: str) -> str:
    minutes = int(CODE_TTL.total_seconds() // 60)
    return (
        f"Your Khayal dashboard sign-in code is {code}. It expires in {minutes} minutes. "
        "Do not share it with anyone."
    )


def find_member(store: Store, member_id: str) -> Member | None:
    try:
        return store.get_member(member_id)
    except NotFoundError:
        return None


def issue_code(store: Store, member: Member) -> LoginCode:
    """Make a new code for the member and queue it on their channel (sent by the outbox)."""
    now = utcnow()
    code = f"{secrets.randbelow(10**CODE_DIGITS):0{CODE_DIGITS}d}"
    store.expire_outbox(now, member_id=member.id)  # the older code's message, if still queued
    row = store.add_login_code(
        LoginCode(
            member_id=member.id,
            code_hash=code_hash(member.id, code),
            created_at=now,
            expires_at=now + CODE_TTL,
        )
    )
    store.add_outbox(
        OutboxMessage(
            member_id=member.id,
            text=code_message(code),
            created_at=now,
            expires_at=now + CODE_TTL,
            sensitive=True,
        )
    )
    return row


def verify_code(store: Store, member_id: str, code: str) -> Member | None:
    """The member, if `code` is their current code (counts one try; single use)."""
    now = utcnow()
    member = find_member(store, member_id)
    given = code_hash(member_id, code)  # computed either way: similar timing
    if member is None:
        return None
    active = store.active_login_code(member.id, now)
    if active is None or store.count_login_try(active.id, MAX_TRIES) is None:
        return None
    if not (code.isdigit() and len(code) == CODE_DIGITS):
        return None
    if not hmac.compare_digest(given, active.code_hash):
        return None
    return member if store.use_login_code(active.id, now) else None
