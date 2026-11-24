"""What a reader returns (brief §6.2), as typed models.

The vision model fills these and nothing else. Values are data read from a screen or a
strip; they are never trusted until the parent confirms the read-back.

No free-text field anywhere: extra fields are forbidden, numbers are numbers, and a strip
photo yields only a medicine id chosen from the list the server offered (or "unknown"),
never the text printed on the strip. Text inside a photo therefore has nowhere to go.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

DeviceType = Literal["bp", "glucose", "unknown"]
GlucoseUnit = Literal["mg/dL", "mmol/L"]
Issue = Literal["glare", "angle", "partial", "blur"]
Display = Literal["HI", "LO"]
UNKNOWN_MEDICINE = "unknown"
# Ids the server hands out: a parent's medicine ("med_<12 hex>") or a row of the curated
# brand list ("cat:<slug>"). The pattern alone already rules out free text.
MEDICINE_ID_PATTERN = r"^(?:med_[0-9a-f]{12}|cat:[a-z0-9-]{1,40}|unknown)$"


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
    display: Display | None = None  # glucometer shows HI / LO instead of a number
    legible: bool
    issues: list[Issue] = Field(default_factory=list)


class StripCandidate(BaseModel):
    """One medicine the reader may pick: an id plus the name the server already knows."""

    id: str = Field(pattern=MEDICINE_ID_PATTERN)
    label: str  # e.g. "Telma 40 (Telmisartan 40 mg)": from our own data, never from OCR


class StripReading(BaseModel):
    """Which of the offered medicines the strip shows. Only an id, never printed text."""

    model_config = ConfigDict(extra="forbid")

    medicine_id: str = Field(default=UNKNOWN_MEDICINE, pattern=MEDICINE_ID_PATTERN)
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
        "display": {"type": ["string", "null"], "enum": ["HI", "LO", None]},
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
        "display",
        "legible",
        "issues",
    ],
    "additionalProperties": False,
}


def strip_json_schema(candidates: list[StripCandidate]) -> dict[str, Any]:
    """Strict schema for one strip photo: medicine_id is an enum of the offered ids."""
    ids = [c.id for c in candidates if c.id != UNKNOWN_MEDICINE] + [UNKNOWN_MEDICINE]
    return {
        "type": "object",
        "properties": {
            "medicine_id": {"type": "string", "enum": ids},
            "legible": {"type": "boolean"},
            "issues": _ISSUES,
        },
        "required": ["medicine_id", "legible", "issues"],
        "additionalProperties": False,
    }


def pick_offered(reading: StripReading, candidates: list[StripCandidate]) -> StripReading:
    """An id the server did not offer is treated as "unknown" (never trusted)."""
    if reading.medicine_id in {c.id for c in candidates}:
        return reading
    return reading.model_copy(update={"medicine_id": UNKNOWN_MEDICINE})
