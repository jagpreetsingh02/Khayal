"""Who is this tool call from, and for whom? Resolved server-side, never from tool arguments.

Token mode (MCP_AUTH_MODE=token, the default): every HTTP request carries a short-lived JWT
signed by our API process (tokens.py). The caller is the member in the token's `sub`, with the
token's role and scopes (capped to what that role may hold). The sender the backend also puts
in `_meta` is only a cross-check: if it names anyone else, the call is refused and audited.

Secret mode (MCP_AUTH_MODE=secret, APP_MODE=mock only) and in-process calls (tests, the
offline terminal demo; no HTTP at all): there is no per-member token, so the caller is the
sender in `_meta`, mapped to a member through the `members` table, with that member's role's
scopes. In secret mode, a holder of the one shared secret could claim any sender; that is why
dev/aws refuse it at startup.

The LLM only produces tool arguments: it can set neither the token nor `_meta`, and no tool
takes a parent or member id.
"""

from dataclasses import dataclass, field
from typing import Any, Literal

from fastmcp import Context
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token, get_http_request

from app.storage.base import NotFoundError, Store
from app.storage.models import Member
from mcp_server.scopes import MEMBER_ROLE, ROLE_SCOPES, ROLES, Role
from mcp_server.tokens import ISSUER

SENDER_META_KEY = "khayal/sender"
AuthMode = Literal["secret", "token"]


class IdentityMismatch(ToolError):
    """`_meta` names a different sender than the signed token."""

    def __init__(self, message: str, token_member: Member) -> None:
        super().__init__(message)
        self.token_member = token_member


@dataclass(frozen=True)
class Caller:
    member: Member
    role: Role
    scopes: frozenset[str]
    via: Literal["token", "meta"]


@dataclass
class CallState:
    """Per tool call: who called, and which parent the call acted on (for the audit)."""

    caller: Caller | None = None
    parent: Member | None = field(default=None)


def sender_meta(channel: str, channel_ref: str) -> dict[str, Any]:
    """Build the `_meta` block the backend sends with each tool call."""
    return {SENDER_META_KEY: {"channel": channel, "ref": channel_ref}}


def _meta(ctx: Context | None) -> dict[str, Any]:
    rc = ctx.request_context if ctx is not None else None
    meta = rc.meta if rc is not None else None
    return meta if isinstance(meta, dict) else {}


def sender_from_context(ctx: Context | None) -> tuple[str, str] | None:
    sender = _meta(ctx).get(SENDER_META_KEY)
    if not isinstance(sender, dict):
        return None
    channel, ref = sender.get("channel"), sender.get("ref")
    if not isinstance(channel, str) or not isinstance(ref, str):
        return None
    return channel, ref


def token_claims() -> tuple[dict[str, Any], frozenset[str]] | None:
    """(claims, scopes) of a verified Khayal token on this request, else None (in-process,
    or the legacy shared secret, whose access token carries no claims)."""
    token = get_access_token()
    if token is None or not token.claims or token.claims.get("iss") != ISSUER:
        return None
    return token.claims, frozenset(token.scopes)


def over_http() -> bool:
    try:
        get_http_request()
    except RuntimeError:
        return False
    return True


def _member(store: Store, member_id: str) -> Member | None:
    try:
        return store.get_member(member_id)
    except NotFoundError:
        return None


def token_scopes(claims: dict[str, Any], scopes: frozenset[str]) -> frozenset[str]:
    """The token's scopes, capped to what its role may hold."""
    role = claims.get("role")
    return scopes & ROLE_SCOPES[role] if role in ROLES else frozenset()


def resolve_caller(ctx: Context | None, store: Store, require_token: bool = False) -> Caller:
    """The member making this call, or a ToolError. `require_token`: the server runs in token
    mode with a verifier, so an HTTP call must carry a Khayal token."""
    verified = token_claims()
    if verified is not None:
        claims, scopes = verified
        member = _member(store, str(claims.get("sub")))
        if member is None:
            raise ToolError("Unknown caller: the token's member is not registered with Khayal.")
        role = MEMBER_ROLE.get(member.role)
        if role is None or claims.get("role") != role:
            raise ToolError("Refused: the token's role does not match the member's role.")
        _cross_check(ctx, store, member)
        return Caller(member, role, token_scopes(claims, scopes), "token")

    if require_token and over_http():  # FastMCP already answers 401; belt and braces
        raise ToolError("Refused: a signed Khayal token is required.")
    sender = sender_from_context(ctx)
    if sender is None:
        raise ToolError("Unknown sender: request has no sender context.")
    member = store.find_member_by_channel(*sender)
    if member is None:
        raise ToolError("Unknown sender: not registered with Khayal.")
    role = MEMBER_ROLE.get(member.role)
    if role is None:
        raise ToolError("Unknown sender: not a parent or family member.")
    return Caller(member, role, ROLE_SCOPES[role], "meta")


def _cross_check(ctx: Context | None, store: Store, member: Member) -> None:
    """`_meta` may only repeat the token's identity. Absent is fine (a family gateway sends
    none); present and different (or unreadable, or unknown) is refused."""
    if SENDER_META_KEY not in _meta(ctx):
        return
    sender = sender_from_context(ctx)
    claimed = store.find_member_by_channel(*sender) if sender else None
    if claimed is None or claimed.id != member.id:
        raise IdentityMismatch(
            "Refused: the sender in _meta does not match the signed token.", member
        )


def meta_parent_id(ctx: Context | None, store: Store) -> str | None:
    """The parent `_meta` claims to be (for auditing a refused call), if any."""
    sender = sender_from_context(ctx)
    member = store.find_member_by_channel(*sender) if sender else None
    return member.id if member and member.role == "parent" else None


def require_parent(caller: Caller) -> Member:
    """The parent this call acts for (write tools, and a parent's own reads)."""
    member = caller.member
    if caller.role != "parent" or member.role != "parent":
        raise ToolError("These tools act for a parent; the sender is not a parent.")
    if member.consent_status != "granted":
        raise ToolError(
            f"Consent is {member.consent_status} for this parent: Khayal does not log, "
            "alert or remind until the parent agrees (or replies START after a pause)."
        )
    return member


def resolve_parent(ctx: Context | None, store: Store, require_token: bool = False) -> Member:
    """Return the parent this call acts for, or raise a ToolError."""
    return require_parent(resolve_caller(ctx, store, require_token))
