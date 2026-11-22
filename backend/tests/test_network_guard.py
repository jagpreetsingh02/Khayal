"""The test suite itself makes zero real API calls (see tests/conftest.py)."""

import socket

import httpx
import pytest

from app.config import Settings, get_settings


def test_real_hosts_are_blocked():
    with pytest.raises(RuntimeError, match="blocked"):
        httpx.get("https://api.groq.com/openai/v1/models", timeout=2)
    with pytest.raises(RuntimeError, match="blocked"):
        socket.create_connection(("93.184.216.34", 443), timeout=2)


def test_localhost_is_allowed():
    with socket.socket() as s:  # a closed local port: refused, not blocked
        assert s.connect_ex(("127.0.0.1", 9)) != 0


def test_providers_are_forced_to_mock_whatever_dotenv_says():
    get_settings.cache_clear()
    s = Settings()
    assert (s.app_mode, s.llm_provider, s.channel, s.voice) == ("mock", "mock", "simulator", "none")


@pytest.mark.live
def test_live_tests_are_opt_in():
    # Runs only with RUN_LIVE_TESTS=1; a normal run reports it as skipped.
    assert True
