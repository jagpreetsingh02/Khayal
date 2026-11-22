import asyncio

from fastapi.testclient import TestClient
from fastmcp import Client

from app.config import Settings
from app.main import app
from mcp_server.server import mcp


def test_health_endpoint_ok():
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


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
