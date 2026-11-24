"""Ground-truth labels for sample photos (`samples/labels.json`).

Used by MockReader (returns the label as if it had read the photo) and by
scripts/accuracy_test.py (compares a real reader against the labels).
Format and naming rules: samples/README.md.
"""

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.reader.schema import UNKNOWN_MEDICINE, MeterReading, StripReading

SAMPLES_DIR = Path(__file__).resolve().parents[3] / "samples"
METER_FIELDS = ("device_type", "systolic", "diastolic", "pulse", "glucose_value", "glucose_unit")
STRIP_FIELDS = ("medicine_id",)


class Label(BaseModel):
    file: str
    kind: Literal["meter", "strip"]
    synthetic: bool = False
    note: str | None = None
    expected: dict[str, Any] = Field(default_factory=dict)

    def meter(self) -> MeterReading:
        return MeterReading.model_validate({"legible": True, **self.expected})

    def strip_brand(self) -> tuple[str | None, str | None]:
        """What the strip label says is printed (brand, strength): ground truth only."""
        return self.expected.get("brand"), self.expected.get("strength")

    def strip(self) -> StripReading:
        """The expected reader answer: the curated-list id of the labelled brand + strength."""
        from app.reader.strips import catalog_id, match_strip

        match = match_strip(*self.strip_brand())
        med_id = catalog_id(match.row) if match else UNKNOWN_MEDICINE
        legible = bool(self.expected.get("legible", True))
        return StripReading(medicine_id=med_id, legible=legible)

    def checked_fields(self) -> tuple[str, ...]:
        """Fields the accuracy test compares. Value fields missing from a meter label mean
        "not on the screen" (null); `legible` is compared only when the label sets it."""
        base: tuple[str, ...] = METER_FIELDS if self.kind == "meter" else STRIP_FIELDS
        if self.kind == "meter" and "display" in self.expected:
            base = base + ("display",)
        return base + (("legible",) if "legible" in self.expected else ())


def load_labels(samples_dir: Path = SAMPLES_DIR) -> list[Label]:
    path = samples_dir / "labels.json"
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    labels = [Label.model_validate(item) for item in raw.get("samples", [])]
    # Validate the expected values now so a typo fails loudly, not mid-demo.
    for label in labels:
        label.meter() if label.kind == "meter" else label.strip()
    return labels


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
