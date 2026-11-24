"""Host / Origin guard for the MCP HTTP endpoint (DNS-rebinding defence).

The MCP Inspector incident (CVE-2025-49596): a web page in the user's browser reached a
localhost MCP endpoint through DNS rebinding. Our only legitimate caller is the Khayal API
process, which never sends an `Origin` header and always uses a known host name. So:

- a request whose Host is not on the allowlist gets 403;
- a request with ANY Origin header (i.e. from a browser) gets 403.

Allowed hosts: localhost, 127.0.0.1, ::1, the Docker Compose service name `mcp`, the host
of MCP_SERVER_URL, plus MCP_ALLOWED_HOSTS (comma separated). Ports are ignored.
This runs before authentication and before any MCP session is created.
"""

from collections.abc import Iterable
from urllib.parse import urlparse

from starlette.datastructures import Headers
from starlette.middleware import Middleware
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

DEFAULT_ALLOWED_HOSTS = ("localhost", "127.0.0.1", "::1", "mcp")


def host_name(host_header: str) -> str:
    """ "localhost:8001" -> "localhost"; "[::1]:8001" -> "::1"."""
    host = host_header.strip().lower()
    if host.startswith("["):
        return host[1 : host.find("]")] if "]" in host else host
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


class HostOriginGuard:
    def __init__(self, app: ASGIApp, allowed_hosts: Iterable[str] = DEFAULT_ALLOWED_HOSTS):
        self.app = app
        self.allowed = {h.strip().lower().strip("[]") for h in allowed_hosts if h.strip()}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = Headers(scope=scope)
            if "origin" in headers:
                await PlainTextResponse("Forbidden: browser origin", 403)(scope, receive, send)
                return
            if host_name(headers.get("host", "")) not in self.allowed:
                await PlainTextResponse("Forbidden: unexpected host", 403)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def allowed_hosts(mcp_server_url: str = "", extra: str = "") -> list[str]:
    hosts = list(DEFAULT_ALLOWED_HOSTS)
    url_host = urlparse(mcp_server_url).hostname if mcp_server_url else None
    if url_host:
        hosts.append(url_host)
    hosts += [h for h in extra.split(",") if h.strip()]
    return hosts


def guard_middleware(mcp_server_url: str = "", extra: str = "") -> list[Middleware]:
    return [Middleware(HostOriginGuard, allowed_hosts=allowed_hosts(mcp_server_url, extra))]
