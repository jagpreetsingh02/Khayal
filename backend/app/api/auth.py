"""Demo login for the family dashboard (brief §5.6: simple auth for the prototype; Cognito
on AWS).

A family member picks their name and enters DASHBOARD_DEMO_TOKEN. They get a session token
`<member_id>.<hmac>` signed with that demo token, so the member cannot be swapped without
the secret, and changing the demo token logs everyone out. Every /api route requires it.
"""

import hashlib
import hmac

from fastapi import Header, HTTPException, Request

from app.config import get_settings
from app.storage import NotFoundError
from app.storage.models import Member


def _secret() -> bytes:
    token = get_settings().dashboard_demo_token
    if not token:
        raise HTTPException(503, "DASHBOARD_DEMO_TOKEN is not set.")
    return token.encode()


def demo_token_ok(token: str) -> bool:
    return hmac.compare_digest(token.encode(), _secret())


def sign_session(member_id: str) -> str:
    sig = hmac.new(_secret(), member_id.encode(), hashlib.sha256).hexdigest()
    return f"{member_id}.{sig}"


def member_from_session(session: str) -> str | None:
    member_id, _, sig = session.rpartition(".")
    if not member_id or not sig:
        return None
    expected = sign_session(member_id).rpartition(".")[2]
    return member_id if hmac.compare_digest(sig, expected) else None


def current_member(request: Request, authorization: str | None = Header(None)) -> Member:
    """FastAPI dependency: the signed-in family member (401 otherwise)."""
    scheme, _, session = (authorization or "").partition(" ")
    member_id = member_from_session(session.strip()) if scheme.lower() == "bearer" else None
    if member_id is None:
        raise HTTPException(401, "Sign in with the demo token.")
    try:
        return request.app.state.service.store.get_member(member_id)
    except NotFoundError as exc:  # e.g. after "Reset demo"
        raise HTTPException(401, "Session expired, please sign in again.") from exc
