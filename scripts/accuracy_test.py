"""Measure a reader against samples/labels.json: exact-match accuracy per field and overall.

    cd backend
    uv run python ../scripts/accuracy_test.py                       # mock reader
    uv run python ../scripts/accuracy_test.py --reader groq         # needs GROQ_API_KEY
    uv run python ../scripts/accuracy_test.py --reader groq --real-only

"Overall" counts a photo as correct only when every checked field matches. A reader error
counts as a miss on every field. Label format: samples/README.md.
"""

import argparse
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.config import get_settings  # noqa: E402
from app.reader import Reader, ReaderError  # noqa: E402
from app.reader.labels import SAMPLES_DIR, Label, load_labels  # noqa: E402
from app.reader.schema import ImageInput  # noqa: E402


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


def _norm_text(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.lower().replace(" ", "")
    return text[:-2] if text.endswith("mg") else text


def same(field: str, expected: Any, got: Any) -> bool:
    if field in ("brand", "strength"):
        return _norm_text(expected) == _norm_text(got)
    if isinstance(expected, int | float) and isinstance(got, int | float):
        return abs(float(expected) - float(got)) < 1e-6
    return expected == got


def evaluate(reader: Reader, labels: list[Label], samples_dir: Path) -> dict[str, Any]:
    field_hits: dict[str, list[bool]] = defaultdict(list)
    results = []
    for label in labels:
        path = samples_dir / label.file
        data = path.read_bytes() if path.is_file() else b""
        image = ImageInput(data=data, filename=label.file)
        expected = label.meter() if label.kind == "meter" else label.strip()
        started = time.perf_counter()
        try:
            got = reader.read_meter(image) if label.kind == "meter" else reader.read_strip(image)
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
        results.append({"file": label.file, "misses": misses, "error": error, "seconds": seconds})
    return {"fields": dict(field_hits), "results": results}


def pct(hits: list[bool]) -> str:
    return f"{100 * sum(hits) / len(hits):5.1f}% ({sum(hits)}/{len(hits)})" if hits else "n/a"


def report(reader_name: str, outcome: dict[str, Any], skipped: list[str]) -> None:
    results = outcome["results"]
    print(f"Reader: {reader_name} · photos: {len(results)}")
    if skipped:
        print(f"Skipped (file missing): {', '.join(skipped)}")
    print("\nPer field (exact match):")
    for field, hits in outcome["fields"].items():
        print(f"  {field:<14} {pct(hits)}")
    overall = [not r["misses"] for r in results]
    print(f"\nOverall (all checked fields right): {pct(overall)}")
    if results:
        avg = sum(r["seconds"] for r in results) / len(results)
        print(f"Average time per photo: {avg:.2f} s")
    failures = [r for r in results if r["misses"]]
    if failures:
        print("\nFailures:")
        for r in failures:
            if r["error"]:
                print(f"  {r['file']}: reader error: {r['error']}")
                continue
            detail = "; ".join(f"{f}: expected {w!r}, got {h!r}" for f, w, h in r["misses"])
            print(f"  {r['file']}: {detail}")
    else:
        print("\nNo failures.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--reader", choices=["mock", "groq"], default="mock")
    ap.add_argument("--samples", type=Path, default=SAMPLES_DIR)
    ap.add_argument("--real-only", action="store_true", help="skip synthetic_* samples")
    args = ap.parse_args(argv)

    labels = load_labels(args.samples)
    if args.real_only:
        labels = [lb for lb in labels if not lb.synthetic]
    skipped: list[str] = []
    if args.reader != "mock":  # a real reader needs the actual photo
        skipped = [lb.file for lb in labels if not (args.samples / lb.file).is_file()]
        labels = [lb for lb in labels if lb.file not in skipped]
    if not labels:
        print("No samples to test. Add photos and labels: see samples/README.md.")
        return 1
    reader = make_reader(args.reader, args.samples)
    report(args.reader, evaluate(reader, labels, args.samples), skipped)
    return 0


if __name__ == "__main__":
    sys.exit(main())
