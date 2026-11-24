"""MockReader: answers from samples/labels.json. No network, no keys (APP_MODE=mock).

A photo is matched to a label by file name, or by content when it is one of the sample
files under another name. Unknown photos come back as unreadable, never as made-up values.
"""

from pathlib import Path

from app.reader.base import Reader
from app.reader.labels import SAMPLES_DIR, Label, load_labels, sha256
from app.reader.schema import (
    UNKNOWN_MEDICINE,
    ImageInput,
    MeterReading,
    StripCandidate,
    StripReading,
    pick_offered,
)
from app.reader.strips import catalog_id, match_strip


class MockReader(Reader):
    name = "mock"

    def __init__(self, samples_dir: Path = SAMPLES_DIR) -> None:
        self._by_name: dict[str, Label] = {}
        self._by_hash: dict[str, Label] = {}
        for label in load_labels(samples_dir):
            self._by_name[label.file.lower()] = label
            path = samples_dir / label.file
            if path.is_file():
                self._by_hash[sha256(path.read_bytes())] = label

    def _find(self, image: ImageInput) -> Label | None:
        if image.filename:
            label = self._by_name.get(Path(image.filename).name.lower())
            if label:
                return label
        return self._by_hash.get(sha256(image.data)) if image.data else None

    def read_meter(self, image: ImageInput) -> MeterReading:
        label = self._find(image)
        if label is None:
            return MeterReading(device_type="unknown", legible=False)
        if label.kind != "meter":  # a strip photo: legible, but not a meter
            return MeterReading(device_type="unknown", legible=True)
        return label.meter()

    def read_strip(self, image: ImageInput, candidates: list[StripCandidate]) -> StripReading:
        """Like a vision model would: pick the offered medicine the labelled strip shows
        (the parent's own entry first, else the curated-list row), or "unknown"."""
        label = self._find(image)
        if label is None:
            return StripReading(legible=False)
        if label.kind != "strip":
            return StripReading(legible=True)
        expected = label.strip()
        match = match_strip(*label.strip_brand())
        if match is None:
            return expected.model_copy(update={"medicine_id": UNKNOWN_MEDICINE})
        name = match.row.display_name.lower()
        own = next((c for c in candidates if c.label.lower().startswith(name + " (")), None)
        med_id = own.id if own and own.id.startswith("med_") else catalog_id(match.row)
        return pick_offered(expected.model_copy(update={"medicine_id": med_id}), candidates)
