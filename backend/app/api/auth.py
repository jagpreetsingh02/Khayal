"""Dashboard sign-in: signed sessions, login rate limits (brief §5.6; Cognito on AWS later).

Sessions are `<payload>.<signature>` (both base64url). The payload is JSON with the member
id, issued-at, expiry and that member's session version; the signature is HMAC-SHA256 with
SESSION_SECRET (stdlib, constant-time compare). Expired, tampered or wrongly signed sessions
get 401. Logging out bumps the member's session version, which ends every session of that
member (all devices). Session times use the real wall clock, never the demo clock, so
"advance 1 day" in the simulator does not sign the presenter out.

How members sign in (app/api/dashboard.py):
- APP_MODE=mock: demo token (DASHBOARD_DEMO_TOKEN) + pick a member, or a one-time code.
- APP_MODE=dev/aws: one-time code only (app/api/login_codes.py); the demo token is refused.

Rate limits are in-memory sliding windows per API process (fine for one process; a shared
store would be needed behind a load balancer). Client IP = the socket peer
(uvicorn trusts X-Forwarded-For only from 127.0.0.1 by default).
"""

import base64
import binascii
import hashlib
import hmac
import json
import logging
import secrets
import threading
import time
from collections import deque
from typing import Any

from fastapi import Header, HTTPException, Request

from app.config import get_settings
from app.storage import NotFoundError
from app.storage.base import Store
from app.storage.models import Member

log = logging.getLogger(__name__)
SESSION_FORMAT = 1
CLOCK_SKEW = 60  # seconds a session's issued-at may lie in the future
_random_secret = secrets.token_bytes(32)  # mock mode without SESSION_SECRET: per process


def session_secret() -> bytes:
    s = get_settings()
    if s.session_secret:
        return s.session_secret.encode()
    if s.app_mode == "mock":
        return _random_secret
    raise HTTPException(503, "SESSION_SECRET is not set.")


def derived_key(purpose: str) -> bytes:
    """A separate key per use of SESSION_SECRET (sessions, login-code hashes)."""
    return hmac.new(session_secret(), f"khayal/{purpose}".encode(), hashlib.sha256).digest()


def demo_login_enabled() -> bool:
    return get_settings().app_mode == "mock"


def demo_token_ok(token: str) -> bool:
    expected = get_settings().dashboard_demo_token
    if not demo_login_enabled() or not expected:
        return False
    return hmac.compare_digest(token.encode(), expected.encode())


# --- sessions ---


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _version_key(member_id: str) -> str:
    return f"session_version:{member_id}"


def session_version(store: Store, member_id: str) -> int:
    return int(store.get_kv(_version_key(member_id)) or 0)


def end_sessions(store: Store, member_id: str) -> int:
    """Log out: every session issued before this for the member stops working."""
    return store.incr_kv(_version_key(member_id), 1)


def _sign(payload: bytes) -> bytes:
    return hmac.new(derived_key("session"), payload, hashlib.sha256).digest()


def sign_session(
    member_id: str, version: int = 0, *, now: float | None = None, ttl_hours: float | None = None
) -> tuple[str, int]:
    """A new session token for the member, and its expiry (unix seconds)."""
    iat = int(now if now is not None else time.time())
    ttl = ttl_hours if ttl_hours is not None else get_settings().session_ttl_hours
    exp = iat + int(ttl * 3600)
    payload = {"v": SESSION_FORMAT, "m": member_id, "iat": iat, "exp": exp, "sv": version}
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return f"{_b64(raw)}.{_b64(_sign(raw))}", exp


def read_session(token: str, *, now: float | None = None) -> dict[str, Any] | None:
    """The payload of a valid, unexpired session signed with our secret; else None."""
    body, _, sig = token.partition(".")
    if not body or not sig:
        return None
    try:
        raw, given = _unb64(body), _unb64(sig)
    except (binascii.Error, ValueError):
        return None
    if not hmac.compare_digest(given, _sign(raw)):
        return None
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    t = now if now is not None else time.time()
    if (
        not isinstance(payload, dict)
        or payload.get("v") != SESSION_FORMAT
        or not isinstance(payload.get("m"), str)
        or not isinstance(payload.get("exp"), int)
        or not isinstance(payload.get("iat"), int)
        or not isinstance(payload.get("sv"), int)
    ):
        return None
    if payload["exp"] <= t or payload["iat"] > t + CLOCK_SKEW:
        return None
    return payload


def new_session(store: Store, member: Member) -> dict[str, Any]:
    """Login response: session token, its expiry and the member."""
    token, exp = sign_session(member.id, session_version(store, member.id))
    return {
        "session": token,
        "expires_at": exp,
        "member": {"id": member.id, "name": member.name, "role": member.role},
    }


def current_member(request: Request, authorization: str | None = Header(None)) -> Member:
    """FastAPI dependency: the signed-in family member (401 otherwise)."""
    scheme, _, token = (authorization or "").partition(" ")
    payload = read_session(token.strip()) if scheme.lower() == "bearer" else None
    if payload is None:
        raise HTTPException(401, "Please sign in.")
    store = request.app.state.service.store
    if payload["sv"] != session_version(store, payload["m"]):
        raise HTTPException(401, "Signed out. Please sign in again.")
    try:
        member = store.get_member(payload["m"])
    except NotFoundError as exc:  # e.g. after "Reset demo"
        raise HTTPException(401, "Session expired, please sign in again.") from exc
    request.state.session = payload
    return member


# --- rate limits ---


class SlidingWindow:
    """At most `limit` events per key in the last `window` seconds (in memory, per process)."""

    def __init__(self, limit: int, window: float, clock=time.monotonic) -> None:
        self.limit, self.window, self.clock = limit, window, clock
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque[float]:
        events = self._events.get(key)
        if events is None:
            return deque()
        while events and events[0] <= now - self.window:
            events.popleft()
        if not events:
            del self._events[key]
        return events

    def retry_after(self, key: str) -> int | None:
        """Seconds until the next event is allowed, or None if allowed now."""
        with self._lock:
            now = self.clock()
            events = self._prune(key, now)
            if len(events) < self.limit:
                return None
            return max(1, int(events[0] + self.window - now + 0.999))

    def add(self, key: str) -> None:
        with self._lock:
            self._events.setdefault(key, deque()).append(self.clock())

    def hit(self, key: str) -> int | None:
        """Count one event if allowed (None); else the seconds to wait (not counted)."""
        with self._lock:
            now = self.clock()
            events = self._prune(key, now)
            if len(events) >= self.limit:
                return max(1, int(events[0] + self.window - now + 0.999))
            self._events.setdefault(key, deque()).append(now)
            return None


class LoginLimits:
    """One set per app (app.state.login_limits)."""

    def __init__(self) -> None:
        # 5 failed sign-ins per IP per 5 minutes (demo token, member list, code verify).
        self.failed = SlidingWindow(limit=5, window=300)
        # Code requests: 3 per member id per 15 minutes, and 10 per IP per 15 minutes.
        self.code_member = SlidingWindow(limit=3, window=900)
        self.code_ip = SlidingWindow(limit=10, window=900)


def limits(request: Request) -> LoginLimits:
    return request.app.state.login_limits


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def too_many(retry_after: int) -> HTTPException:
    return HTTPException(
        429,
        f"Too many attempts. Try again in {retry_after} seconds.",
        headers={"Retry-After": str(retry_after)},
    )


def check_login_allowed(request: Request) -> None:
    wait = limits(request).failed.retry_after(client_ip(request))
    if wait is not None:
        raise too_many(wait)


def record_failed_login(request: Request) -> None:
    ip = client_ip(request)
    limits(request).failed.add(ip)
    log.warning("failed dashboard sign-in from %s", ip)
