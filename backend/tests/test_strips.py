"""Medicine strip matching against the curated demo CSV."""

import pytest

from app.reader.strips import load_brands, match_strip


def test_demo_csv_loads_about_fifty_bp_and_diabetes_brands():
    rows = load_brands()
    assert 45 <= len(rows) <= 70
    assert {r.category for r in rows} == {"bp", "diabetes"}
    assert len({(r.brand.lower(), r.strength) for r in rows}) == len(rows)  # no duplicates


@pytest.mark.parametrize(
    ("brand", "strength", "expected_name", "expected_label"),
    [
        ("Telma", "40", "Telma 40", "Telmisartan 40 mg"),
        ("TELMA", "40 mg", "Telma 40", "Telmisartan 40 mg"),
        ("Telma 40", None, "Telma 40", "Telmisartan 40 mg"),  # strength inside the brand
        (
            "Telma-H",
            "40/12.5",
            "Telma-H 40/12.5",
            "Telmisartan 40 mg + Hydrochlorothiazide 12.5 mg",
        ),
        ("Glycomet GP", "2", "Glycomet GP 2", "Glimepiride 2 mg + Metformin 500 mg"),
        ("Glycomet-GP", "2/500", "Glycomet GP 2", "Glimepiride 2 mg + Metformin 500 mg"),
        ("Glycomet", "500", "Glycomet 500", "Metformin 500 mg"),
        ("Amlong", "2.5", "Amlong 2.5", "Amlodipine 2.5 mg"),
        ("Jardiance", "10mg", "Jardiance 10", "Empagliflozin 10 mg"),
        ("Forxigo", "10", "Forxiga 10", "Dapagliflozin 10 mg"),  # one-letter OCR slip
    ],
)
def test_match_strip_finds_brand_and_generic(brand, strength, expected_name, expected_label):
    m = match_strip(brand, strength)
    assert m is not None
    assert m.row.display_name == expected_name
    assert m.row.generic_label == expected_label


@pytest.mark.parametrize(
    ("brand", "strength"),
    [
        ("Telma", "60"),  # brand known, strength not in the list: never guess a strength
        ("Telma", None),  # no strength at all
        ("Crocin", "500"),  # not in the demo list
        ("Telma H", "40"),  # combination brand with a single strength: not sure
        (None, "40"),
        ("", "40"),
        ("40", "40"),
    ],
)
def test_match_strip_refuses_to_guess(brand, strength):
    assert match_strip(brand, strength) is None
