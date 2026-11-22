"""Render SYNTHETIC meter screens and a strip label into samples/ (drawn, not photographed).

They let the mock reader, the simulator and a live smoke test work before real photos exist.
They are far easier to read than real photos, so they say nothing about real accuracy.
    cd backend && uv run python ../scripts/make_synthetic_samples.py
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

SAMPLES = Path(__file__).resolve().parents[1] / "samples"
LCD_BG = (176, 190, 160)
INK = (25, 30, 25)


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return ImageFont.load_default(size=size)


def device(title: str) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (900, 1100), (232, 232, 228))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(
        (60, 40, 840, 1060), radius=60, fill=(245, 245, 242), outline=(90, 90, 90), width=6
    )
    d.text((450, 110), title, font=font(44), fill=(60, 60, 60), anchor="mm")
    d.rounded_rectangle((130, 170, 770, 900), radius=20, fill=LCD_BG, outline=(60, 60, 60), width=4)
    d.text((170, 200), "SYNTHETIC SAMPLE", font=font(26), fill=(90, 100, 80))
    return img, d


def bp_screen(sys_: int, dia: int, pulse: int) -> Image.Image:
    img, d = device("AUTO BP MONITOR")
    for y, label, unit, value, size in (
        (300, "SYS", "mmHg", sys_, 210),
        (540, "DIA", "mmHg", dia, 190),
        (760, "PUL", "/min", pulse, 120),
    ):
        d.text((170, y), label, font=font(40), fill=INK)
        d.text((170, y + 50), unit, font=font(26), fill=INK)
        d.text((740, y + 60), str(value), font=font(size), fill=INK, anchor="rm")
    d.text((450, 980), "12/11  08:15   M-07", font=font(34), fill=(70, 70, 70), anchor="mm")
    return img


def glucose_screen(value: str, unit: str) -> Image.Image:
    img, d = device("GLUCOSE METER")
    d.text((450, 500), value, font=font(260), fill=INK, anchor="mm")
    d.text((720, 720), unit, font=font(56), fill=INK, anchor="rm")
    d.text((180, 820), "08:02   22-11", font=font(34), fill=INK)
    return img


def strip(brand: str, strength: str, generic: str) -> Image.Image:
    img = Image.new("RGB", (1200, 700), (205, 210, 215))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(
        (40, 60, 1160, 640), radius=30, fill=(235, 238, 242), outline=(120, 120, 130), width=4
    )
    for x in range(160, 1100, 230):
        d.ellipse((x - 70, 380, x + 70, 600), outline=(160, 165, 175), width=5)
    d.text((90, 90), "SYNTHETIC SAMPLE", font=font(26), fill=(120, 120, 120))
    d.text((90, 140), f"{brand} {strength}", font=font(110), fill=(20, 60, 140))
    d.text((90, 280), f"{generic} Tablets IP {strength} mg", font=font(40), fill=(40, 40, 40))
    return img


def main() -> None:
    SAMPLES.mkdir(exist_ok=True)
    blurry = bp_screen(138, 86, 72).filter(ImageFilter.GaussianBlur(18))
    images = {
        "synthetic_bp_normal.jpg": bp_screen(132, 84, 74),
        "synthetic_bp_high.jpg": bp_screen(186, 122, 92),
        "synthetic_glucose_mgdl.jpg": glucose_screen("112", "mg/dL"),
        "synthetic_glucose_mmol.jpg": glucose_screen("6.2", "mmol/L"),
        "synthetic_strip_telma40.jpg": strip("Telma", "40", "Telmisartan"),
        "synthetic_bp_blurry.jpg": blurry,
    }
    for name, img in images.items():
        img.save(SAMPLES / name, format="JPEG", quality=80)
        print("wrote", SAMPLES / name)


if __name__ == "__main__":
    main()
