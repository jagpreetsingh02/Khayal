"""MCP server auth: every HTTP request must carry "Authorization: Bearer <MCP_SHARED_SECRET>".

The only legitimate caller is our own API process (its agent), so a shared secret is enough.
It is what makes the sender in `_meta` trustworthy (see identity.py): only a caller that
proved the secret can claim to act for a sender. Uses FastMCP's TokenVerifier, so FastMCP
answers 401 to requests without a valid token before any tool runs.
"""

import hmac

from fastmcp.server.auth import TokenVerifier
from fastmcp.server.auth.auth import AccessToken

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
