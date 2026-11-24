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
    assert accuracy.main(["--reader", "mock", "--synthetic-only"]) == 0
    out = capsys.readouterr().out
    assert "== Synthetic" in out
    assert "Overall (all checked fields right): 100.0%" in out
    assert "No failures." in out


def test_real_only_with_no_real_photos_says_how_to_add_them(tmp_path: Path, capsys):
    assert accuracy.main(["--reader", "mock", "--real-only", "--real", str(tmp_path)]) == 1
    assert "samples/real_photos/README.md" in capsys.readouterr().out


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
    assert result["fields"]["medicine_id"] == [True]  # strips: only an id is compared
    accuracy.report("test", result, [])
    out = capsys.readouterr().out
    assert "50.0% (1/2)" in out and "systolic: expected 140, got 141" in out


def test_text_comparison_ignores_case_spaces_and_mg():
    assert accuracy.same("strength", "40", "40 mg")
    assert accuracy.same("brand", "Glycomet GP", "glycomet gp")
    assert not accuracy.same("strength", "40", "80")
    assert accuracy.same("glucose_value", 6.2, 6.2)
    assert not accuracy.same("pulse", None, 72)  # a number where none is shown is a miss


def test_mock_run_reports_zero_tokens(capsys):
    accuracy.main(["--reader", "mock", "--synthetic-only"])
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

        def read_strip(self, image, candidates):
            self.calls += 1
            self.tokens_used += 2400
            return super().read_strip(image, candidates)

    monkeypatch.setattr(accuracy, "make_reader", lambda name, d: CountingReader(d))
    assert accuracy.main(["--reader", "groq", "--yes", "--synthetic-only"]) == 0
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
    assert (
        "Misses: 2 = 1 retake/refused (asks for a new photo) + 1 wrong but confident + 0 reader"
        in out
    )
    assert "bp_a.jpg [retake]" in out and "glucose_a.jpg [WRONG BUT CONFIDENT]" in out
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
    assert accuracy.main(["--samples", str(tmp_path), "--kind", "meter", "--synthetic-only"]) == 0
    out = capsys.readouterr().out
    assert "photos: 2" in out and "strip " not in out


def test_match_filters_by_file_glob(tmp_path: Path, capsys):
    _two_meter_labels(tmp_path)
    args = ["--samples", str(tmp_path), "--match", "GLUCOSE_*", "--synthetic-only"]
    assert accuracy.main(args) == 0
    out = capsys.readouterr().out
    assert "photos: 1" in out and "glucose" in out and "bp " not in out


# --- real photos: labels.csv, import with EXIF removed, RESULTS.md ---


def _jpeg_with_exif(path: Path) -> None:
    from PIL import Image

    img = Image.new("RGB", (64, 48), (200, 200, 200))
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"  # Make
    exif[0x0132] = "2026:11:24 08:15:00"  # DateTime
    exif[0x8825] = {1: "N", 2: (12.0, 58.0, 0.0)}  # GPS
    img.save(path, "JPEG", exif=exif.tobytes())


def test_import_strips_exif_and_adds_csv_rows(tmp_path: Path, capsys):
    from PIL import Image

    src, real = tmp_path / "src", tmp_path / "real"
    src.mkdir()
    _jpeg_with_exif(src / "BP Morning 01.JPG")
    (src / "notes.txt").write_text("not a photo")
    with Image.open(src / "BP Morning 01.JPG") as im:
        assert len(im.getexif()) > 0  # the source really has metadata
    assert accuracy.main(["--import", str(src), "--real", str(real)]) == 0
    assert "Imported 1 photo(s)" in capsys.readouterr().out
    out = real / "bp_morning_01.jpg"
    with Image.open(out) as im:
        assert len(im.getexif()) == 0 and "exif" not in im.info
    rows = (real / "labels.csv").read_text().splitlines()
    assert rows == ["file,type,sys,dia,pulse,glucose,unit", "bp_morning_01.jpg,bp,,,,,"]
    accuracy.main(["--import", str(src), "--real", str(real)])  # again: nothing duplicated
    assert len((real / "labels.csv").read_text().splitlines()) == 2


def test_csv_labels_cover_bp_glucose_units_and_hi_lo(tmp_path: Path):
    (tmp_path / "labels.csv").write_text(
        "file,type,sys,dia,pulse,glucose,unit\n"
        "bp_1.jpg,bp,142,88,76,,\n"
        "bp_2.jpg,bp,120,80,,,\n"
        "g_1.jpg,glucose,,,,118,mg/dL\n"
        "g_2.jpg,glucose,,,,6.4,mmol/L\n"
        "g_3.jpg,glucose,,,,LO,\n"
        "todo.jpg,,,,,,\n"
    )
    labels = {lb.file: lb for lb in accuracy.load_real_labels(tmp_path)}
    assert set(labels) == {"bp_1.jpg", "bp_2.jpg", "g_1.jpg", "g_2.jpg", "g_3.jpg"}
    assert labels["bp_1.jpg"].meter().pulse == 76 and labels["bp_2.jpg"].meter().pulse is None
    assert labels["g_2.jpg"].meter().glucose_unit == "mmol/L"
    assert labels["g_3.jpg"].meter().display == "LO" and "display" in labels[
        "g_3.jpg"
    ].checked_fields()
    assert all(not lb.synthetic for lb in labels.values())


def test_real_and_synthetic_are_reported_separately_and_written(
    tmp_path: Path, capsys, monkeypatch
):
    """Fake reader: one real photo right, one refused, one wrong but confident."""
    real = tmp_path / "real"
    real.mkdir()
    for name in ("bp_ok.jpg", "bp_blur.jpg", "glucose_wrong.jpg", "bp_crazy.jpg"):
        (real / name).write_bytes(b"x")
    (real / "labels.csv").write_text(
        "file,type,sys,dia,pulse,glucose,unit\n"
        "bp_ok.jpg,bp,130,80,70,,\n"
        "bp_blur.jpg,bp,150,90,,,\n"
        "glucose_wrong.jpg,glucose,,,,118,mg/dL\n"
        "bp_crazy.jpg,bp,140,90,,,\n"
    )
    answers = {
        "bp_ok.jpg": MeterReading(
            device_type="bp", systolic=130, diastolic=80, pulse=70, legible=True
        ),
        "bp_blur.jpg": MeterReading(device_type="unknown", legible=False),
        "glucose_wrong.jpg": MeterReading(
            device_type="glucose", glucose_value=178, glucose_unit="mg/dL", legible=True
        ),
        # legible but impossible: the app's plausibility rules ask for a retake
        "bp_crazy.jpg": MeterReading(device_type="bp", systolic=40, diastolic=90, legible=True),
    }

    class Fake(MockReader):
        calls = None

        def read_meter(self, image):
            if image.filename in answers:
                return answers[image.filename]
            return super().read_meter(image)

    results = tmp_path / "RESULTS.md"
    monkeypatch.setattr(accuracy, "make_reader", lambda name, folder: Fake(folder))
    assert accuracy.main(["--real", str(real), "--results", str(results)]) == 0
    out = capsys.readouterr().out
    assert "== Synthetic" in out and "== Real · photos: 4 ==" in out
    real_part = out.split("== Real")[1]
    assert "2 retake/refused (asks for a new photo) + 1 wrong but confident" in real_part
    assert "glucose_wrong.jpg [WRONG BUT CONFIDENT]" in real_part
    md = results.read_text()
    assert "| Real | 4 |" in md and "| Synthetic |" in md
    assert "`glucose_wrong.jpg`: glucose_value 118.0 -> 178.0" in md


def test_results_file_not_written_without_real_photos(tmp_path: Path):
    results = tmp_path / "RESULTS.md"
    accuracy.main(["--real", str(tmp_path), "--results", str(results)])
    assert not results.exists()
