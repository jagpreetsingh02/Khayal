"""Medicine strip matching: brand + strength read from a strip -> generic name (brief §5.2).

The reader returns what is printed ("Telma", "40"); this module maps it to a row of the
curated DEMO list (data/medicine_brands_demo.csv) with rapidfuzz. It never guesses a
strength: a brand that matches without a matching strength is "not sure", not a match.
"""

import csv
import re
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel
from rapidfuzz import fuzz

DATA_FILE = Path(__file__).parent / "data" / "medicine_brands_demo.csv"
MIN_BRAND_SCORE = 85.0
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


class BrandRow(BaseModel):
    brand: str
    strength: str
    generic: str
    generic_strength: str
    category: str

    @property
    def display_name(self) -> str:
        return f"{self.brand} {self.strength}"

    @property
    def generic_label(self) -> str:
        """ "Telmisartan 40 mg" or "Telmisartan 40 mg + Hydrochlorothiazide 12.5 mg"."""
        names = [n.strip() for n in self.generic.split("+")]
        doses = [d.strip() for d in self.generic_strength.split("+")]
        if len(names) != len(doses):
            return f"{self.generic} {self.generic_strength}"
        return " + ".join(f"{n} {d}" for n, d in zip(names, doses, strict=True))


class StripMatch(BaseModel):
    row: BrandRow
    score: float


def _numbers(text: str | None) -> tuple[float, ...]:
    return tuple(float(n) for n in _NUMBER.findall(text or ""))


def _brand_key(text: str) -> str:
    """Lowercase, numbers and punctuation removed: "Telma-H 40" -> "telma h"."""
    text = _NUMBER.sub(" ", text.lower())
    return " ".join(re.sub(r"[^a-z]+", " ", text).split())


@lru_cache
def load_brands(path: Path = DATA_FILE) -> tuple[BrandRow, ...]:
    with path.open(newline="", encoding="utf-8") as f:
        return tuple(BrandRow.model_validate(row) for row in csv.DictReader(f))


def match_strip(
    brand: str | None, strength: str | None, rows: tuple[BrandRow, ...] | None = None
) -> StripMatch | None:
    """Best row whose brand fuzzy-matches and whose strength matches exactly, else None."""
    if not brand or not _brand_key(brand):
        return None
    rows = rows if rows is not None else load_brands()
    wanted = _numbers(strength) or _numbers(brand)  # "Telma 40" with no separate strength
    if not wanted:
        return None
    key = _brand_key(brand)
    best: StripMatch | None = None
    for row in rows:
        if wanted not in (_numbers(row.strength), _numbers(row.generic_strength)):
            continue
        score = fuzz.ratio(key, _brand_key(row.brand))
        if score >= MIN_BRAND_SCORE and (best is None or score > best.score):
            best = StripMatch(row=row, score=score)
    return best
