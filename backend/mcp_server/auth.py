"""MCP server HTTP auth. FastMCP answers 401 to a request without a valid bearer, before
any tool runs.

- token (default, MCP_AUTH_MODE=token): a short-lived HS256 JWT per call, minted by our API
  process with MCP_TOKEN_SECRET (tokens.py). The caller's identity comes from its `sub`.
- secret (legacy, APP_MODE=mock only): "Authorization: Bearer <MCP_SHARED_SECRET>"; the
  sender in `_meta` is then trusted by convention, so dev/aws refuse this mode at startup.
"""

import hmac

from fastmcp.server.auth import TokenVerifier
from fastmcp.server.auth.auth import AccessToken

from mcp_server.tokens import KhayalTokenVerifier

CLIENT_ID = "khayal-api"


class SharedSecretVerifier(TokenVerifier):
    def __init__(self, secret: str) -> None:
        if not secret:
            raise ValueError("MCP shared secret is empty")
        super().__init__()
        self._secret = secret.encode()

    async def verify_token(self, token: str) -> AccessToken | None:
        if not hmac.compare_digest(token.encode(), self._secret):  # constant time
            return None
        return AccessToken(token="shared-secret", client_id=CLIENT_ID, scopes=[])


def build_auth(mode: str, shared_secret: str, token_secret: str) -> TokenVerifier | None:
    """The verifier for `mode`; None when its secret is empty (in-process tests only:
    mcp_server.server.main() refuses to serve HTTP without one)."""
    if mode == "secret":
        return SharedSecretVerifier(shared_secret) if shared_secret else None
    return KhayalTokenVerifier(token_secret) if token_secret else None
