"""Reader: photo -> typed values (§5.2, §6.2). Pick the provider with LLM_PROVIDER in .env."""

from app.config import Settings, get_settings
from app.reader.base import Reader, ReaderError


def get_reader(settings: Settings | None = None) -> Reader:
    settings = settings or get_settings()
    if settings.llm_provider == "groq":
        from app.reader.groq_reader import GroqReader

        return GroqReader(settings.groq_api_key, settings.groq_vision_model)
    if settings.llm_provider == "bedrock":
        from app.reader.bedrock_reader import BedrockReader

        return BedrockReader(settings.bedrock_model_id, settings.aws_region)
    from app.reader.mock_reader import MockReader

    return MockReader()


__all__ = ["Reader", "ReaderError", "get_reader"]
