"""scripts/accuracy_test.py: scoring logic and a mock run (no network)."""

import importlib.util
import json
from pathlib import Path

from app.reader.mock_reader import MockReader
from app.reader.schema import ImageInput, MeterReading

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
spec = importlib.util.spec_from_file_location("accuracy_test", SCRIPTS / "accuracy_test.py")
accuracy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(accuracy)


def test_mock_reader_scores_100_percent_on_the_labels(capsys):
    assert accuracy.main(["--reader", "mock"]) == 0
    out = capsys.readouterr().out
    assert "Overall (all checked fields right): 100.0%" in out
    assert "No failures." in out


def test_real_only_with_no_real_photos_says_how_to_add_them(capsys):
    assert accuracy.main(["--reader", "mock", "--real-only"]) == 1
    assert "samples/README.md" in capsys.readouterr().out


def test_failures_are_scored_and_listed(tmp_path: Path, capsys):
    labels = {
        "samples": [
            {
                "file": "bp_a.jpg",
                "kind": "meter",
                "expected": {"device_type": "bp", "systolic": 140, "diastolic": 90},
            },
            {
                "file": "strip_a.jpg",
                "kind": "strip",
                "expected": {"brand": "Telma", "strength": "40"},
            },
        ]
    }
    (tmp_path / "labels.json").write_text(json.dumps(labels))

    class OffByOne(MockReader):
        def read_meter(self, image: ImageInput) -> MeterReading:
            r = super().read_meter(image)
            return r.model_copy(update={"systolic": (r.systolic or 0) + 1})

    from app.reader.labels import load_labels

    result = accuracy.evaluate(OffByOne(tmp_path), load_labels(tmp_path), tmp_path)
    assert result["fields"]["systolic"] == [False]
    assert result["fields"]["diastolic"] == [True]
    assert result["fields"]["brand"] == [True]
    accuracy.report("test", result, [])
    out = capsys.readouterr().out
    assert "50.0% (1/2)" in out and "systolic: expected 140, got 141" in out


def test_strip_comparison_ignores_case_spaces_and_mg():
    assert accuracy.same("strength", "40", "40 mg")
    assert accuracy.same("brand", "Glycomet GP", "glycomet gp")
    assert not accuracy.same("strength", "40", "80")
    assert accuracy.same("glucose_value", 6.2, 6.2)
    assert not accuracy.same("pulse", None, 72)  # a number where none is shown is a miss
