import logging
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

from cards.watermark import apply_watermark
from services.net import new_session

logger = logging.getLogger("genshin_userbot")

FONT_PATH = "assets/fonts/Genshin_Impact.ttf"

ELEMENT_COLORS = {
    "Pyro": (255, 130, 90),
    "Hydro": (70, 175, 255),
    "Anemo": (130, 220, 195),
    "Electro": (195, 135, 235),
    "Dendro": (170, 210, 75),
    "Cryo": (150, 225, 240),
    "Geo": (240, 180, 60),
    "Physical": (225, 225, 225),
    "None": (225, 225, 225),
}

# Void Bastion design: a deep violet-magenta palette that mirrors the in-game
# Stygian Onslaught summary screen (dark plum backdrop, gold accents, square
# portraits) rather than the abstract ember/hex look the module used to have.
# Kept distinct from the Astral Observatory (Abyss, blue-violet + diamonds)
# so each report still reads as its own thing at a glance.
BG_TOP = (34, 16, 46, 255)
BG_BOTTOM = (10, 6, 16, 255)
PANEL_BG = (37, 21, 48, 225)
PANEL_BG_ALT = (46, 26, 58, 235)
PANEL_BORDER = (128, 92, 158, 200)
MUTED = (200, 180, 214, 255)
GOLD = (255, 208, 120, 255)
WHITE = (247, 240, 250, 255)
ACCENT_A = (186, 120, 255, 255)   # bright violet
ACCENT_B = (232, 96, 160, 255)    # magenta pink
SILVER = (206, 210, 224, 255)

CARD_WIDTH = 1400
MARGIN = 40
GAP = 24
MAX_STAGES_SHOWN = 6


def _element_color(element):
    return ELEMENT_COLORS.get((element or "None").capitalize(), (225, 225, 225))


def _lerp_color(c1, c2, t):
    t = max(0.0, min(1.0, t))
    c1 = tuple(c1) + (255,) * (4 - len(c1))
    c2 = tuple(c2) + (255,) * (4 - len(c2))
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(4))


def _vertical_gradient(width, height, top_color, bottom_color):
    strip = Image.new("RGBA", (1, max(height, 1)))
    for y in range(strip.height):
        strip.putpixel((0, y), _lerp_color(top_color, bottom_color, y / max(1, strip.height - 1)))
    return strip.resize((width, strip.height))


def _horizontal_gradient(width, height, left_color, right_color, radius=0):
    strip = Image.new("RGBA", (max(width, 1), 1))
    for x in range(strip.width):
        strip.putpixel((x, 0), _lerp_color(left_color, right_color, x / max(1, strip.width - 1)))
    bar = strip.resize((strip.width, max(height, 1)))
    if radius:
        mask = Image.new("L", bar.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, bar.width - 1, bar.height - 1), radius=radius, fill=255)
        rounded = Image.new("RGBA", bar.size, (0, 0, 0, 0))
        rounded.paste(bar, (0, 0), mask)
        return rounded
    return bar


def _draw_motes(draw, width, height):
    """A deterministic scatter of faint violet motes gives every report the
    same drifting backdrop without needing an image asset."""
    for index in range(130):
        x = (index * 223 + 61) % width
        y = (index * 157 + 41) % height
        radius = 1 if index % 6 else 2
        alpha = 45 + (index * 29) % 100
        color = ACCENT_A if index % 3 else ACCENT_B
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color[:3] + (alpha,))


def draw_text_with_shadow(draw, text, position, font_path, font_size, text_color=WHITE, shadow_color=(0, 0, 0, 190), anchor="mm", shadow_offset=(2, 2)):
    font = ImageFont.truetype(font_path, font_size)
    shadow_pos = (position[0] + shadow_offset[0], position[1] + shadow_offset[1])
    draw.text(shadow_pos, text, font=font, fill=shadow_color, anchor=anchor)
    draw.text(position, text, font=font, fill=text_color, anchor=anchor)
    return font


def _wrap_text(text, font_path, font_size, max_width, max_lines=2):
    font = ImageFont.truetype(font_path, font_size)
    words = (text or "").split()
    lines = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if font.getlength(candidate) <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
        if len(lines) == max_lines - 1 and current:
            # last allowed line: let it keep growing, we'll ellipsize after the loop
            pass
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
    if lines and font.getlength(lines[-1]) > max_width:
        while lines[-1] and font.getlength(lines[-1] + "…") > max_width:
            lines[-1] = lines[-1][:-1]
        lines[-1] = lines[-1].rstrip() + "…"
    return lines or [""]


def _rounded_panel(draw, box, radius=20, fill=PANEL_BG, outline=PANEL_BORDER, width=2):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def _diamond_points(cx, cy, radius):
    return [(cx, cy - radius), (cx + radius, cy), (cx, cy + radius), (cx - radius, cy)]


async def _load_icon(session, url):
    if not url:
        return None
    try:
        async with session.get(url, timeout=15) as response:
            if response.status != 200:
                return None
            return Image.open(BytesIO(await response.read())).convert("RGBA")
    except Exception:
        return None


def _format_time(seconds):
    seconds = int(seconds or 0)
    if seconds >= 60:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    return f"{seconds}s"


class StygianCardBuilder:
    """Builds a Stygian Onslaught report image from the plain dict produced
    by stygian.normalize_stygian(). Keeping the input format plain (rather
    than a genshin.py model) means this file never needs to change just
    because the upstream library renamed a field.
    """

    def __init__(self, data, font_path=FONT_PATH):
        self.data = data
        self.font_path = font_path
        self._icons = {}

    # ---- asset loading -----------------------------------------------

    def _stages(self):
        return self.data.get("stages", [])[:MAX_STAGES_SHOWN]

    def _collect_icon_urls(self):
        urls = set()
        for stage in self._stages():
            if stage.get("boss_icon"):
                urls.add(stage["boss_icon"])
            for character in stage.get("team", []):
                if character.get("icon"):
                    urls.add(character["icon"])
        return urls

    async def _preload_icons(self, session):
        for url in self._collect_icon_urls():
            self._icons[url] = await _load_icon(session, url)

    def _square_chip(self, url, size, radius):
        image = self._icons.get(url)
        if image is None:
            return None
        image = image.resize((size, size), Image.Resampling.LANCZOS)
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
        chip = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        chip.paste(image, (0, 0), mask)
        return chip

    def _draw_square_chip(self, canvas, draw, top_left, url, size, border_color, radius=14):
        x, y = top_left
        chip = self._square_chip(url, size, radius)
        backdrop = Image.new("RGBA", (size, size), (26, 14, 34, 255))
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
        rounded_backdrop = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        rounded_backdrop.paste(backdrop, (0, 0), mask)
        canvas.paste(rounded_backdrop, (x, y), rounded_backdrop)
        if chip:
            canvas.paste(chip, (x, y), chip)
        draw.rounded_rectangle((x, y, x + size - 1, y + size - 1), radius=radius, outline=border_color, width=3)

    # ---- layout pieces --------------------------------------------------

    def _draw_header(self, canvas, draw, y):
        height = 168
        box = (MARGIN, y, CARD_WIDTH - MARGIN, y + height)
        _rounded_panel(draw, box, radius=26)

        strip = _horizontal_gradient(box[2] - box[0] - 4, 5, ACCENT_A, ACCENT_B)
        canvas.paste(strip, (box[0] + 2, box[1] + 2), strip)

        pad_x = 34
        draw_text_with_shadow(draw, f"UID: {self.data.get('uid', '?')}", (box[0] + pad_x, box[1] + 30), self.font_path, 18, text_color=MUTED, anchor="lm")

        # pill badge
        badge_text = "\u2726  Stygian Onslaught"
        badge_font = ImageFont.truetype(self.font_path, 18)
        badge_w = int(badge_font.getlength(badge_text)) + 44
        badge_box = (box[0] + pad_x, box[1] + 54, box[0] + pad_x + badge_w, box[1] + 54 + 38)
        badge_fill = _horizontal_gradient(badge_box[2] - badge_box[0], badge_box[3] - badge_box[1], ACCENT_A, ACCENT_B, radius=19)
        canvas.paste(badge_fill, (badge_box[0], badge_box[1]), badge_fill)
        draw_text_with_shadow(draw, badge_text, ((badge_box[0] + badge_box[2]) // 2, (badge_box[1] + badge_box[3]) // 2), self.font_path, 18, text_color=(255, 255, 255, 255), anchor="mm")

        period = f"{self.data.get('period_start', '')} \u2014 {self.data.get('period_end', '')}".strip(" \u2014")
        if period:
            draw_text_with_shadow(draw, f"Period: {period}", (box[0] + pad_x, box[1] + 118), self.font_path, 16, text_color=MUTED, anchor="lm")

        # mode row, centered, flanked by small diamonds
        mode_text = self.data.get("mode", "")
        mode_font = ImageFont.truetype(self.font_path, 17)
        mode_w = mode_font.getlength(mode_text)
        cx = box[0] + (box[2] - box[0]) * 0.66
        cy = box[1] + height - 26
        draw.polygon(_diamond_points(cx - mode_w / 2 - 24, cy, 6), fill=ACCENT_A)
        draw_text_with_shadow(draw, mode_text, (cx, cy), self.font_path, 17, text_color=SILVER, anchor="mm")
        draw.polygon(_diamond_points(cx + mode_w / 2 + 24, cy, 6), fill=ACCENT_B)

        draw_text_with_shadow(draw, "STYGIAN ONSLAUGHT REPORT", (box[2] - pad_x, box[1] + 30), self.font_path, 14, text_color=MUTED, anchor="rm")

        return y + height

    def _draw_best_record(self, canvas, draw, y):
        height = 78
        box = (MARGIN, y, CARD_WIDTH - MARGIN, y + height)
        fill = _horizontal_gradient(box[2] - box[0], height, (90, 46, 110, 235), (150, 60, 120, 235), radius=20)
        canvas.paste(fill, (box[0], box[1]), fill)
        draw.rounded_rectangle(box, radius=20, outline=PANEL_BORDER, width=2)

        draw_text_with_shadow(draw, "Best Record", (box[0] + 34, box[1] + height // 2), self.font_path, 22, anchor="lm")

        difficulty = self.data.get("difficulty_label") or "?"
        diamond_cx = box[2] - 150
        diamond_cy = box[1] + height // 2
        draw.polygon(_diamond_points(diamond_cx, diamond_cy, 22), fill=(210, 214, 226, 255), outline=(255, 255, 255, 255))
        draw_text_with_shadow(draw, difficulty, (diamond_cx, diamond_cy), self.font_path, 16, text_color=(30, 20, 40, 255), anchor="mm")

        draw_text_with_shadow(draw, _format_time(self.data.get("best_record_seconds", 0)), (box[2] - 34, box[1] + height // 2), self.font_path, 30, text_color=GOLD, anchor="rm")

        return y + height

    def _stage_title_lines(self, stage):
        return _wrap_text(stage.get("boss_name", "Unknown Boss"), self.font_path, 22, CARD_WIDTH - 2 * MARGIN - 60 - 260, max_lines=2)

    def _stage_panel_height(self, stage):
        title_lines = len(self._stage_title_lines(stage))
        title_block = 30 if title_lines == 1 else 58
        return 42 + title_block + 24 + 108 + 74

    def _draw_stage(self, canvas, draw, index, stage, y):
        height = self._stage_panel_height(stage)
        box = (MARGIN, y, CARD_WIDTH - MARGIN, y + height)
        _rounded_panel(draw, box, radius=20, fill=PANEL_BG_ALT if index % 2 else PANEL_BG)

        # index chip
        chip_r = 20
        chip_center = (box[0] + 34, box[1] + 34)
        chip_fill = _horizontal_gradient(chip_r * 2, chip_r * 2, ACCENT_A, ACCENT_B)
        chip_mask = Image.new("L", (chip_r * 2, chip_r * 2), 0)
        ImageDraw.Draw(chip_mask).ellipse((0, 0, chip_r * 2 - 1, chip_r * 2 - 1), fill=255)
        chip_img = Image.new("RGBA", (chip_r * 2, chip_r * 2), (0, 0, 0, 0))
        chip_img.paste(chip_fill, (0, 0), chip_mask)
        canvas.paste(chip_img, (chip_center[0] - chip_r, chip_center[1] - chip_r), chip_img)
        draw_text_with_shadow(draw, str(index + 1), chip_center, self.font_path, 19, text_color=(20, 8, 20, 255), anchor="mm")

        title_x = box[0] + 66
        title_lines = self._stage_title_lines(stage)
        title_y = box[1] + 26
        for line in title_lines:
            draw_text_with_shadow(draw, line, (title_x, title_y), self.font_path, 22, anchor="lm")
            title_y += 27

        time_row_y = title_y + 6
        draw_text_with_shadow(draw, "Time Elapsed", (title_x, time_row_y), self.font_path, 14, text_color=MUTED, anchor="lm")
        draw_text_with_shadow(draw, _format_time(stage.get("time_elapsed", 0)), (box[2] - 30, time_row_y), self.font_path, 18, text_color=GOLD, anchor="rm")

        divider_y = time_row_y + 18
        draw.line((box[0] + 24, divider_y, box[2] - 24, divider_y), fill=PANEL_BORDER, width=1)

        # team row
        icon_size = 68
        row_y = divider_y + 18
        for slot, character in enumerate(stage.get("team", [])[:4]):
            icon_x = box[0] + 24 + slot * (icon_size + 14)
            color = _element_color(character.get("element"))
            self._draw_square_chip(canvas, draw, (icon_x, row_y), character.get("icon"), icon_size, color)

            constellation = character.get("constellation", 0)
            if constellation:
                badge_r = 13
                bx, by = icon_x + badge_r - 2, row_y + badge_r - 2
                badge = _horizontal_gradient(badge_r * 2, badge_r * 2, GOLD[:3], (200, 140, 40))
                bmask = Image.new("L", (badge_r * 2, badge_r * 2), 0)
                ImageDraw.Draw(bmask).ellipse((0, 0, badge_r * 2 - 1, badge_r * 2 - 1), fill=255)
                bimg = Image.new("RGBA", (badge_r * 2, badge_r * 2), (0, 0, 0, 0))
                bimg.paste(badge, (0, 0), bmask)
                canvas.paste(bimg, (bx - badge_r, by - badge_r), bimg)
                draw.ellipse((bx - badge_r, by - badge_r, bx + badge_r, by + badge_r), outline=(255, 255, 255, 200), width=1)
                draw_text_with_shadow(draw, str(constellation), (bx, by), self.font_path, 13, text_color=(35, 22, 10, 255), anchor="mm")

            level_y = row_y + icon_size + 15
            draw_text_with_shadow(draw, f"Lv. {character.get('level', 0)}", (icon_x + icon_size // 2, level_y), self.font_path, 12, text_color=MUTED, anchor="mm")

        # boss icon
        boss_size = 82
        boss_x = box[2] - 30 - boss_size
        boss_y = row_y - 7
        self._draw_square_chip(canvas, draw, (boss_x, boss_y), stage.get("boss_icon"), boss_size, ACCENT_A, radius=18)
        draw_text_with_shadow(draw, f"Lv. {stage.get('boss_level', 90)}", (boss_x + boss_size // 2, boss_y + boss_size + 17), self.font_path, 12, text_color=MUTED, anchor="mm")

        # stat rows
        stat_y0 = row_y + icon_size + 40
        draw_text_with_shadow(draw, "Strongest Single Strike", (title_x, stat_y0), self.font_path, 15, text_color=MUTED, anchor="lm")
        draw_text_with_shadow(draw, f"{stage.get('strongest_strike', 0):,}", (box[2] - 30, stat_y0), self.font_path, 19, text_color=(255, 255, 255, 255), anchor="rm")
        stat_y1 = stat_y0 + 32
        draw_text_with_shadow(draw, "Highest Total Damage Dealt", (title_x, stat_y1), self.font_path, 15, text_color=MUTED, anchor="lm")
        draw_text_with_shadow(draw, f"{stage.get('highest_total_damage', 0):,}", (box[2] - 30, stat_y1), self.font_path, 19, text_color=(255, 255, 255, 255), anchor="rm")

        return y + height

    # ---- entrypoint -----------------------------------------------------

    async def build(self):
        stages = self._stages()
        header_h = 168
        best_record_h = 78
        stages_h = sum(self._stage_panel_height(stage) + GAP for stage in stages)

        total_height = MARGIN + header_h + GAP + best_record_h + GAP + stages_h + MARGIN
        total_height = max(int(total_height), 400)

        canvas = _vertical_gradient(CARD_WIDTH, total_height, BG_TOP, BG_BOTTOM)
        draw = ImageDraw.Draw(canvas)
        _draw_motes(draw, CARD_WIDTH, total_height)

        async with new_session() as session:
            await self._preload_icons(session)

            y = MARGIN
            y = self._draw_header(canvas, draw, y) + GAP
            y = self._draw_best_record(canvas, draw, y) + GAP
            if not stages:
                draw_text_with_shadow(draw, "No Stygian Onslaught data for this cycle yet", (CARD_WIDTH // 2, y + 40), self.font_path, 20, text_color=MUTED, anchor="mm")
            for index, stage in enumerate(stages):
                y = self._draw_stage(canvas, draw, index, stage, y) + GAP

        apply_watermark(canvas)
        buffer = BytesIO()
        canvas.convert("RGB").save(buffer, format="JPEG", quality=95)
        buffer.seek(0)
        buffer.name = f"stygian_{self.data.get('uid', 'card')}.jpg"
        return buffer


async def generate_stygian_card(data):
    return await StygianCardBuilder(data).build()
