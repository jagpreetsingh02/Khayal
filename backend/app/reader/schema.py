"""What a reader returns (brief §6.2), as typed models.

The vision model fills these and nothing else. Values are data read from a screen or a
strip; they are never trusted until the parent confirms the read-back.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

DeviceType = Literal["bp", "glucose", "unknown"]
GlucoseUnit = Literal["mg/dL", "mmol/L"]
Issue = Literal["glare", "angle", "partial", "blur"]


class ImageInput(BaseModel):
    """A photo as received from a channel. `filename` is optional (the mock reader uses it)."""

    data: bytes
    filename: str | None = None


class MeterReading(BaseModel):
    """§6.2 vision output for a BP monitor or glucometer screen. Nulls for missing fields."""

    model_config = ConfigDict(extra="forbid")

    device_type: DeviceType
    systolic: int | None = None
    diastolic: int | None = None
    pulse: int | None = None
    glucose_value: float | None = None
    glucose_unit: GlucoseUnit | None = None
    legible: bool
    issues: list[Issue] = Field(default_factory=list)


class StripReading(BaseModel):
    """Brand and strength printed on a medicine strip, e.g. brand "Telma", strength "40"."""

    model_config = ConfigDict(extra="forbid")

    brand: str | None = None
    strength: str | None = None
    legible: bool
    issues: list[Issue] = Field(default_factory=list)


def _nullable(json_type: str, **extra: Any) -> dict[str, Any]:
    return {"type": [json_type, "null"], **extra}


_ISSUES = {
    "type": "array",
    "items": {"type": "string", "enum": ["glare", "angle", "partial", "blur"]},
}

# Strict JSON schemas for providers with structured outputs (every field required,
# additionalProperties false). Tests check they list exactly the model fields.
METER_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "device_type": {"type": "string", "enum": ["bp", "glucose", "unknown"]},
        "systolic": _nullable("integer"),
        "diastolic": _nullable("integer"),
        "pulse": _nullable("integer"),
        "glucose_value": _nullable("number"),
        "glucose_unit": {"type": ["string", "null"], "enum": ["mg/dL", "mmol/L", None]},
        "legible": {"type": "boolean"},
        "issues": _ISSUES,
    },
    "required": [
        "device_type",
        "systolic",
        "diastolic",
        "pulse",
        "glucose_value",
        "glucose_unit",
        "legible",
        "issues",
    ],
    "additionalProperties": False,
}

STRIP_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "brand": _nullable("string"),
        "strength": _nullable("string"),
        "legible": {"type": "boolean"},
        "issues": _ISSUES,
    },
    "required": ["brand", "strength", "legible", "issues"],
    "additionalProperties": False,
}
