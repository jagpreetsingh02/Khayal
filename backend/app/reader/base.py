"""Reader interface: photo in, typed values out. A reader has no tools and no side effects.

Implementations: MockReader (labels from samples/labels.json, no keys), GroqReader (vision
model), BedrockReader (stub for the AWS deployment). Pick one with LLM_PROVIDER in .env.
"""

from abc import ABC, abstractmethod

from app.reader.schema import ImageInput, MeterReading, StripReading


class ReaderError(RuntimeError):
    """The reader could not produce a valid result (network, provider or parse failure).

    The conversation layer turns this into "please try again"; it never invents values.
    """


class Reader(ABC):
    name: str = "reader"

    @abstractmethod
    def read_meter(self, image: ImageInput) -> MeterReading:
        """Read a BP monitor or glucometer screen."""

    @abstractmethod
    def read_strip(self, image: ImageInput) -> StripReading:
        """Read the brand and strength printed on a medicine strip."""
