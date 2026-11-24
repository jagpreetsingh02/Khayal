"""BedrockReader: placeholder for the AWS deployment (Phase 6)."""

from app.reader.base import Reader
from app.reader.schema import ImageInput, MeterReading, StripCandidate, StripReading

_MSG = (
    "BedrockReader is not implemented yet (planned for Phase 6, AWS). "
    "Use LLM_PROVIDER=mock (no keys) or LLM_PROVIDER=groq."
)


class BedrockReader(Reader):
    name = "bedrock"

    def __init__(self, model_id: str = "", region: str = "ap-south-1") -> None:
        self.model_id = model_id
        self.region = region

    def read_meter(self, image: ImageInput) -> MeterReading:
        raise NotImplementedError(_MSG)

    def read_strip(self, image: ImageInput, candidates: list[StripCandidate]) -> StripReading:
        raise NotImplementedError(_MSG)
