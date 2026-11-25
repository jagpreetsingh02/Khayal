"""Settings loaded from environment / .env. Mock mode is the default and needs no keys."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    app_mode: Literal["mock", "dev", "aws"] = "mock"
    channel: Literal["simulator", "telegram", "twilio"] = "simulator"
    llm_provider: Literal["mock", "groq", "bedrock"] = "mock"
    storage: Literal["sqlite", "dynamodb"] = "sqlite"
    # Voice notes: mock = "voice note" bubbles spoken by the simulator's browser (no keys);
    # elevenlabs = real TTS/STT (needs ELEVENLABS_API_KEY + ELEVENLABS_VOICE_ID, else text only).
    voice: Literal["none", "mock", "elevenlabs"] = "mock"
    # Red-alert phone call to family: mock = "Incoming call" card in the simulator.
    call_provider: Literal["none", "mock", "twilio"] = "mock"

    database_url: str = "sqlite:///./khayal.db"
    mcp_server_url: str = "http://localhost:8001/mcp"
    # Interface the MCP server binds to. Localhost by default; Docker Compose sets 0.0.0.0 for
    # the mcp container only (its port is never published; the bearer secret is still required).
    mcp_host: str = "127.0.0.1"
    # Extra Host header names the MCP server accepts (comma separated). localhost, 127.0.0.1,
    # ::1, the compose service name "mcp" and the host of MCP_SERVER_URL are always allowed.
    mcp_allowed_hosts: str = ""

    groq_api_key: str = ""
    groq_vision_model: str = ""
    groq_agent_model: str = ""
    telegram_bot_token: str = ""
    telegram_mode: Literal["polling", "webhook"] = "polling"
    telegram_webhook_secret: str = ""
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_from: str = "whatsapp:+14155238886"
    twilio_template_content_sid: str = ""  # Sandbox template for messages outside 24 h
    public_base_url: str = ""  # URL Twilio calls (signature check), e.g. https://x.ngrok.app
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = ""
    elevenlabs_tts_model: str = "eleven_v4_turbo"  # lists hi + ta (docs, Nov 2026)
    elevenlabs_stt_model: str = "scribe_v3"
    elevenlabs_daily_char_budget: int = 1500  # TTS characters/day; over it -> text only
    voice_cache_dir: str = "./.voice_cache"
    twilio_call_from: str = ""  # a Twilio voice number (+1...) for CALL_PROVIDER=twilio
    aws_region: str = "ap-south-1"
    bedrock_model_id: str = ""

    dashboard_demo_token: str = "change-me"  # demo login, mock mode only
    # Signs dashboard sessions and peppers login-code hashes. Empty in mock mode: a random
    # one per start (sessions end on restart). Required in dev/aws.
    session_secret: str = ""
    session_ttl_hours: float = 12
    # How the API proves itself to the MCP server. token (default): a short-lived HS256 JWT
    # per call (sub = member id, role, scopes), signed with MCP_TOKEN_SECRET. secret (legacy,
    # APP_MODE=mock only): "Authorization: Bearer <MCP_SHARED_SECRET>", sender from _meta.
    mcp_auth_mode: Literal["secret", "token"] = "token"
    # 32+ characters. scripts/start.py / Docker make a random one per run in mock mode.
    mcp_token_secret: str = ""
    mcp_shared_secret: str = ""
    # scripts/family_gateway.py (local demo bridge): the family member its tokens are for.
    family_gateway_member_id: str = ""
    tz: str = "Asia/Kolkata"
    # Finished inbox rows (metadata only: payloads are blanked when done) and failed ones
    # (payload kept for inspection) are deleted after this many days.
    inbox_retention_days: float = 7


@lru_cache
def get_settings() -> Settings:
    return Settings()
