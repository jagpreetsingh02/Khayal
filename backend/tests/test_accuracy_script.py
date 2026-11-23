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


def test_mock_run_reports_zero_tokens(capsys):
    accuracy.main(["--reader", "mock"])
    assert "Tokens used: 0 (mock reader, no API calls)" in capsys.readouterr().out


def test_real_reader_warns_and_stops_without_confirmation(monkeypatch, capsys):
    made = []
    monkeypatch.setattr(accuracy, "make_reader", lambda *a: made.append(a))
    monkeypatch.setattr(accuracy.sys.stdin, "isatty", lambda: False)
    assert accuracy.main(["--reader", "groq"]) == 2
    out = capsys.readouterr().out
    assert "WARNING" in out and "tokens" in out and "--yes" in out
    assert made == []  # no reader built, no API call


def test_real_reader_with_yes_reports_api_tokens(monkeypatch, capsys):
    class CountingReader(MockReader):
        calls, tokens_used = 0, 0

        def read_meter(self, image):
            self.calls += 1
            self.tokens_used += 2400
            return super().read_meter(image)

        def read_strip(self, image):
            self.calls += 1
            self.tokens_used += 2400
            return super().read_strip(image)

    monkeypatch.setattr(accuracy, "make_reader", lambda name, d: CountingReader(d))
    assert accuracy.main(["--reader", "groq", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "Estimated ~" in out and "reported by the API" in out


def _two_meter_labels(tmp_path: Path) -> None:
    labels = {
        "samples": [
            {
                "file": "bp_a.jpg",
                "kind": "meter",
                "expected": {"device_type": "bp", "systolic": 140, "diastolic": 90},
            },
            {
                "file": "glucose_a.jpg",
                "kind": "meter",
                "expected": {
                    "device_type": "glucose",
                    "glucose_value": 120,
                    "glucose_unit": "mg/dL",
                },
            },
            {
                "file": "strip_a.jpg",
                "kind": "strip",
                "expected": {"brand": "Telma", "strength": "40"},
            },
        ]
    }
    (tmp_path / "labels.json").write_text(json.dumps(labels))


def test_said_unreadable_is_counted_apart_from_read_wrong(tmp_path: Path, capsys):
    _two_meter_labels(tmp_path)

    class Mixed(MockReader):
        def read_meter(self, image: ImageInput) -> MeterReading:
            r = super().read_meter(image)
            if r.device_type == "bp":  # refuses to guess
                return MeterReading(device_type="unknown", legible=False)
            return r.model_copy(update={"glucose_value": 121})  # wrong number

    from app.reader.labels import load_labels

    result = accuracy.evaluate(Mixed(tmp_path), load_labels(tmp_path), tmp_path)
    accuracy.report("test", result, [])
    out = capsys.readouterr().out
    assert "Misses: 2 = 1 said unreadable (asks for a new photo) + 1 read wrong + 0 reader" in out
    assert "bp_a.jpg [said unreadable]" in out
    lines = {" ".join(line.split()) for line in out.splitlines()}
    assert "bp 0.0% (0/1)" in lines and "strip 100.0% (1/1)" in lines


def test_pace_sleeps_between_photos_only(tmp_path: Path):
    _two_meter_labels(tmp_path)
    from app.reader.labels import load_labels

    naps: list[float] = []
    accuracy.evaluate(MockReader(tmp_path), load_labels(tmp_path), tmp_path, 21, naps.append)
    assert naps == [21, 21]  # 3 photos -> 2 waits, none before the first


def test_kind_filter_keeps_only_that_kind(tmp_path: Path, capsys):
    _two_meter_labels(tmp_path)
    assert accuracy.main(["--samples", str(tmp_path), "--kind", "meter"]) == 0
    out = capsys.readouterr().out
    assert "photos: 2" in out and "strip " not in out
