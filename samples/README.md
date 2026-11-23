# Samples

Meter photos (BP monitors, glucometers), medicine-strip photos, and `labels.json` with the
true values. They are used by the mock reader (`LLM_PROVIDER=mock`) and by
`scripts/accuracy_test.py`.

The six `synthetic_*.jpg` files are **synthetic**: drawn by `scripts/make_synthetic_samples.py`,
not photographed. They let the mock mode and a quick live test work. They are much
easier to read than real photos, so they say nothing about real accuracy. Real photos are
what count.

`generated_medical_test_images_jpg/` holds 24 **AI-generated** phone-style images with their
own `labels.json` (see its README). Harder than the drawings, but still not real photos.

## Adding your own photos

1. **Take the photo** the way a parent would: phone in hand, screen filling most of the frame.
   A few with glare, a slight angle or blur are useful too, because that is real life.
2. **Name the file**: lowercase, no spaces, and start with what it shows:
   - `bp_<anything>.jpg`: BP monitor, e.g. `bp_omron_01.jpg`, `bp_dad_morning_02.jpg`
   - `glucose_<anything>.jpg`: glucometer, e.g. `glucose_accuchek_01.jpg`
   - `strip_<anything>.jpg`: medicine strip or box, e.g. `strip_telma40_01.jpg`

   `.jpg`, `.jpeg`, `.png` and `.webp` all work. Names must be unique (matching ignores case).
   Don't start real photos with `synthetic_`.
3. **Remove anything private** before committing: names, faces, prescriptions with personal
   details. The repo is public.
4. **Add an entry to `labels.json`** under `"samples"` with what the screen *actually shows*:

```json
{
  "file": "bp_omron_01.jpg",
  "kind": "meter",
  "expected": {"device_type": "bp", "systolic": 142, "diastolic": 88, "pulse": 76}
}
```

```json
{
  "file": "glucose_accuchek_01.jpg",
  "kind": "meter",
  "expected": {"device_type": "glucose", "glucose_value": 118, "glucose_unit": "mg/dL"}
}
```

```json
{
  "file": "strip_telma40_01.jpg",
  "kind": "strip",
  "expected": {"brand": "Telma", "strength": "40"}
}
```

### Rules for labels

- `kind`: `"meter"` for BP monitors and glucometers, `"strip"` for medicine strips and boxes.
- Meter fields: `device_type` (`"bp"`, `"glucose"` or `"unknown"`), `systolic`, `diastolic`,
  `pulse`, `glucose_value`, `glucose_unit` (`"mg/dL"` or `"mmol/L"`, exactly as on the screen).
  Write numbers as shown on the screen. Do **not** convert mmol/L to mg/dL; the app does that.
- A meter field you leave out means "not shown on the screen" (null). The reader is then
  expected to return null for it too. For example, if the BP monitor shows no pulse, leave
  `pulse` out.
- If the photo is too bad to read, add `"legible": false` and leave the numbers out. A good
  reader must then say it can't read the photo, not guess. `legible` is checked only when you
  set it.
- Strip fields: `brand` as printed, without the strength (`"Telma"`, `"Glycomet GP"`), and
  `strength` as printed (`"40"`, `"500"`, `"2/500"`). They are compared ignoring case, spaces
  and a trailing "mg".
- Optional: `"note"` for anything worth remembering, and `"issues"` (`glare`, `angle`,
  `partial`, `blur`). Issues are informational and not scored.

## Measuring accuracy

```bash
cd backend
uv run python ../scripts/accuracy_test.py                 # mock reader (checks the labels)
uv run python ../scripts/accuracy_test.py --reader groq   # real vision model, needs GROQ_API_KEY
uv run python ../scripts/accuracy_test.py --reader groq --real-only   # skip synthetic_*
```

It prints exact-match accuracy per field and overall (a photo counts only when every checked
field matches), and lists every failure.
