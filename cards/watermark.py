"""Shared brand watermark applied to every generated card image."""

import logging
from functools import lru_cache
from pathlib import Path

from PIL import Image

logger = logging.getLogger(__name__)

LOGO_PATH = Path(__file__).resolve().parent.parent / "assets" / "logo.png"

# Tunables
WATERMARK_WIDTH_RATIO = 0.09   # logo width as a fraction of the card width
WATERMARK_MARGIN_RATIO = 0.018  # margin from the edge as a fraction of card width
WATERMARK_MIN_SIZE = 48
WATERMARK_MAX_SIZE = 160
WATERMARK_OPACITY = 0.55       # 0-1, keeps it subtle so it doesn't fight the artwork


@lru_cache(maxsize=1)
def _load_logo():
    try:
        return Image.open(LOGO_PATH).convert("RGBA")
    except Exception:
        logger.warning("Watermark logo not found at %s - skipping watermark", LOGO_PATH)
        return None


def apply_watermark(card_image, position="bottom-right", opacity=WATERMARK_OPACITY):
    """Composite the brand logo onto a finished card image.

    `card_image` must be an RGBA Pillow Image. Returns the same image with the
    watermark stamped on (mutates and returns `card_image`; safe to ignore the
    return value if you only care about the mutation).
    """
    logo = _load_logo()
    if logo is None:
        return card_image

    card_w, card_h = card_image.size

    target_w = int(card_w * WATERMARK_WIDTH_RATIO)
    target_w = max(WATERMARK_MIN_SIZE, min(WATERMARK_MAX_SIZE, target_w))
    scale = target_w / logo.width
    target_h = max(1, int(logo.height * scale))
    resized_logo = logo.resize((target_w, target_h), Image.Resampling.LANCZOS)

    if opacity < 1:
        alpha = resized_logo.getchannel("A").point(lambda a: int(a * opacity))
        resized_logo.putalpha(alpha)

    margin = max(8, int(card_w * WATERMARK_MARGIN_RATIO))

    positions = {
        "bottom-right": (card_w - target_w - margin, card_h - target_h - margin),
        "bottom-left": (margin, card_h - target_h - margin),
        "top-right": (card_w - target_w - margin, margin),
        "top-left": (margin, margin),
    }
    x, y = positions.get(position, positions["bottom-right"])

    card_image.paste(resized_logo, (x, y), resized_logo)
    return card_image
