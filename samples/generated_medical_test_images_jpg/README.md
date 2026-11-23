# AI-generated synthetic test images

24 images of home BP monitors (13), glucometers (9) and medicine strips (2), made by the repo
owner with an image-generation model. **They are not photographs of real devices or real
readings.** They look like phone photos (clutter, glare, blur, odd angles), so they test the
reader harder than the plain `synthetic_*.jpg` drawings one folder up, but they are still not
evidence of accuracy on real photos.

- Licence: the owner's own work, published with this repo (MIT).
- Ground truth: `labels.json` here, written by a person looking at each image (same format as
  `samples/README.md`). Uncertain spots are in each entry's `note` (e.g. `bp_generated_02.jpg`
  has soft pulse digits).
- Brand names visible in the images are part of the generated scene, not endorsements or
  real product photos.

Run:

```bash
cd backend
uv run python ../scripts/accuracy_test.py --samples ../samples/generated_medical_test_images_jpg --kind meter          # mock: checks the labels
uv run python ../scripts/accuracy_test.py --reader groq --samples ../samples/generated_medical_test_images_jpg --kind meter --pace 21   # real, ~53k tokens
```

## Result (23 Nov 2026, Groq `qwen/qwen3.8-27b`)

16 of 22 meter images scored before Groq's free daily token limit stopped the run: BP 11/13,
glucose 3/3, 0 rejected as unreadable. Misses: `bp_generated_02` pulse 108 read as 100;
`bp_generated_07` read the glucometer in the same frame (240 mg/dL) instead of the BP monitor.
Not yet scored: `glucose_generated_04` to `09` (about 13k tokens):
`--reader groq --samples ../samples/generated_medical_test_images_jpg --match 'glucose_generated_0[4-9]*' --pace 21`.

## Why not public real photos (checked 23 Nov 2026)

- **Oxford seven-segment dataset** (Finnegan, Villarroel, Velardo, Tarassenko, "Automated
  method for detecting and reading seven-segment digits from images of blood glucose metres and
  blood pressure monitors", J. Med. Eng. & Tech. 43(6):341-355, 2019,
  https://eng.ox.ac.uk/lcmt/resources/resources-seven-segment): the image download
  (`cameralab.eng.ox.ac.uk/data/bp_bg_meters.zip`) now redirects to the lab home page, no
  licence is stated, and the code repo holds no images or labels.
- **Roboflow Universe** (e.g. "Blood-Pressure-monitor-digit-reader", CC BY 4.0; glucometer
  digit sets): downloads need a Roboflow account API key, and labels are per-digit boxes, so
  readings would have to be reassembled from boxes and checked by hand. Possible later with a
  free Roboflow key.

Testing on photos of Indian home devices is planned.
