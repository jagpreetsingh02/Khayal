# Real photos (accuracy harness)

Photos of real home BP monitors and glucometers, taken the way a parent would take them, with
their true values in `labels.csv`. `scripts/accuracy_test.py` scores the reader on this set
separately from the synthetic set and writes `RESULTS.md` here.

## What a photo needs (and doesn't)

- **The meter screen**, filling most of the frame. A little glare, a slight angle or blur is
  fine and useful: that is real life.
- **No faces, no people, no names**: crop or retake so only the device is visible. Nothing
  else on the table (prescriptions, letters, phone screens).
- **No brand logo needed**: the reader only needs the numbers. A logo in the frame is fine,
  but don't go looking for it.
- Any phone, any format (`.jpg`, `.jpeg`, `.png`, `.webp`, `.heic` if Pillow can open it).

## Adding photos

1. Put the photos in any folder, e.g. `~/Downloads/meter_photos/`. Name them so the type is
   obvious if you can (`bp_...`, `glucose_...`); otherwise you set the type in step 3.
2. Import them (from `backend/`):

   ```bash
   uv run python ../scripts/accuracy_test.py --import ~/Downloads/meter_photos
   ```

   Each photo is copied here as a JPEG with **all metadata removed** (EXIF: GPS location,
   phone model, date and time), rotated upright first, and gets an empty row in `labels.csv`.
   Never copy photos into this folder by hand: that keeps their EXIF.
3. Fill in each row of `labels.csv` with what the screen **actually shows**:

   | column | BP monitor | glucometer |
   |---|---|---|
   | `file` | file name in this folder | file name in this folder |
   | `type` | `bp` | `glucose` |
   | `sys`, `dia`, `pulse` | the numbers (leave `pulse` empty if not shown) | empty |
   | `glucose` | empty | the number, or `HI` / `LO` if the screen shows that |
   | `unit` | empty | `mg/dL` or `mmol/L`, as on the screen (don't convert) |

   ```csv
   file,type,sys,dia,pulse,glucose,unit
   bp_omron_morning.jpg,bp,142,88,76,,
   glucose_accuchek_01.jpg,glucose,,,,118,mg/dL
   glucose_old_meter.jpg,glucose,,,,6.4,mmol/L
   ```

   Rows with an empty `type` (imported, not labelled yet) are skipped.
4. Check the labels with the mock reader (no API calls; it cannot read real photos, so it
   reports every photo as "retake"):

   ```bash
   uv run python ../scripts/accuracy_test.py --real-only --no-results
   ```

5. Run the real reader (manual, spends Groq quota, ~3 photos a minute on the free tier):

   ```bash
   uv run python ../scripts/accuracy_test.py --reader groq --yes --pace 21
   ```

## What the numbers mean

For the synthetic and the real set separately:

- **per field**: exact match for each value (`systolic`, `glucose_value`, ...);
- **all fields right**: the share of photos where every value matched;
- **retake / refused**: the reader said it could not read the photo, or the app's plausibility
  rules refused the numbers. The parent is asked for a new photo: the safe outcome;
- **wrong but confident**: a legible, plausible but wrong value. It would be read back to the
  parent ("I read 142/88 — correct?"), whose "2" is then the last check. This is the number
  to keep near zero.

The repo is public: commit photos only if you are happy for anyone to see them.
