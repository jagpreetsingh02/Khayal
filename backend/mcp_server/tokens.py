"""Short-lived signed tokens between the Khayal API and the Khayal MCP server.

The API mints one HS256 JWT per MCP call (60 seconds):

    iss=khayal-api  aud=khayal-mcp  sub=<member id>  role=parent|family
    scope="<space-separated scopes>"  iat  exp=iat+60  jti=<random>

The MCP server verifies signature, issuer, audience and expiry with FastMCP's JWTVerifier
(HS256, shared secret MCP_TOKEN_SECRET) plus the stricter checks below, and takes the
caller's identity from `sub`. `_meta` is then only a cross-check (identity.py).

Limits, said plainly: HS256 means the same secret signs and verifies, shared by two of our
own processes (not a third-party identity provider). There is no refresh and no revocation
list: a token dies 60 seconds after it is made.
"""

import secrets
import time
from collections.abc import Iterable
from typing import Any

from fastmcp.server.auth.auth import AccessToken
from fastmcp.server.auth.providers.jwt import JWTVerifier
from joserfc import jwt
from joserfc.jwk import OctKey

from mcp_server.scopes import ROLE_SCOPES, ROLES, Role

ISSUER = "khayal-api"
AUDIENCE = "khayal-mcp"
ALGORITHM = "HS256"
TOKEN_TTL_SECONDS = 60
MIN_SECRET_LENGTH = 32
CLOCK_SKEW_SECONDS = 5
# Subject of the API's own tools/list check (health panel): lists the parent tools, and
# every tool call with it fails (no member has this id).
HEALTH_SUBJECT = "khayal-api:health"


def _key(secret: str) -> OctKey:
    if len(secret) < MIN_SECRET_LENGTH:
        raise ValueError(f"MCP_TOKEN_SECRET must be at least {MIN_SECRET_LENGTH} characters.")
    return OctKey.import_key(secret.encode("utf-8"))


def sign(claims: dict[str, Any], secret: str, algorithm: str = ALGORITHM) -> str:
    """Sign arbitrary claims (tests use it to build bad tokens on purpose)."""
    header = {"alg": algorithm, "typ": "JWT"}
    return jwt.encode(header, claims, _key(secret), algorithms=[algorithm])


def mint_token(
    secret: str,
    member_id: str,
    role: Role,
    scopes: Iterable[str] | None = None,
    *,
    ttl: int = TOKEN_TTL_SECONDS,
    now: float | None = None,
) -> str:
    """A token for one MCP call by `member_id` acting as `role` (default: the role's scopes)."""
    if role not in ROLES:
        raise ValueError(f"Unknown role {role!r}.")
    issued = int(now if now is not None else time.time())
    granted = sorted(ROLE_SCOPES[role] if scopes is None else set(scopes))
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": member_id,
        "role": role,
        "scope": " ".join(granted),
        "iat": issued,
        "exp": issued + ttl,
        "jti": secrets.token_urlsafe(16),
    }
    return sign(claims, secret)


class KhayalTokenVerifier(JWTVerifier):
    """FastMCP's JWTVerifier (HS256: signature, iss, aud, exp, nbf), plus what it leaves
    optional and we require: exp, iat and jti present, a lifetime of at most 60 seconds,
    no iat in the future, a non-empty sub and a known role. A token that fails any check is
    answered with 401 before any tool runs."""

    def __init__(self, secret: str) -> None:
        _key(secret)  # length check with a clear message
        super().__init__(public_key=secret, issuer=ISSUER, audience=AUDIENCE, algorithm=ALGORITHM)

    async def verify_token(self, token: str) -> AccessToken | None:
        access = await super().verify_token(token)
        if access is None:
            return None
        c = access.claims
        exp, iat = c.get("exp"), c.get("iat")
        if not isinstance(exp, int) or not isinstance(iat, int):
            return None
        if exp - iat > TOKEN_TTL_SECONDS or iat > time.time() + CLOCK_SKEW_SECONDS:
            return None
        if not isinstance(c.get("jti"), str) or not c["jti"]:
            return None
        if not isinstance(c.get("sub"), str) or not c["sub"] or c.get("role") not in ROLES:
            return None
        return access
