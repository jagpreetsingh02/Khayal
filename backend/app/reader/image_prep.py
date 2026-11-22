"""Shrink photos before sending them to a vision model: fewer bytes, faster, cheaper.

Phone photos are often 4000 px and several MB; a meter screen is readable at ~1280 px.
"""

import io

from PIL import Image, ImageOps, UnidentifiedImageError

from app.reader.base import ReaderError

MAX_SIDE = 1280
JPEG_QUALITY = 85


def prepare_image(data: bytes, max_side: int = MAX_SIDE, quality: int = JPEG_QUALITY) -> bytes:
    """Return JPEG bytes with the longest side at most `max_side` px, upright (EXIF)."""
    try:
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img)
    except (UnidentifiedImageError, OSError) as exc:
        raise ReaderError("The file is not a readable image.") from exc
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)  # keeps aspect, never upscales
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=quality, optimize=True)
    return out.getvalue()
