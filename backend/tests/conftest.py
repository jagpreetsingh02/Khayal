"""Test-wide safety net: a normal `pytest` run makes zero real API calls.

- Provider settings are forced to mock/off for the whole run, whatever `.env` says (your
  `.env` may select Groq / Twilio / ElevenLabs and hold real keys).
- Any socket connection to a non-local address fails the test immediately.
- Tests marked `@pytest.mark.live` call real APIs; they are skipped unless RUN_LIVE_TESTS=1
  (environment or .env), and only they may reach the network.
"""

import ipaddress
import os
import socket
from pathlib import Path

import pytest
from dotenv import dotenv_values

_ENV_FILES = [
    Path(__file__).resolve().parents[1] / ".env",
    Path(__file__).resolve().parents[2] / ".env",
]
LIVE = os.environ.get("RUN_LIVE_TESTS", "") == "1" or any(
    dotenv_values(f).get("RUN_LIVE_TESTS") == "1" for f in _ENV_FILES if f.is_file()
)

# Environment beats .env in pydantic-settings, so these win for every Settings() built here.
for name, value in {
    "APP_MODE": "mock",
    "CHANNEL": "simulator",
    "LLM_PROVIDER": "mock",
    "VOICE": "none",
    "CALL_PROVIDER": "none",
    "STT_PROVIDER": "none",
    # Secrets from your .env must not change test behaviour; tests pass their own.
    "SESSION_SECRET": "",
    "MCP_SHARED_SECRET": "",
}.items():
    os.environ[name] = value

_LOCAL_NAMES = {"localhost", "testserver", "ip6-localhost"}


def _is_local(host) -> bool:
    if host is None:
        return True
    if isinstance(host, bytes):
        host = host.decode()
    host = str(host)
    if host in _LOCAL_NAMES or host == "":
        return True
    try:
        return ipaddress.ip_address(host.split("%")[0]).is_loopback
    except ValueError:
        return False


class RealNetworkCall(RuntimeError):
    """A test tried to reach a real server. Mock it, or mark the test @pytest.mark.live."""


_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex
_real_getaddrinfo = socket.getaddrinfo


def _check(address) -> None:
    if isinstance(address, tuple) and address and not _is_local(address[0]):
        raise RealNetworkCall(f"real network call blocked in tests: {address[0]}")


def _guarded_connect(self, address):
    _check(address)
    return _real_connect(self, address)


def _guarded_connect_ex(self, address):
    _check(address)
    return _real_connect_ex(self, address)


def _guarded_getaddrinfo(host, *args, **kwargs):
    if not _is_local(host):
        raise RealNetworkCall(f"real network lookup blocked in tests: {host}")
    return _real_getaddrinfo(host, *args, **kwargs)


@pytest.fixture(autouse=True)
def _no_real_network(request, monkeypatch):
    if request.node.get_closest_marker("live"):
        yield
        return
    monkeypatch.setattr(socket.socket, "connect", _guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _guarded_connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", _guarded_getaddrinfo)
    yield


def pytest_collection_modifyitems(config, items):
    if LIVE:
        return
    skip = pytest.mark.skip(reason="live API test: set RUN_LIVE_TESTS=1 to run it")
    for item in items:
        if item.get_closest_marker("live"):
            item.add_marker(skip)
