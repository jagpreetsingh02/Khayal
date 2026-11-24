import asyncio
import socket

from fastapi.testclient import TestClient
from fastmcp import Client
from fastmcp.utilities.tests import run_server_async

from app.alerts.notifier import StoreNotifier
from app.config import Settings
from app.main import create_app
from mcp_server.server import create_server, mcp
from tests.channel_helpers import family, service


def _health(svc, *, close_store: bool = False):
    with TestClient(create_app(svc, tick_seconds=0)) as client:
        if close_store:
            svc.store.close()
        response = client.get("/health")
    return response.status_code, response.json()


def _closed_port_url() -> str:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    return f"http://127.0.0.1:{port}/mcp"  # nothing listens here once the socket closes


def test_health_checks_the_database_and_mcp():
    store, *_ = family("simulator", "+919800000001", "simulator", "+2")
    code, body = _health(service(store))
    assert code == 200
    assert body == {"status": "ok", "mode": "mock", "database": "ok", "mcp": "in-process"}


def test_health_reports_a_reachable_mcp_server_over_http():
    store, *_ = family("simulator", "+919800000001", "simulator", "+2")
    svc = service(store)
    server = create_server(store, StoreNotifier(lambda: store), secret="s" * 40)

    async def main():
        async with run_server_async(server) as url:
            svc.agent.mcp = url  # no bearer: the 401 still proves the server answers
            return await asyncio.to_thread(_health, svc)

    code, body = asyncio.run(main())
    assert code == 200 and body["status"] == "ok" and body["mcp"] == "reachable"


def test_health_is_degraded_not_failing_when_mcp_is_down():
    store, *_ = family("simulator", "+919800000001", "simulator", "+2")
    svc = service(store)
    svc.agent.mcp = _closed_port_url()
    code, body = _health(svc)
    assert code == 200
    assert body["status"] == "degraded" and body["mcp"] == "unreachable"
    assert body["database"] == "ok"


def test_health_fails_without_a_database():
    store, *_ = family("simulator", "+919800000001", "simulator", "+2")
    code, body = _health(service(store), close_store=True)
    assert code == 503 and body["status"] == "error" and body["database"] == "error"


def test_defaults_need_no_keys():
    settings = Settings(_env_file=None)
    assert settings.app_mode == "mock"
    assert settings.llm_provider == "mock"
    assert settings.channel == "simulator"


def test_mcp_server_exposes_no_approve_refill_tool():
    async def tool_names() -> set[str]:
        async with Client(mcp) as client:
            return {tool.name for tool in await client.list_tools()}

    assert "approve_refill" not in asyncio.run(tool_names())
