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

DIFFICULTY_LABELS = {
    0: "Unknown",
    1: "Easy",
    2: "Normal",
    3: "Hard Mode",
    4: "Visionary",
    5: "Arcana Challenge",
}

# Imaginarium Theater design: velvet curtain burgundy, footlight gold, and a
# single soft spotlight glow. Kept deliberately separate from the Abyss
# (starlight violet) and Stygian (frost cyan) palettes so each report reads
# as its own collectible.
BG_TOP = (36, 12, 22, 255)
BG_BOTTOM = (12, 5, 10, 255)
PANEL_BG = (46, 20, 30, 225)
PANEL_BORDER = (168, 108, 91, 190)
MUTED = (221, 195, 200, 255)
GOLD = (255, 202, 110, 255)
WHITE = (250, 244, 240, 255)
ACCENT_A = (255, 168, 96, 255)   # footlight amber
ACCENT_B = (196, 78, 96, 255)    # curtain crimson

CARD_WIDTH = 1400
MARGIN = 40
GAP = 26
MAX_ACTS_SHOWN = 12


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


def _draw_spotlight(canvas, cx, cy, radius, color=(255, 200, 130, 26)):
    """A single soft radial glow behind the header, evoking a stage spotlight."""
    glow = Image.new("RGBA", (radius * 2, radius * 2), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    steps = 40
    for i in range(steps, 0, -1):
        t = i / steps
        r = int(radius * t)
        alpha = int(color[3] * (1 - t) ** 2)
        gdraw.ellipse((radius - r, radius - r, radius + r, radius + r), fill=(color[0], color[1], color[2], alpha))
    canvas.alpha_composite(glow, (cx - radius, cy - radius))


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


def _format_duration(seconds):
    seconds = int(seconds or 0)
    minutes, secs = divmod(seconds, 60)
    return f"{minutes}m {secs:02d}s"


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


class TheaterCardBuilder:
    """Builds an Imaginarium Theater report image from a genshin.py ImgTheaterData object.

    Only the data genshin.py actually exposes is shown: per-act blessing
    descriptions aren't part of the API response, so this card keeps the
    cast list to characters, levels, mystery caches and medal/arcana status
    instead of reproducing screenshot-only text.
    """

    def __init__(self, uid, theater, font_path=FONT_PATH):
        self.uid = uid
        if hasattr(theater, "datas") and theater.datas:
            self.theater = theater.datas[0]
        else:
            self.theater = theater
        self.font_path = font_path
        self._icons = {}

    # ---- asset loading -------------------------------------------------

    def _acts_to_render(self):
        if hasattr(self.theater, "acts"):
            return list(self.theater.acts)

        if hasattr(self.theater, "rounds"):
            return list(self.theater.rounds)

        if hasattr(self.theater, "levels"):
            return list(self.theater.levels)

        logger.warning(
            "Cannot find theater acts. Available attributes: %s",
            dir(self.theater)
        )

        return []

    def _collect_icon_urls(self):
        urls = set()
        stats = self.theater.battle_stats
        for entry in (stats.max_defeat_character, stats.max_damage_character, stats.max_take_damage_character):
            if entry:
                urls.add(entry.icon)
        for entry in stats.fastest_character_list:
            urls.add(entry.icon)
        for act in self._acts_to_render():
            for character in act.characters:
                urls.add(character.icon)
        return urls

    async def _preload_icons(self, session):
        for url in self._collect_icon_urls():
            self._icons[url] = await _load_icon(session, url)

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
        else:
            draw.polygon(_diamond_points(x, y, size - 1), fill=(60, 40, 46, 255))
        points = _diamond_points(x, y, size)
        draw.line(points + [points[0]], fill=border_color, width=3, joint="curve")

    # ---- layout pieces ---------------------------------------------------

    def _draw_section_label(self, draw, text, y):
        draw_text_with_shadow(draw, text.upper(), (MARGIN, y), self.font_path, 16, text_color=ACCENT_A, anchor="lm")
        draw.line((MARGIN, y + 22, CARD_WIDTH - MARGIN, y + 22), fill=PANEL_BORDER, width=1)
        return y + 40

    def _draw_header(self, canvas, draw, y):
        stats = self.theater.stats
        schedule = self.theater.schedule
        header_h = 150

        _draw_spotlight(canvas, CARD_WIDTH // 2, y + 10, 420)

        _rounded_panel(draw, (MARGIN, y, CARD_WIDTH - MARGIN, y + header_h), radius=22)

        draw_text_with_shadow(draw, "IMAGINARIUM THEATER", (CARD_WIDTH // 2, y + 40), self.font_path, 34, text_color=GOLD, anchor="mm")
        draw_text_with_shadow(draw, f"UID {self.uid}", (CARD_WIDTH // 2, y + 74), self.font_path, 16, text_color=MUTED, anchor="mm")

        if schedule is not None:
            period = f"{schedule.start_datetime:%Y.%m.%d} \u2014 {schedule.end_datetime:%Y.%m.%d}"
            draw_text_with_shadow(draw, period, (CARD_WIDTH // 2, y + 100), self.font_path, 15, text_color=MUTED, anchor="mm")

        difficulty_label = DIFFICULTY_LABELS.get(int(stats.difficulty), "Unknown")
        badge_w, badge_h = 190, 34
        bx0 = CARD_WIDTH // 2 - badge_w // 2
        by0 = y + header_h - 22
        badge = _horizontal_gradient(badge_w, badge_h, ACCENT_B, ACCENT_A, radius=17)
        canvas.paste(badge, (bx0, by0), badge)
        draw_text_with_shadow(draw, difficulty_label.upper(), (CARD_WIDTH // 2, by0 + badge_h // 2), self.font_path, 15, text_color=(30, 12, 10, 255), anchor="mm", shadow_color=(255, 255, 255, 0))

        return y + header_h

    def _stat_card(self, canvas, draw, box, label, value, sub=None):
        _rounded_panel(draw, box)
        x0, y0, x1, y1 = box
        cx = (x0 + x1) // 2
        draw_text_with_shadow(draw, label.upper(), (cx, y0 + 24), self.font_path, 14, text_color=MUTED, anchor="mm")
        draw_text_with_shadow(draw, str(value), (cx, y0 + 58), self.font_path, 30, text_color=GOLD, anchor="mm")
        if sub:
            draw_text_with_shadow(draw, sub, (cx, y0 + 84), self.font_path, 13, text_color=MUTED, anchor="mm")

    def _draw_overview(self, canvas, draw, y):
        y = self._draw_section_label(draw, "Past Performances", y)
        stats = self.theater.stats
        stars_obtained = sum(1 for got in stats.star_challenge_stellas if got)
        stars_total = len(stats.star_challenge_stellas) or 8

        entries = [
            ("Best Record", f"Act {stats.best_record}", None),
            ("Star Challenge", f"{stars_obtained}/{stars_total}", "stellas obtained"),
            ("Medals Earned", stats.medal_num, None),
            ("Fantasia Flowers", f"{stats.fantasia_flowers_used:,}", "used"),
            ("Audience Support", stats.audience_support_trigger_num, "triggers"),
            ("Player Assists", stats.player_assists, "times"),
        ]

        cols = 3
        card_width = (CARD_WIDTH - 2 * MARGIN - (cols - 1) * GAP) // cols
        card_height = 108
        for index, (label, value, sub) in enumerate(entries):
            row, col = divmod(index, cols)
            x0 = MARGIN + col * (card_width + GAP)
            y0 = y + row * (card_height + GAP)
            self._stat_card(canvas, draw, (x0, y0, x0 + card_width, y0 + card_height), label, value, sub)

        rows = -(-len(entries) // cols)
        return y + rows * (card_height + GAP) - GAP

    def _honour_card(self, canvas, draw, box, label, character, suffix):
        _rounded_panel(draw, box)
        x0, y0, x1, y1 = box
        if character is None:
            draw_text_with_shadow(draw, label, (x0 + 24, y0 + 24), self.font_path, 16, text_color=MUTED, anchor="lm")
            draw_text_with_shadow(draw, "No data", (x0 + 24, y0 + 56), self.font_path, 18, anchor="lm")
            return

        icon_size = 64
        icon_x, icon_y = x0 + 24, y0 + (y1 - y0) // 2 - icon_size // 2
        color = GOLD if character.rarity >= 5 else (200, 190, 210, 255)
        self._draw_diamond_chip(canvas, draw, (icon_x, icon_y), character.icon, icon_size, color)

        text_x = icon_x + icon_size + 22
        draw_text_with_shadow(draw, label, (text_x, y0 + 26), self.font_path, 14, text_color=MUTED, anchor="lm")
        draw_text_with_shadow(draw, f"{character.value:,}", (x1 - 24, y0 + (y1 - y0) // 2), self.font_path, 30, text_color=GOLD, anchor="rm")
        draw_text_with_shadow(draw, suffix, (x1 - 24, y0 + (y1 - y0) // 2 + 26), self.font_path, 13, text_color=MUTED, anchor="rm")

    def _draw_battle_honours(self, canvas, draw, y):
        y = self._draw_section_label(draw, "Battle Honours", y)
        stats = self.theater.battle_stats
        entries = [
            ("Highest Damage Dealt", stats.max_damage_character, "damage"),
            ("Most Opponents Defeated", stats.max_defeat_character, "defeated"),
            ("Most Damage Taken", stats.max_take_damage_character, "damage taken"),
        ]

        cols = 3
        card_width = (CARD_WIDTH - 2 * MARGIN - (cols - 1) * GAP) // cols
        card_height = 108
        for index, (label, character, suffix) in enumerate(entries):
            x0 = MARGIN + index * (card_width + GAP)
            self._honour_card(canvas, draw, (x0, y, x0 + card_width, y + card_height), label, character, suffix)
        y += card_height + GAP

        # Fastest team + total cast time, as a single wide strip
        strip_h = 96
        box = (MARGIN, y, CARD_WIDTH - MARGIN, y + strip_h)
        _rounded_panel(draw, box)
        draw_text_with_shadow(draw, "FASTEST TEAM", (MARGIN + 24, y + 22), self.font_path, 14, text_color=MUTED, anchor="lm")

        icon_size = 46
        icon_x = MARGIN + 24
        icon_y = y + strip_h - icon_size - 16
        for character in list(stats.fastest_character_list)[:4]:
            color = GOLD if character.rarity >= 5 else (200, 190, 210, 255)
            self._draw_diamond_chip(canvas, draw, (icon_x, icon_y), character.icon, icon_size, color)
            icon_x += icon_size + 14

        draw_text_with_shadow(draw, _format_duration(stats.total_cast_seconds), (CARD_WIDTH - MARGIN - 24, y + strip_h // 2), self.font_path, 30, text_color=GOLD, anchor="rm")
        draw_text_with_shadow(draw, "fastest clear time", (CARD_WIDTH - MARGIN - 24, y + strip_h - 22), self.font_path, 13, text_color=MUTED, anchor="rm")

        return y + strip_h

    def _act_panel_height(self, act):
        return 150

    def _draw_act(self, canvas, draw, act, box):
        x0, y0, x1, y1 = box

        _rounded_panel(
            draw,
            box,
            radius=18
        )


        # -----------------------
        # LEFT - Act + Date
        # -----------------------

        title = f"Act {act.round_id}"

        if act.is_arcana:
            title += (
                f" • Arcana {act.arcana_number}"
                if act.arcana_number
                else " • Arcana"
            )

        draw_text_with_shadow(
            draw,
            title,
            (x0 + 20, y0 + 30),
            self.font_path,
            18,
            anchor="lm"
        )


        draw_text_with_shadow(
            draw,
            f"{act.finish_datetime:%Y.%m.%d}",
            (x0 + 20, y0 + 58),
            self.font_path,
            12,
            text_color=MUTED,
            anchor="lm"
        )



        # -----------------------
        # CENTER - Characters
        # -----------------------

        characters = list(act.characters)[:6]

        icon_size = 105
        spacing = 12

        total_width = (
            len(characters) * icon_size
            +
            (len(characters)-1)*spacing
        )


        center_x = (x0+x1)//2

        icon_x = center_x - total_width//2
        icon_y = y0 + (y1 - y0)//2 - icon_size//2


        for character in characters:

            self._draw_diamond_chip(
                canvas,
                draw,
                (
                    icon_x,
                    icon_y
                ),
                character.icon,
                icon_size,
                _element_color(character.element)
            )

            icon_x += icon_size + spacing



        # -----------------------
        # RIGHT - Medal
        # -----------------------

        badge_w = 90
        badge_h = 30

        bx = x1 - badge_w - 20
        by = y0 + 30


        badge_color = (
            GOLD
            if act.medal_obtained
            else (90,70,76,255)
        )


        draw.rounded_rectangle(
            (
                bx,
                by,
                bx+badge_w,
                by+badge_h
            ),
            radius=15,
            fill=badge_color
        )


        draw_text_with_shadow(
            draw,
            "MEDAL" if act.medal_obtained else "NONE",
            (
                bx+badge_w//2,
                by+badge_h//2
            ),
            self.font_path,
            11,
            text_color=(30,12,10,255),
            anchor="mm",
            shadow_color=(0,0,0,0)
        )
    # ---- entrypoint -------------------------------------------------------
    def _draw_acts_grid(self, canvas, draw, acts, y):

        card_height = 150

        cols = 2

        card_width = (
            CARD_WIDTH
            - 2*MARGIN
            - GAP
        ) // cols


        for index, act in enumerate(acts):

            row = index // cols
            col = index % cols


            x = (
                MARGIN
                + col*(card_width+GAP)
            )

            y_pos = (
                y
                + row*(card_height+GAP)
            )


            self._draw_act(
                canvas,
                draw,
                act,
                (
                    x,
                    y_pos,
                    x+card_width,
                    y_pos+card_height
                )
            )


        rows = (len(acts)+1)//2


        return (
            y
            + rows*(card_height+GAP)
            - GAP
        )
    async def build(self):
        acts = self._acts_to_render()

        header_h = 150
        overview_h = 40 + 2 * (108 + GAP) - GAP
        honours_h = 40 + (108 + GAP) + 96
        act_rows = (len(acts)+1)//2

        acts_h = (
            act_rows *
            (150 + GAP)
        )

        total_height = MARGIN + header_h + GAP + overview_h + GAP + honours_h + GAP + acts_h + MARGIN
        total_height = max(int(total_height), 400)

        canvas = _vertical_gradient(CARD_WIDTH, total_height, BG_TOP, BG_BOTTOM).convert("RGBA")
        draw = ImageDraw.Draw(canvas)

        async with new_session() as session:
            await self._preload_icons(session)

            y = MARGIN
            y = self._draw_header(canvas, draw, y) + GAP
            y = self._draw_overview(canvas, draw, y) + GAP
            y = self._draw_battle_honours(canvas, draw, y) + GAP
            y = self._draw_acts_grid(
                canvas,
                draw,
                acts,
                y
            )

        apply_watermark(canvas)
        buffer = BytesIO()
        canvas.convert("RGB").save(buffer, format="JPEG", quality=95)
        buffer.seek(0)
        buffer.name = f"theater_{self.uid}.jpg"
        return buffer


async def generate_theater_card(uid, theater):
    return await TheaterCardBuilder(uid, theater).build()