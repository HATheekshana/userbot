"""
futuristic_theme.py

Shared visual language for the futuristic dark-HUD character card design
(glass panels, neon text, segmented bars, hex frames, scanlines, ...).

Pure PIL - no extra dependencies beyond what character_card.py already
uses. Every drawing helper here works on RGBA layers so callers can keep
compositing them freely (Image.alpha_composite).
"""
import math
from PIL import Image, ImageDraw, ImageFilter, ImageEnhance, ImageChops

# --------------------------------------------------------------------------
# Palette
# --------------------------------------------------------------------------
ACCENT = (0, 224, 255)              # default neon cyan, used when element is unknown/missing
TEXT_MAIN = (235, 242, 248, 255)
TEXT_DIM = (150, 168, 185, 255)
GOLD = (255, 200, 80, 255)

_ELEMENT_COLORS = {
    "pyro": (255, 110, 70),
    "hydro": (70, 170, 255),
    "electro": (190, 110, 255),
    "cryo": (140, 225, 255),
    "anemo": (110, 230, 200),
    "geo": (255, 190, 70),
    "dendro": (150, 220, 70),
    "physical": (210, 210, 220),
}


def element_color(element):
    """Element name -> RGB accent tuple used throughout the theme."""
    if not element:
        return ACCENT
    return _ELEMENT_COLORS.get(str(element).lower(), ACCENT)


def _rgb(color, default_alpha=255):
    """Normalize a 3- or 4-tuple color to RGBA."""
    if color is None:
        return (0, 0, 0, 0)
    if len(color) == 4:
        return tuple(color)
    return (color[0], color[1], color[2], default_alpha)


# --------------------------------------------------------------------------
# Backgrounds / full-canvas effects
# --------------------------------------------------------------------------

def base_atmosphere(size):
    """Dark vertical-gradient HUD backdrop with a faint grid and vignette,
    used as the base layer before the namecard art is folded in."""
    width, height = size
    canvas = Image.new("RGBA", size, (0, 0, 0, 255))
    top = (10, 14, 22)
    bottom = (4, 6, 10)
    for y in range(height):
        ratio = y / max(1, height - 1)
        row_color = tuple(int(top[i] + (bottom[i] - top[i]) * ratio) for i in range(3))
        ImageDraw.Draw(canvas).line([(0, y), (width, y)], fill=row_color + (255,))

    grid = Image.new("RGBA", size, (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(grid)
    step = 64
    for x in range(0, width, step):
        gdraw.line([(x, 0), (x, height)], fill=(255, 255, 255, 6), width=1)
    for y in range(0, height, step):
        gdraw.line([(0, y), (width, y)], fill=(255, 255, 255, 6), width=1)
    canvas = Image.alpha_composite(canvas, grid)

    # soft vignette
    vignette = Image.new("L", size, 0)
    vdraw = ImageDraw.Draw(vignette)
    vdraw.ellipse([-width * 0.25, -height * 0.3, width * 1.25, height * 1.3], fill=255)
    vignette = vignette.filter(ImageFilter.GaussianBlur(120))
    dark_overlay = Image.new("RGBA", size, (0, 0, 0, 160))
    inverted_alpha = Image.eval(vignette, lambda p: 255 - p)
    dark_overlay.putalpha(inverted_alpha)
    canvas = Image.alpha_composite(canvas, dark_overlay)

    return canvas


def darken_and_desaturate(image, brightness=0.42, saturation=0.5):
    """Dim + desaturate a background image so foreground HUD text/panels
    stay readable on top of busy namecard art."""
    rgba = image.convert("RGBA")
    rgb = rgba.convert("RGB")
    rgb = ImageEnhance.Color(rgb).enhance(saturation)
    rgb = ImageEnhance.Brightness(rgb).enhance(brightness)
    rgb = rgb.convert("RGBA")
    rgb.putalpha(rgba.getchannel("A"))
    return rgb


def scanlines(size, spacing=4, alpha=10):
    """Subtle horizontal scanline overlay for the CRT/HUD feel."""
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for y in range(0, size[1], max(1, spacing)):
        draw.line([(0, y), (size[0], y)], fill=(0, 0, 0, alpha), width=1)
    return overlay


# --------------------------------------------------------------------------
# Panels / frames
# --------------------------------------------------------------------------

def draw_glass_panel(ui_layer, box, radius=12, glow=None, fill=(12, 18, 28, 130), outline=(255, 255, 255, 25)):
    """Translucent 'glassmorphism' rounded panel, optionally with a soft
    colored glow bled out behind its edges. Composites directly onto
    ui_layer (an RGBA Image), matching how the rest of the theme is used."""
    x0, y0, x1, y1 = box

    if glow is not None:
        glow_layer = Image.new("RGBA", ui_layer.size, (0, 0, 0, 0))
        gdraw = ImageDraw.Draw(glow_layer)
        pad = 10
        gdraw.rounded_rectangle([x0 - pad, y0 - pad, x1 + pad, y1 + pad], radius=radius + pad, fill=_rgb(glow))
        glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(18))
        ui_layer.alpha_composite(glow_layer)

    panel = Image.new("RGBA", ui_layer.size, (0, 0, 0, 0))
    pdraw = ImageDraw.Draw(panel)
    pdraw.rounded_rectangle(box, radius=radius, fill=_rgb(fill), outline=_rgb(outline), width=1)
    # faint top highlight line for the "glass" look
    pdraw.line([(x0 + radius, y0 + 1), (x1 - radius, y0 + 1)], fill=(255, 255, 255, 35), width=1)
    ui_layer.alpha_composite(panel)
    return ui_layer


def draw_corner_brackets(draw, box, color, length=18, width=2):
    """Small L-shaped HUD brackets at the four corners of a box."""
    x0, y0, x1, y1 = box
    fill = _rgb(color)
    corners = [
        ((x0, y0), (1, 1)),
        ((x1, y0), (-1, 1)),
        ((x0, y1), (1, -1)),
        ((x1, y1), (-1, -1)),
    ]
    for (cx, cy), (dx, dy) in corners:
        draw.line([(cx, cy), (cx + dx * length, cy)], fill=fill, width=width)
        draw.line([(cx, cy), (cx, cy + dy * length)], fill=fill, width=width)


def draw_diagonal_tag(ui_layer, position, w, h, color, text, font):
    """Small slanted ribbon tag (e.g. weapon refinement 'R1') anchored at
    its top-left corner."""
    x, y = position
    skew = h * 0.4
    points = [
        (x, y + h), (x + skew, y), (x + w, y), (x + w - skew, y + h),
    ]
    layer = Image.new("RGBA", ui_layer.size, (0, 0, 0, 0))
    ldraw = ImageDraw.Draw(layer)
    ldraw.polygon(points, fill=_rgb(color))
    ui_layer.alpha_composite(layer)
    draw = ImageDraw.Draw(ui_layer)
    draw.text((x + w / 2, y + h / 2), text, font=font, fill=(10, 10, 14, 255), anchor="mm")


def neon_text(ui_layer, text, position, font, fill=TEXT_MAIN, glow=None, anchor="lm"):
    """Crisp text with an optional soft colored glow behind it."""
    if glow is not None:
        glow_layer = Image.new("RGBA", ui_layer.size, (0, 0, 0, 0))
        gdraw = ImageDraw.Draw(glow_layer)
        gdraw.text(position, text, font=font, fill=_rgb(glow, 200), anchor=anchor)
        glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(4))
        ui_layer.alpha_composite(glow_layer)

    draw = ImageDraw.Draw(ui_layer)
    draw.text(position, text, font=font, fill=_rgb(fill), anchor=anchor)
    return ui_layer


def segmented_bar(ui_layer, box, ratio, color=ACCENT, segments=18, track=(255, 255, 255, 25)):
    """HUD-style segmented progress bar filling `box` left-to-right."""
    x0, y0, x1, y1 = box
    ratio = max(0.0, min(1.0, ratio))
    draw = ImageDraw.Draw(ui_layer)
    gap = 3
    total_width = x1 - x0
    seg_width = (total_width - gap * (segments - 1)) / segments
    filled_segments = round(ratio * segments)
    for index in range(segments):
        seg_x0 = x0 + index * (seg_width + gap)
        seg_x1 = seg_x0 + seg_width
        fill = _rgb(color) if index < filled_segments else _rgb(track)
        draw.rectangle([seg_x0, y0, seg_x1, y1], fill=fill)


def stat_row_icon_bg(ui_layer, box, color=ACCENT):
    """Small rounded chip background used behind compact stat rows/icons
    (e.g. weapon substats)."""
    draw = ImageDraw.Draw(ui_layer)
    accent = _rgb(color)
    draw.rounded_rectangle(box, radius=8, fill=(255, 255, 255, 18))
    draw.rounded_rectangle(box, radius=8, outline=(accent[0], accent[1], accent[2], 90), width=1)


# --------------------------------------------------------------------------
# Hexagon helpers (weapon icon frame, talent/constellation frames)
# --------------------------------------------------------------------------

def hex_points(cx, cy, r):
    """Six vertices of a flat-top hexagon centered at (cx, cy) with
    circumradius r."""
    return [
        (cx + r * math.cos(math.radians(angle)), cy + r * math.sin(math.radians(angle)))
        for angle in range(0, 360, 60)
    ]


def draw_hex_frame(ui_layer, cx, cy, r, fill=None, outline=ACCENT, width=3):
    """Filled + outlined hexagon frame, with a faint outer glow."""
    points = hex_points(cx, cy, r)

    if outline is not None:
        glow_layer = Image.new("RGBA", ui_layer.size, (0, 0, 0, 0))
        gdraw = ImageDraw.Draw(glow_layer)
        gdraw.polygon(hex_points(cx, cy, r + 4), outline=_rgb(outline), width=width)
        glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(5))
        ui_layer.alpha_composite(glow_layer)

    draw = ImageDraw.Draw(ui_layer)
    if fill is not None:
        draw.polygon(points, fill=_rgb(fill))
    if outline is not None:
        draw.polygon(points, outline=_rgb(outline), width=width)


def paste_hex_masked(ui_layer, image, cx, cy, r):
    """Resize/crop `image` to cover a hexagon of circumradius r centered at
    (cx, cy), then paste it into ui_layer through a hexagon mask."""
    diameter = int(r * 2)
    if diameter <= 0:
        return
    fitted = image.convert("RGBA")
    scale = diameter / min(fitted.width, fitted.height)
    fitted = fitted.resize((max(1, int(fitted.width * scale)), max(1, int(fitted.height * scale))), Image.Resampling.LANCZOS)
    left = (fitted.width - diameter) // 2
    top = (fitted.height - diameter) // 2
    fitted = fitted.crop((left, top, left + diameter, top + diameter))

    mask = Image.new("L", (diameter, diameter), 0)
    mdraw = ImageDraw.Draw(mask)
    local_points = [(px - (cx - r), py - (cy - r)) for px, py in hex_points(cx, cy, r)]
    mdraw.polygon(local_points, fill=255)

    combined_alpha = ImageChops.multiply(fitted.getchannel("A"), mask)
    fitted.putalpha(combined_alpha)
    ui_layer.paste(fitted, (int(cx - r), int(cy - r)), fitted)