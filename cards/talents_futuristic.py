"""
talents_futuristic.py

Futuristic-HUD counterpart to t_c.py's draw_build_column. Same data
contract (build_data with "talents"/"cons_count", plus the talent_icons /
constellation_icons image lists already fetched by CharacterBuildFetcher),
but talents/constellations are framed with neon hexagons instead of the
classic circular game-asset frames.
"""
from PIL import Image, ImageDraw

from cards.futuristic_theme import (
    TEXT_DIM, GOLD, ACCENT,
    draw_hex_frame, paste_hex_masked, neon_text,
)

TALENT_HEX_R = 46
CONST_HEX_R = 34


def draw_build_column_futuristic(canvas, start_x, data, talent_icons, constellation_icons, font_hex, accent=ACCENT):
    draw = ImageDraw.Draw(canvas)

    talent_x = start_x + 30 + TALENT_HEX_R
    talent_y_base = 330 + TALENT_HEX_R
    for index, icon in enumerate(talent_icons):
        if not icon:
            continue
        cy = talent_y_base + index * 105
        level = data["talents"][index] if index < len(data["talents"]) else 1
        maxed = level >= 10

        frame_color = GOLD if maxed else accent
        draw_hex_frame(canvas, talent_x, cy, TALENT_HEX_R, fill=(8, 12, 20, 210), outline=frame_color, width=3)
        paste_hex_masked(canvas, icon, talent_x, cy, TALENT_HEX_R - 8)

        badge_y = cy + TALENT_HEX_R + 12
        badge_color = GOLD if maxed else (255, 255, 255, 255)
        draw.ellipse(
            [talent_x - 16, badge_y - 14, talent_x + 16, badge_y + 14],
            fill=(10, 14, 22, 220), outline=(badge_color[0], badge_color[1], badge_color[2], 200), width=1,
        )
        draw.text((talent_x, badge_y), str(level), font=font_hex, fill=badge_color, anchor="mm")

    const_x = start_x - 600 + CONST_HEX_R
    const_y_base = 250 + CONST_HEX_R
    for index, icon in enumerate(constellation_icons):
        if not icon:
            continue
        cy = const_y_base + index * 95
        is_locked = index >= data["cons_count"]

        if is_locked:
            draw_hex_frame(canvas, const_x, cy, CONST_HEX_R, fill=(6, 8, 12, 190), outline=(120, 130, 140, 140), width=2)
            gray_icon = icon.convert("L").convert("RGBA")
            gray_icon.putalpha(icon.getchannel("A").point(lambda p: int(p * 0.35)))
            paste_hex_masked(canvas, gray_icon, const_x, cy, CONST_HEX_R - 6)
        else:
            draw_hex_frame(canvas, const_x, cy, CONST_HEX_R, fill=(8, 12, 20, 210), outline=accent, width=3)
            paste_hex_masked(canvas, icon, const_x, cy, CONST_HEX_R - 6)

    return canvas