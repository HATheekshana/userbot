import logging
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

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

# Astral Observatory design: midnight violet glass panels, starlight blue,
# and comet-gold highlights. It is deliberately separate from the character
# card palette so Abyss reports read as their own collectible report.
BG_TOP = (20, 13, 48, 255)
BG_BOTTOM = (4, 9, 27, 255)
PANEL_BG = (20, 26, 57, 230)
PANEL_BORDER = (91, 108, 168, 210)
MUTED = (175, 187, 221, 255)
GOLD = (255, 203, 100, 255)
WHITE = (245, 248, 255, 255)
ACCENT_A = (92, 207, 255, 255)   # starlight blue
ACCENT_B = (154, 111, 255, 255)  # astral violet

CARD_WIDTH = 1400
MARGIN = 40
GAP = 26
MAX_FLOORS_SHOWN = 4
MAX_ABYSS_STARS = 36  # current format: 4 counted floors x 3 chambers x 3 stars


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


def _draw_starfield(draw, width, height):
    """A deterministic star field gives every report an astral backdrop."""
    for index in range(155):
        x = (index * 197 + 71) % width
        y = (index * 113 + 31) % height
        radius = 1 if index % 7 else 2
        alpha = 65 + (index * 29) % 115
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=(180, 216, 255, alpha))


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


def draw_text_with_shadow(draw, text, position, font_path, font_size, text_color=WHITE, shadow_color=(0, 0, 0, 180), anchor="mm", shadow_offset=(2, 2)):
    font = ImageFont.truetype(font_path, font_size)
    shadow_pos = (position[0] + shadow_offset[0], position[1] + shadow_offset[1])
    draw.text(shadow_pos, text, font=font, fill=shadow_color, anchor=anchor)
    draw.text(position, text, font=font, fill=text_color, anchor=anchor)


def _rounded_panel(draw, box, radius=20, fill=PANEL_BG, outline=PANEL_BORDER, width=2):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def _diamond_points(x, y, size):
    half = size / 2
    return [(x + half, y), (x + size, y + half), (x + half, y + size), (x, y + half)]


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


class AbyssCardBuilder:
    """Builds a Spiral Abyss report image from a genshin.py SpiralAbyss object."""

    def __init__(self, uid, abyss, font_path=FONT_PATH):
        self.uid = uid
        self.abyss = abyss
        self.font_path = font_path
        self._icons = {}

    # ---- asset loading -------------------------------------------------

    def _collect_icon_urls(self):
        urls = set()
        ranks = self.abyss.ranks
        for rank_list in (
            ranks.most_played,
            ranks.most_kills,
            ranks.strongest_strike,
            ranks.most_damage_taken,
            ranks.most_bursts_used,
            ranks.most_skills_used,
        ):
            for entry in rank_list:
                urls.add(entry.icon)
        for floor in self._floors_to_render():
            for chamber in floor.chambers:
                for battle in chamber.battles:
                    for character in battle.characters:
                        urls.add(character.icon)
        return urls

    async def _preload_icons(self, session):
        for url in self._collect_icon_urls():
            self._icons[url] = await _load_icon(session, url)

    def _floors_to_render(self):
        floors = [floor for floor in self.abyss.floors if floor.chambers]
        return floors[-MAX_FLOORS_SHOWN:]

    def _diamond_chip(self, url, size):
        image = self._icons.get(url)
        if image is None:
            return None
        image = image.resize((size, size), Image.Resampling.LANCZOS)
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).polygon(_diamond_points(0, 0, size - 1), fill=255)
        diamond = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        diamond.paste(image, (0, 0), mask)
        return diamond

    def _draw_diamond_chip(self, canvas, draw, position, url, size, border_color):
        x, y = position
        chip = self._diamond_chip(url, size)
        if chip:
            canvas.paste(chip, (x, y), chip)
        points = _diamond_points(x, y, size)
        draw.line(points + [points[0]], fill=border_color, width=3, joint="curve")

    # ---- layout pieces ---------------------------------------------------

    def _draw_ring_gauge(self, draw, center, radius, thickness, percent, value_text, label_text, color):
        cx, cy = center
        bbox = (cx - radius, cy - radius, cx + radius, cy + radius)
        draw.arc(bbox, 0, 360, fill=(255, 255, 255, 45), width=thickness)
        end_angle = -90 + 360 * max(0.0, min(1.0, percent))
        if percent > 0:
            draw.arc(bbox, -90, end_angle, fill=color, width=thickness)
        draw_text_with_shadow(draw, value_text, (cx, cy - 6), self.font_path, 28, text_color=color, anchor="mm")
        draw_text_with_shadow(draw, label_text, (cx, cy + 20), self.font_path, 13, text_color=MUTED, anchor="mm")

    def _draw_header(self, canvas, draw, y):
        height = 190
        box = (MARGIN, y, CARD_WIDTH - MARGIN, y + height)
        _rounded_panel(draw, box, radius=26)

        # thin gradient accent strip along the top edge of the panel
        strip = _horizontal_gradient(box[2] - box[0] - 4, 6, ACCENT_A, ACCENT_B)
        canvas.paste(strip, (box[0] + 2, box[1] + 2), strip)

        # season badge - diamond with a gradient fill
        badge_size = 92
        badge_x, badge_y = MARGIN + 34, y + (height - badge_size) // 2
        badge_fill = _horizontal_gradient(badge_size, badge_size, ACCENT_A, ACCENT_B)
        mask = Image.new("L", (badge_size, badge_size), 0)
        ImageDraw.Draw(mask).polygon(_diamond_points(0, 0, badge_size - 1), fill=255)
        badge = Image.new("RGBA", (badge_size, badge_size), (0, 0, 0, 0))
        badge.paste(badge_fill, (0, 0), mask)
        canvas.paste(badge, (badge_x, badge_y), badge)
        season = getattr(self.abyss, "season", "?")
        draw_text_with_shadow(draw, str(season), (badge_x + badge_size // 2, badge_y + badge_size // 2), self.font_path, 30, text_color=(15, 15, 25, 255), anchor="mm")
        draw_text_with_shadow(draw, "SEASON", (badge_x + badge_size // 2, badge_y + badge_size + 14), self.font_path, 13, text_color=MUTED, anchor="mm")

        text_x = badge_x + badge_size + 40
        draw_text_with_shadow(draw, "Spiral Abyss Report", (text_x, y + 62), self.font_path, 40, anchor="lm")
        draw_text_with_shadow(draw, f"UID {self.uid}", (text_x, y + 100), self.font_path, 19, text_color=MUTED, anchor="lm")

        stars_percent = self.abyss.total_stars / MAX_ABYSS_STARS if MAX_ABYSS_STARS else 0
        try:
            floor_num = int(str(self.abyss.max_floor).split("-")[0])
        except (ValueError, IndexError):
            floor_num = 0
        floor_percent = floor_num / 12

        self._draw_ring_gauge(draw, (CARD_WIDTH - MARGIN - 260, y + height // 2), 58, 10, stars_percent, str(self.abyss.total_stars), "STARS", ACCENT_A)
        self._draw_ring_gauge(draw, (CARD_WIDTH - MARGIN - 110, y + height // 2), 58, 10, floor_percent, str(self.abyss.max_floor), "MAX FLOOR", ACCENT_B)

        return y + height

    def _draw_section_label(self, draw, text, y):
        points = _diamond_points(MARGIN, y + 4, 16)
        draw.polygon(points, fill=ACCENT_A)
        draw_text_with_shadow(draw, text, (MARGIN + 30, y + 12), self.font_path, 26, anchor="lm")
        return y + 46

    def _draw_honour_card(self, canvas, draw, box, label, entry, suffix):
        x0, y0, x1, y1 = box
        _rounded_panel(draw, box)
        accent = _horizontal_gradient(x1 - x0 - 4, 4, ACCENT_A, ACCENT_B)
        canvas.paste(accent, (x0 + 2, y0 + 2), accent)

        if entry is None:
            draw_text_with_shadow(draw, "No data", ((x0 + x1) // 2, (y0 + y1) // 2), self.font_path, 20, text_color=MUTED, anchor="mm")
            return

        icon_size = 66
        icon_x, icon_y = x0 + 22, y0 + (y1 - y0 - icon_size) // 2 + 4
        self._draw_diamond_chip(canvas, draw, (icon_x, icon_y), entry.icon, icon_size, _element_color(entry.element))

        text_x = icon_x + icon_size + 24
        draw_text_with_shadow(draw, label.upper(), (text_x, y0 + 28), self.font_path, 15, text_color=MUTED, anchor="lm")
        draw_text_with_shadow(draw, entry.name, (text_x, y0 + 56), self.font_path, 25, anchor="lm")
        draw_text_with_shadow(draw, entry.element, (text_x, y0 + 82), self.font_path, 15, text_color=_element_color(entry.element), anchor="lm")

        draw_text_with_shadow(draw, f"{entry.value:,}", (x1 - 24, y0 + (y1 - y0) // 2 - 10), self.font_path, 29, text_color=GOLD, anchor="rm")
        draw_text_with_shadow(draw, suffix, (x1 - 24, y0 + (y1 - y0) // 2 + 16), self.font_path, 14, text_color=MUTED, anchor="rm")

    def _draw_battle_honours(self, canvas, draw, y):
        y = self._draw_section_label(draw, "Battle Honours", y)
        ranks = self.abyss.ranks
        entries = [
            ("Strongest Strike", ranks.strongest_strike[0] if ranks.strongest_strike else None, "damage"),
            ("Most Kills", ranks.most_kills[0] if ranks.most_kills else None, "kills"),
            ("Most Bursts", ranks.most_bursts_used[0] if ranks.most_bursts_used else None, "bursts"),
            ("Most Dmg Taken", ranks.most_damage_taken[0] if ranks.most_damage_taken else None, "absorbed"),
            ("Most Skills", ranks.most_skills_used[0] if ranks.most_skills_used else None, "casts"),
        ]

        cols = 3
        card_width = (CARD_WIDTH - 2 * MARGIN - (cols - 1) * GAP) // cols
        card_height = 116
        for index, (label, entry, suffix) in enumerate(entries):
            row, col = divmod(index, cols)
            x0 = MARGIN + col * (card_width + GAP)
            y0 = y + row * (card_height + GAP)
            self._draw_honour_card(canvas, draw, (x0, y0, x0 + card_width, y0 + card_height), label, entry, suffix)

        rows = -(-len(entries) // cols)
        return y + rows * (card_height + GAP) - GAP + 20

    def _draw_most_deployed(self, canvas, draw, y):
        y = self._draw_section_label(draw, "Most Deployed", y)
        entries = list(self.abyss.ranks.most_played)[:6]
        if not entries:
            draw_text_with_shadow(draw, "No data", (MARGIN + 20, y + 20), self.font_path, 18, text_color=MUTED, anchor="lm")
            return y + 60

        max_value = max((entry.value for entry in entries), default=1) or 1
        row_height = 78
        panel_box = (MARGIN, y, CARD_WIDTH - MARGIN, y + len(entries) * row_height + 20)
        _rounded_panel(draw, panel_box)

        icon_size = 52
        for index, entry in enumerate(entries):
            row_y = y + 10 + index * row_height
            color = _element_color(entry.element)
            self._draw_diamond_chip(canvas, draw, (MARGIN + 20, row_y), entry.icon, icon_size, color)

            # rank badge overlapping the icon's top-left corner
            badge_r = 12
            bx, by = MARGIN + 20, row_y
            draw.ellipse((bx - badge_r, by - badge_r, bx + badge_r, by + badge_r), fill=ACCENT_B)
            draw_text_with_shadow(draw, str(index + 1), (bx, by), self.font_path, 14, text_color=(255, 255, 255, 255), anchor="mm")

            text_x = MARGIN + 20 + icon_size + 26
            draw_text_with_shadow(draw, entry.name, (text_x, row_y + 10), self.font_path, 22, anchor="lm")
            draw_text_with_shadow(draw, entry.element, (text_x, row_y + 34), self.font_path, 14, text_color=color, anchor="lm")

            bar_x0 = text_x + 180
            bar_y0 = row_y + 20
            bar_width = CARD_WIDTH - MARGIN - 130 - bar_x0
            bar_height = 12
            draw.rounded_rectangle((bar_x0, bar_y0, bar_x0 + bar_width, bar_y0 + bar_height), radius=6, fill=(50, 48, 68, 255))
            fill_width = max(10, int(bar_width * (entry.value / max_value)))
            fill_bar = _horizontal_gradient(fill_width, bar_height, ACCENT_A, color, radius=6)
            canvas.paste(fill_bar, (bar_x0, bar_y0), fill_bar)

            draw_text_with_shadow(draw, str(entry.value), (CARD_WIDTH - MARGIN - 20, row_y + 12), self.font_path, 24, text_color=color, anchor="rm")
            draw_text_with_shadow(draw, "battles", (CARD_WIDTH - MARGIN - 20, row_y + 36), self.font_path, 13, text_color=MUTED, anchor="rm")

        return panel_box[3] + 20

    def _draw_pip_row(self, draw, position, filled, total=3, size=13, gap=5):
        x, y = position
        for index in range(total):
            fill = GOLD if index < filled else (70, 68, 90, 255)
            points = _diamond_points(x + index * (size + gap), y, size)
            draw.polygon(points, fill=fill)
        return x + total * (size + gap)

    def _draw_battle_row(self, canvas, draw, x, y, label, characters, icon_size=44, gap=10):
        draw_text_with_shadow(draw, label, (x, y), self.font_path, 13, text_color=MUTED, anchor="lm")
        icon_y = y + 16
        for index, character in enumerate(characters[:4]):
            icon_x = x + index * (icon_size + gap)
            self._draw_diamond_chip(canvas, draw, (icon_x, icon_y), character.icon, icon_size, _element_color(character.element))
            draw_text_with_shadow(draw, f"Lv.{character.level}", (icon_x + icon_size // 2, icon_y + icon_size + 13), self.font_path, 12, text_color=MUTED, anchor="mm")
        return icon_y + icon_size + 26

    def _floor_panel_height(self, floor):
        chamber_heights = []
        for chamber in floor.chambers:
            height = 30  # chamber label + pips
            for battle in chamber.battles:
                height += 16 + 44 + 18
            chamber_heights.append(height)
        return 56 + max(chamber_heights, default=90) + 18

    def _draw_floor(self, canvas, draw, floor, y, is_last):
        height = self._floor_panel_height(floor)
        spine_x = MARGIN + 22
        node_radius = 22

        # timeline spine connecting this floor's node to the next
        if not is_last:
            draw.line((spine_x, y + node_radius, spine_x, y + height + GAP), fill=PANEL_BORDER, width=3)

        node_center = (spine_x, y + node_radius + 6)
        node_gradient = _horizontal_gradient(node_radius * 2, node_radius * 2, ACCENT_A, ACCENT_B)
        node_mask = Image.new("L", (node_radius * 2, node_radius * 2), 0)
        ImageDraw.Draw(node_mask).ellipse((0, 0, node_radius * 2 - 1, node_radius * 2 - 1), fill=255)
        node_img = Image.new("RGBA", (node_radius * 2, node_radius * 2), (0, 0, 0, 0))
        node_img.paste(node_gradient, (0, 0), node_mask)
        canvas.paste(node_img, (node_center[0] - node_radius, node_center[1] - node_radius), node_img)
        draw_text_with_shadow(draw, str(floor.floor), node_center, self.font_path, 20, text_color=(15, 15, 25, 255), anchor="mm")

        panel_x0 = spine_x + node_radius + 24
        box = (panel_x0, y, CARD_WIDTH - MARGIN, y + height)
        _rounded_panel(draw, box, radius=18)

        subtitle = "Perfect Clear" if floor.stars == floor.max_stars else f"{floor.stars}/{floor.max_stars} Stars"
        draw_text_with_shadow(draw, f"Floor {floor.floor} \u2014 {subtitle}", (panel_x0 + 24, y + 26), self.font_path, 19, anchor="lm")
        self._draw_pip_row(draw, (CARD_WIDTH - MARGIN - 24 - floor.max_stars * 18, y + 18), floor.stars, total=floor.max_stars, size=13)

        draw.line((panel_x0 + 24, y + 46, CARD_WIDTH - MARGIN - 24, y + 46), fill=PANEL_BORDER, width=1)

        cols = max(len(floor.chambers), 1)
        inner_width = (CARD_WIDTH - MARGIN - 24) - (panel_x0 + 24)
        col_width = (inner_width - (cols - 1) * GAP) // cols
        for index, chamber in enumerate(floor.chambers):
            col_x = panel_x0 + 24 + index * (col_width + GAP)
            row_y = y + 60
            draw_text_with_shadow(draw, f"Chamber {chamber.chamber}", (col_x, row_y), self.font_path, 16, anchor="lm")
            self._draw_pip_row(draw, (col_x + 140, row_y - 6), chamber.stars, total=chamber.max_stars, size=11)
            row_y += 24
            for battle in chamber.battles:
                half_label = "1ST HALF" if battle.half == 1 else "2ND HALF"
                row_y = self._draw_battle_row(canvas, draw, col_x, row_y, half_label, list(battle.characters))

        return y + height

    # ---- entrypoint -------------------------------------------------------

    async def build(self):
        floors = self._floors_to_render()

        header_h = 190
        honours_h = 46 + 2 * (116 + GAP) - GAP + 20
        deployed_count = min(len(self.abyss.ranks.most_played), 6)
        deployed_h = 46 + max(deployed_count, 1) * 78 + 20 + 20
        floors_h = sum(self._floor_panel_height(floor) + GAP for floor in floors)

        total_height = MARGIN + header_h + GAP + honours_h + GAP + deployed_h + GAP + floors_h + MARGIN
        total_height = max(int(total_height), 400)

        canvas = _vertical_gradient(CARD_WIDTH, total_height, BG_TOP, BG_BOTTOM)
        draw = ImageDraw.Draw(canvas)
        _draw_starfield(draw, CARD_WIDTH, total_height)

        async with new_session() as session:
            await self._preload_icons(session)

            y = MARGIN
            y = self._draw_header(canvas, draw, y) + GAP
            y = self._draw_battle_honours(canvas, draw, y) + GAP
            y = self._draw_most_deployed(canvas, draw, y) + GAP
            for index, floor in enumerate(floors):
                y = self._draw_floor(canvas, draw, floor, y, is_last=(index == len(floors) - 1)) + GAP

        buffer = BytesIO()
        canvas.convert("RGB").save(buffer, format="JPEG", quality=95)
        buffer.seek(0)
        buffer.name = f"abyss_{self.uid}.jpg"
        return buffer


async def generate_abyss_card(uid, abyss):
    return await AbyssCardBuilder(uid, abyss).build()
