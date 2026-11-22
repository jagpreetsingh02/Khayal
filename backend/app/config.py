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
    voice: Literal["none", "elevenlabs"] = "none"

    database_url: str = "sqlite:///./care_circle.db"
    mcp_server_url: str = "http://localhost:8001/mcp"

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
    aws_region: str = "ap-south-1"
    bedrock_model_id: str = ""

    dashboard_demo_token: str = "change-me"
    tz: str = "Asia/Kolkata"


@lru_cache
def get_settings() -> Settings:
    return Settings()
