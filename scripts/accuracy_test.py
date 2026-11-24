"""Measure a reader on the synthetic set (labels.json) and on real photos (labels.csv).

    cd backend
    uv run python ../scripts/accuracy_test.py                       # mock reader, both sets
    uv run python ../scripts/accuracy_test.py --reader groq         # needs GROQ_API_KEY
    uv run python ../scripts/accuracy_test.py --reader groq --real-only --yes --pace 21
    uv run python ../scripts/accuracy_test.py --samples DIR --kind meter --match 'glucose_*'
    uv run python ../scripts/accuracy_test.py --import ~/Downloads/meter_photos   # add photos

Two sets, always reported separately:
- synthetic: `--samples` (default samples/) with labels.json (drawn or AI-generated images);
- real: `--real` (default samples/real_photos/) with labels.csv, columns
  file,type,sys,dia,pulse,glucose,unit (type bp | glucose; glucose may be HI or LO; leave a
  cell empty when the screen does not show it). See samples/real_photos/README.md.

For each set: exact match per field, "overall" (every checked field right), and what happened
to the misses, from the parent's point of view:
- retake / refused: the reader said it could not read the photo, or the app's plausibility
  rules refused the numbers. The parent is asked for a new photo: the safe outcome.
- wrong but confident: a legible, plausible but wrong value. It would be read back to the
  parent ("I read 142/88, correct?"), whose "2" is then the last check. This is the number
  that matters most.
- reader errors (network, quota, invalid JSON).

When the real set has photos, the result is written to samples/real_photos/RESULTS.md
(`--results` to change, `--no-results` to skip). `--import DIR` copies photos into the real
folder with all metadata (EXIF: GPS, phone model, time) removed and adds empty CSV rows.

A real reader spends API quota: the script estimates the tokens first and asks to continue
(`--yes` skips the question; without a terminal it stops unless `--yes` is given). Tests never
call Groq; the live run is manual.
"""

import argparse
import csv
import re
import sys
import time
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.config import get_settings  # noqa: E402
from app.reader import Reader, ReaderError  # noqa: E402
from app.reader.labels import SAMPLES_DIR, Label, load_labels  # noqa: E402
from app.reader.rules import check_bp, check_glucose, check_glucose_unit, to_mgdl  # noqa: E402
from app.reader.schema import ImageInput, MeterReading, StripCandidate  # noqa: E402
from app.reader.strips import catalog_id, catalog_label, load_brands  # noqa: E402

REAL_DIR = SAMPLES_DIR / "real_photos"
CSV_NAME = "labels.csv"
CSV_COLUMNS = ("file", "type", "sys", "dia", "pulse", "glucose", "unit")
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
# Measured on Groq qwen/qwen3.8-27b (Nov 2026): ~2.4k input+output tokens per downscaled photo.
EST_TOKENS_PER_PHOTO = 2400
GROQ_FREE_TOKENS_PER_DAY = 200_000  # free tier limit seen in Groq's 429 message, Nov 2026


def make_reader(name: str, samples_dir: Path) -> Reader:
    if name == "mock":
        from app.reader.mock_reader import MockReader

        return MockReader(samples_dir)
    if name == "groq":
        from app.reader.groq_reader import GroqReader

        s = get_settings()
        # Batch run: waiting a minute for the free-tier rate limit is fine here.
        return GroqReader(s.groq_api_key, s.groq_vision_model, max_wait=65)
    raise SystemExit(f"Unknown reader {name!r} (use mock or groq).")


def strip_candidates() -> list[StripCandidate]:
    """A strip photo is scored against the curated list: the id is all a reader returns."""
    return [StripCandidate(id=catalog_id(r), label=catalog_label(r)) for r in load_brands()]


# --- real photos: CSV labels ---


def _num(cell: str | None) -> float | None:
    cell = (cell or "").strip()
    return float(cell) if cell else None


def label_from_row(row: dict[str, str]) -> Label:
    """One labels.csv row -> a meter Label. Empty cells mean "not shown on the screen"."""
    kind = (row.get("type") or "").strip().lower()
    if kind not in ("bp", "glucose"):
        raise ValueError(f"{row.get('file')}: type must be bp or glucose, got {kind!r}")
    expected: dict[str, Any] = {"device_type": kind}
    if kind == "bp":
        for col, field in (("sys", "systolic"), ("dia", "diastolic"), ("pulse", "pulse")):
            value = _num(row.get(col))
            if value is not None:
                expected[field] = int(value)
    else:
        glucose = (row.get("glucose") or "").strip()
        if glucose.upper() in ("HI", "LO"):
            expected["display"] = glucose.upper()
        elif glucose:
            expected["glucose_value"] = float(glucose)
            unit = (row.get("unit") or "").strip()
            expected["glucose_unit"] = "mmol/L" if unit.lower().startswith("mmol") else "mg/dL"
    return Label(file=row["file"].strip(), kind="meter", synthetic=False, expected=expected)


def load_real_labels(real_dir: Path) -> list[Label]:
    path = real_dir / CSV_NAME
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if (r.get("file") or "").strip()]
    labels = []
    for row in rows:
        if not (row.get("type") or "").strip():
            continue  # imported but not labelled yet
        labels.append(label_from_row(row))
    return labels


def import_photos(src: Path, real_dir: Path) -> list[str]:
    """Copy photos into the real set as JPEG without any metadata (EXIF GPS, device, time),
    upright, and add an empty labels.csv row for each new one. Returns the new file names."""
    from PIL import Image, ImageOps

    real_dir.mkdir(parents=True, exist_ok=True)
    csv_path = real_dir / CSV_NAME
    known: set[str] = set()
    if csv_path.is_file():
        with csv_path.open(newline="", encoding="utf-8") as f:
            known = {r["file"] for r in csv.DictReader(f)}
    added: list[str] = []
    for path in sorted(src.iterdir()):
        if path.suffix.lower() not in IMAGE_SUFFIXES or not path.is_file():
            continue
        name = re.sub(r"[^a-z0-9_-]+", "_", path.stem.lower()).strip("_") + ".jpg"
        if name in known or (real_dir / name).exists():
            continue
        with Image.open(path) as im:
            upright = ImageOps.exif_transpose(im).convert("RGB")
            clean = Image.new("RGB", upright.size)
            clean.paste(upright)  # pixels only: no EXIF, ICC, XMP or comments carried over
            clean.save(real_dir / name, "JPEG", quality=92)
        added.append(name)
    if added:
        new_file = not csv_path.is_file()
        with csv_path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if new_file:
                writer.writerow(CSV_COLUMNS)
            for name in added:
                guess = "bp" if name.startswith("bp") else "glucose" if "gluc" in name else ""
                writer.writerow([name, guess, "", "", "", "", ""])
    return added


# --- scoring ---


def _norm_text(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.lower().replace(" ", "")
    return text[:-2] if text.endswith("mg") else text


def same(field: str, expected: Any, got: Any) -> bool:
    if field in ("brand", "strength", "medicine_id"):
        return _norm_text(expected) == _norm_text(got)
    if isinstance(expected, int | float) and isinstance(got, int | float):
        return abs(float(expected) - float(got)) < 1e-6
    return expected == got


def app_would_refuse(got: MeterReading) -> list[str]:
    """The plausibility rules the app applies before any read-back (no history)."""
    if got.device_type == "bp":
        return check_bp(got.systolic, got.diastolic, got.pulse)
    if got.device_type == "glucose":
        if got.display:
            return []
        problems = check_glucose_unit(got.glucose_value, got.glucose_unit)
        value = got.glucose_value
        return problems or check_glucose(to_mgdl(value, got.glucose_unit) if value else None)
    return []


def evaluate(
    reader: Reader,
    labels: list[Label],
    samples_dir: Path,
    pace: float = 0.0,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    field_hits: dict[str, list[bool]] = defaultdict(list)
    results = []
    candidates = strip_candidates()
    for i, label in enumerate(labels):
        if pace and i:
            sleep(pace)
        path = samples_dir / label.file
        data = path.read_bytes() if path.is_file() else b""
        image = ImageInput(data=data, filename=label.file)
        expected = label.meter() if label.kind == "meter" else label.strip()
        started = time.perf_counter()
        got: Any
        try:
            if label.kind == "meter":
                got = reader.read_meter(image)
            else:
                got = reader.read_strip(image, candidates)
            error = None
        except ReaderError as exc:
            got, error = None, str(exc)
        seconds = time.perf_counter() - started
        misses = []
        for field in label.checked_fields():
            want = getattr(expected, field)
            have = getattr(got, field) if got is not None else "<error>"
            ok = got is not None and same(field, want, have)
            field_hits[field].append(ok)
            if not ok:
                misses.append((field, want, have))
        device = expected.device_type if label.kind == "meter" else "strip"
        # legible=false where the label expects a readable photo: the reader refused to guess.
        said_unreadable = got is not None and expected.legible and (
            not got.legible or (label.kind == "meter" and got.device_type == "unknown")
        )
        implausible = (
            got is not None
            and label.kind == "meter"
            and not said_unreadable
            and bool(app_would_refuse(got))
        )
        refused = bool(misses) and (said_unreadable or implausible)
        results.append(
            {
                "file": label.file,
                "device": device,
                "synthetic": label.synthetic,
                "misses": misses,
                "error": error,
                "refused": refused,
                "implausible": implausible,
                "wrong_confident": bool(misses) and not refused and error is None,
                "seconds": seconds,
            }
        )
    return {"fields": dict(field_hits), "results": results}


def pct(hits: list[bool]) -> str:
    return f"{100 * sum(hits) / len(hits):5.1f}% ({sum(hits)}/{len(hits)})" if hits else "n/a"


def counts(results: list[dict[str, Any]]) -> dict[str, int]:
    missed = [r for r in results if r["misses"]]
    return {
        "photos": len(results),
        "correct": len(results) - len(missed),
        "retake": sum(1 for r in missed if r["refused"]),
        "wrong_confident": sum(1 for r in missed if r["wrong_confident"]),
        "errors": sum(1 for r in missed if r["error"]),
    }


def confirm_real_run(reader_name: str, photos: int, assume_yes: bool) -> bool:
    """Warn before spending real API quota; True to go ahead."""
    estimate = photos * EST_TOKENS_PER_PHOTO
    share = 100 * estimate / GROQ_FREE_TOKENS_PER_DAY
    print(
        f"WARNING: --reader {reader_name} sends {photos} photo(s) to the real API.\n"
        f"  Estimated ~{estimate:,} tokens (~{EST_TOKENS_PER_PHOTO:,} per photo), about "
        f"{share:.0f}% of a {GROQ_FREE_TOKENS_PER_DAY:,} tokens/day free quota.\n"
        "  The free tier also allows only ~3 photos a minute, so this can take a while."
    )
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        print("Not run: no terminal to confirm. Re-run with --yes to spend the quota.")
        return False
    return input("Continue? [y/N] ").strip().lower() in ("y", "yes")


def token_line(readers: list[Reader], photos: int) -> str:
    if any(getattr(r, "calls", None) is None for r in readers):
        return "Tokens used: 0 (mock reader, no API calls)"
    calls = sum(getattr(r, "calls", 0) for r in readers)
    used = sum(getattr(r, "tokens_used", 0) for r in readers)
    if used:
        per = used / calls if calls else 0
        return f"Tokens used: {used:,} reported by the API over {calls} call(s) (~{per:,.0f} each)"
    return (
        f"Tokens used: not reported by the API; estimate ~{photos * EST_TOKENS_PER_PHOTO:,} "
        f"({calls} call(s))"
    )


def report(set_name: str, outcome: dict[str, Any], skipped: list[str]) -> None:
    results = outcome["results"]
    print(f"\n== {set_name} · photos: {len(results)} ==")
    if skipped:
        print(f"Skipped (file missing): {', '.join(skipped)}")
    print("Per field (exact match):")
    for field, hits in outcome["fields"].items():
        print(f"  {field:<14} {pct(hits)}")
    overall = [not r["misses"] for r in results]
    print(f"Overall (all checked fields right): {pct(overall)}")
    by_device: dict[str, list[bool]] = defaultdict(list)
    for r in results:
        by_device[r["device"]].append(not r["misses"])
    for device, hits in by_device.items():
        print(f"  {device:<14} {pct(hits)}")
    c = counts(results)
    print(
        f"Misses: {c['photos'] - c['correct']} = {c['retake']} retake/refused (asks for a new "
        f"photo) + {c['wrong_confident']} wrong but confident + {c['errors']} reader error(s)"
    )
    if results:
        avg = sum(r["seconds"] for r in results) / len(results)
        print(f"Average time per photo: {avg:.2f} s")
    failures = [r for r in results if r["misses"]]
    if not failures:
        print("No failures.")
        return
    print("Failures:")
    for r in failures:
        if r["error"]:
            print(f"  {r['file']}: reader error: {r['error']}")
            continue
        detail = "; ".join(f"{f}: expected {w!r}, got {h!r}" for f, w, h in r["misses"])
        tag = " [retake]" if r["refused"] else " [WRONG BUT CONFIDENT]"
        print(f"  {r['file']}{tag}: {detail}")


def results_markdown(reader_name: str, sets: dict[str, dict[str, Any]]) -> str:
    when = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Reader accuracy: real photos vs synthetic",
        "",
        f"Generated by `scripts/accuracy_test.py` on {when} with reader `{reader_name}`.",
        "Real = photos of real home meters (labels.csv). Synthetic = drawn / AI-generated",
        "images (labels.json): they say little about real accuracy.",
        "",
        "| Set | Photos | All fields right | Retake / refused | Wrong but confident | Errors |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name, outcome in sets.items():
        c = counts(outcome["results"])
        lines.append(
            f"| {name} | {c['photos']} | {pct([not r['misses'] for r in outcome['results']])}"
            f" | {c['retake']} | {c['wrong_confident']} | {c['errors']} |"
        )
    for name, outcome in sets.items():
        lines += ["", f"## {name}: per field (exact match)", ""]
        lines += [f"- `{f}`: {pct(h)}" for f, h in outcome["fields"].items()]
        wrong = [r for r in outcome["results"] if r["wrong_confident"]]
        if wrong:
            lines += ["", "Wrong but confident (the parent's read-back is the last check):"]
            for r in wrong:
                detail = "; ".join(f"{f} {w!r} -> {h!r}" for f, w, h in r["misses"])
                lines.append(f"- `{r['file']}`: {detail}")
    return "\n".join(lines) + "\n"


def _filter(labels: list[Label], kind: str | None, match: str | None) -> list[Label]:
    if kind:
        labels = [lb for lb in labels if lb.kind == kind]
    if match:
        labels = [lb for lb in labels if fnmatch(lb.file.lower(), match.lower())]
    return labels


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--reader", choices=["mock", "groq"], default="mock")
    ap.add_argument("--samples", type=Path, default=SAMPLES_DIR, help="synthetic set folder")
    ap.add_argument("--real", type=Path, default=REAL_DIR, help="real photos + labels.csv")
    ap.add_argument("--real-only", action="store_true", help="skip the synthetic set")
    ap.add_argument("--synthetic-only", action="store_true", help="skip the real set")
    ap.add_argument("--yes", action="store_true", help="don't ask before spending real API quota")
    ap.add_argument("--kind", choices=["meter", "strip"], help="only test this kind of photo")
    ap.add_argument("--match", help="only files matching this glob, e.g. 'glucose_*'")
    ap.add_argument("--pace", type=float, default=0.0, help="seconds to wait between photos")
    ap.add_argument("--results", type=Path, help="where to write RESULTS.md")
    ap.add_argument("--no-results", action="store_true", help="don't write RESULTS.md")
    ap.add_argument("--import", dest="import_dir", type=Path, help="add photos (EXIF removed)")
    args = ap.parse_args(argv)

    if args.import_dir:
        added = import_photos(args.import_dir, args.real)
        print(f"Imported {len(added)} photo(s) into {args.real} with metadata removed.")
        if added:
            print(f"Now fill in their rows in {args.real / CSV_NAME} (see README.md there).")
        return 0

    sets: dict[str, tuple[list[Label], Path]] = {}
    if not args.real_only:
        synthetic = _filter(load_labels(args.samples), args.kind, args.match)
        if synthetic:
            sets["Synthetic"] = (synthetic, args.samples)
    if not args.synthetic_only and args.kind != "strip":
        real = _filter(load_real_labels(args.real), args.kind, args.match)
        if real:
            sets["Real"] = (real, args.real)

    skipped: dict[str, list[str]] = {}
    if args.reader != "mock":  # a real reader needs the actual photo
        for name, (labels, folder) in list(sets.items()):
            skipped[name] = [lb.file for lb in labels if not (folder / lb.file).is_file()]
            kept = [lb for lb in labels if lb.file not in skipped[name]]
            sets[name] = (kept, folder)
    sets = {k: v for k, v in sets.items() if v[0]}
    if not sets:
        print(
            "No samples to test. Synthetic: samples/README.md. Real photos: "
            "samples/real_photos/README.md."
        )
        return 1
    photos = sum(len(labels) for labels, _ in sets.values())
    if args.reader != "mock" and not confirm_real_run(args.reader, photos, args.yes):
        return 2

    print(f"Reader: {args.reader}")
    outcomes: dict[str, dict[str, Any]] = {}
    readers: list[Reader] = []
    for name, (labels, folder) in sets.items():
        readers.append(make_reader(args.reader, folder))
        outcomes[name] = evaluate(readers[-1], labels, folder, args.pace)
        report(name, outcomes[name], skipped.get(name, []))
    print("\n" + token_line(readers, photos))

    if "Real" in outcomes and not args.no_results:
        target = args.results or (args.real / "RESULTS.md")
        target.write_text(results_markdown(args.reader, outcomes), encoding="utf-8")
        print(f"Wrote {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
