"""
Futuristic dark-themed character card - a brand new visual design, kept
fully separate from character_card.py.

CharacterCardGenerator (character_card.py) is imported and subclassed, not
edited: every data-fetching / lookup / caching method (splash & namecard
resolution, HoYoLAB fallbacks, stat extraction, char.json persistence,
custom_splash loading, etc.) is reused as-is. Only generate_card() is
overridden, and it calls out to the new draw_* helpers in
futuristic_theme.py / artifacts_futuristic.py / talents_futuristic.py
instead of the classic drawing code.

Same public contract as the classic generator:
    FuturisticCharacterCardGenerator().generate_card(uid, char_id) -> BytesIO (JPEG)
so bot.py can swap between the two without any other code caring.
"""
import logging
from io import BytesIO
from PIL import Image, ImageDraw, ImageOps, ImageFont

from cards.character_card import CharacterCardGenerator, W_STAT_ICONS
from cards.artifacts_futuristic import draw_horizontal_artifacts_futuristic
from cards.talents_futuristic import draw_build_column_futuristic
from cards.watermark import apply_watermark
from services.net import new_session
from cards.futuristic_theme import (
    ACCENT, TEXT_MAIN, TEXT_DIM, GOLD,
    element_color, base_atmosphere, darken_and_desaturate, scanlines,
    draw_glass_panel, draw_corner_brackets, draw_diagonal_tag,
    neon_text, segmented_bar, stat_row_icon_bg, hex_points,
    draw_hex_frame, paste_hex_masked,
)

logger = logging.getLogger("genshin_userbot")

CARD_SIZE = (1875, 890)
SPLASH_CUT_X = 760
SPLASH_FADE = 190


def _paste_splash_futuristic(ui_layer, splash_image, size, accent):
    """Left-hand splash art with a diagonal neon-edged cutoff, replacing
    the classic straight vertical fade."""
    card_width, card_height = size
    left_width = SPLASH_CUT_X
    fade_width = SPLASH_FADE

    scale = card_height / splash_image.height
    splash_image = splash_image.resize((int(splash_image.width * scale), card_height), Image.Resampling.LANCZOS)
    splash_image = splash_image.crop(((splash_image.width - left_width) // 2, 0, (splash_image.width - left_width) // 2 + left_width, card_height))

    mask = Image.new("L", (left_width, card_height), 255)
    skew = 90
    for y in range(card_height):
        edge = left_width - fade_width + int(skew * (y / card_height))
        for step in range(fade_width):
            x = edge + step
            if 0 <= x < left_width:
                mask.putpixel((x, y), int(255 * (1 - step / fade_width)))

    splash_image.putalpha(Image.composite(splash_image.getchannel("A"), Image.new("L", mask.size, 0), Image.eval(mask, lambda p: 255 if p == 255 else p)))
    ui_layer.paste(splash_image, (0, 0), splash_image)

    # neon rim line traced along the fade's leading edge
    rim = Image.new("RGBA", size, (0, 0, 0, 0))
    rdraw = ImageDraw.Draw(rim)
    points = []
    for y in range(0, card_height, 4):
        edge_x = left_width - fade_width + int(skew * (y / card_height))
        points.append((edge_x, y))
    if len(points) > 1:
        rdraw.line(points, fill=accent, width=3, joint="curve")
    from PIL import ImageFilter
    glow = rim.filter(ImageFilter.GaussianBlur(6))
    ui_layer.alpha_composite(glow)
    ui_layer.alpha_composite(rim)
    return ui_layer


class FuturisticCharacterCardGenerator(CharacterCardGenerator):
    """Drop-in replacement for CharacterCardGenerator with an entirely
    different look. All constructor args (char_map_path, namecard_path,
    text_map_path, splash_directory, font_path) behave identically - same
    char.json / data.json / new.json / custom_splash directory are reused,
    so custom splashes uploaded for the classic design work here too."""

    async def generate_card(self, uid, char_id):
        player_profile = await self.player_data_provider.fetch_player_profile(uid)
        if not player_profile or not player_profile.get("avatarInfoList"):
            raise RuntimeError(f"No player profile available for uid={uid}")

        if not self._find_avatar_record(player_profile["avatarInfoList"], char_id):
            if not await self._ensure_character_record(uid, char_id, player_profile):
                raise RuntimeError(f"Character record not found for char_id={char_id} in uid={uid}")

        build_data, talent_icons, constellation_icons = await self.build_fetcher.fetch_build_assets(
            uid, char_id, avatar_data=player_profile
        )
        if not build_data:
            raise RuntimeError(f"No build/assets data found for uid={uid}, char_id={char_id}")

        character_info = await self._lookup_character_info(char_id, uid)
        avatar_record = self._find_avatar_record(player_profile["avatarInfoList"], char_id)
        if not avatar_record:
            raise RuntimeError(f"Character record not found for char_id={char_id} in uid={uid}")

        stats = self._extract_character_stats(player_profile["avatarInfoList"], char_id, character_info.get("element", "Anemo"))
        if not stats:
            raise RuntimeError(f"Character stats not found for char_id={char_id} in uid={uid}")

        avatar_icon = character_info.get("avataricon", "UI_AvatarIcon_Zibai")
        character_name = avatar_icon.replace("UI_AvatarIcon_", "")
        character_level = stats.get("char_level", 1)
        friendship_level = stats.get("friendship", 1)
        target_size = CARD_SIZE
        accent = element_color(stats.get("element"))

        font_tiny = ImageFont.truetype(self.font_path, 18)
        font_small = ImageFont.truetype(self.font_path, 20)
        font_label = ImageFont.truetype(self.font_path, 22)
        font_value = ImageFont.truetype(self.font_path, 26)
        font_name = ImageFont.truetype(self.font_path, 46)
        font_nick = ImageFont.truetype(self.font_path, 24)
        font_weapon = ImageFont.truetype(self.font_path, 30)
        font_hex = ImageFont.truetype(self.font_path, 20)

        async with new_session() as session:
            custom_splash = await self._load_custom_splash(char_id)
            splash_image = custom_splash or await self._load_image(session, self._get_splash_url(avatar_icon))
            background_url = self._get_namecard_urls(avatar_icon)
            background_image = None
            for url in background_url:
                background_image = await self._load_image(session, url)
                if background_image:
                    break

            weapon_icon = stats["weapon"].get("icon")
            weapon_icon_url = stats["weapon"].get("icon_url")
            if weapon_icon_url:
                weapon_image = await self._load_image(session, weapon_icon_url)
            elif weapon_icon:
                weapon_image = await self._load_image(session, f"https://enka.network/ui/{weapon_icon}.png")
            else:
                weapon_image = None

            # --- background: namecard art folded into the dark HUD atmosphere ---
            canvas = base_atmosphere(target_size)
            if background_image:
                bg_fit = ImageOps.fit(background_image.convert("RGBA"), target_size, method=Image.Resampling.LANCZOS)
                bg_fit = darken_and_desaturate(bg_fit, brightness=0.42, saturation=0.5)
                canvas = Image.alpha_composite(canvas, bg_fit)

            ui_layer = Image.new("RGBA", target_size, (0, 0, 0, 0))

            if splash_image:
                ui_layer = _paste_splash_futuristic(ui_layer, splash_image, target_size, accent)

            draw = ImageDraw.Draw(ui_layer)

            # top accent bar
            draw.rectangle([0, 0, target_size[0], 6], fill=accent)

            # --- name plate ---
            plate_box = [40, 30, 760, 150]
            draw_glass_panel(ui_layer, plate_box, radius=12, glow=(accent[0], accent[1], accent[2], 70))
            draw_corner_brackets(draw, plate_box, color=accent, length=18, width=2)
            neon_text(ui_layer, character_name.upper(), (66, 62), font_name, fill=TEXT_MAIN, glow=accent, anchor="lm")
            nickname = player_profile.get("nickname", "")
            if nickname:
                neon_text(ui_layer, f"@ {nickname}", (66, 102), font_nick, fill=TEXT_DIM, glow=accent, anchor="lm")

            # level segmented bar + friendship chip, inside the plate
            level_ratio = min(1.0, character_level / 90)
            draw.text((520, 62), f"LV. {character_level}/90", font=font_small, fill=TEXT_MAIN, anchor="lm")
            segmented_bar(ui_layer, [520, 78, 730, 88], level_ratio, color=accent, segments=18)
            draw.text((520, 108), f"Friendship {friendship_level}", font=font_tiny, fill=TEXT_DIM, anchor="lm")

            # --- weapon panel ---
            panel_x = 940
            weapon_box = [panel_x, 15, 1825, 185]
            draw_glass_panel(ui_layer, weapon_box, radius=12, glow=(accent[0], accent[1], accent[2], 60))
            draw_corner_brackets(draw, weapon_box, color=accent, length=16, width=2)

            weapon_stats = []
            if weapon_image:
                hex_cx, hex_cy, hex_r = panel_x + 85, 100, 70
                draw_hex_frame(ui_layer, hex_cx, hex_cy, hex_r, fill=(8, 12, 20, 210), outline=accent, width=3)
                paste_hex_masked(ui_layer, weapon_image, hex_cx, hex_cy, hex_r - 10)

                weapon_text_x = panel_x + 175
                neon_text(ui_layer, self._get_weapon_name(stats["weapon"]), (weapon_text_x, 48), font_weapon, fill=TEXT_MAIN, glow=accent, anchor="lm")
                refinement = stats["weapon"].get("refinement", 1)
                weapon_level = stats["weapon"].get("level", 1)
                max_level = "90" if stats["weapon"].get("rank", 0) == 5 else "80" if stats["weapon"].get("rank", 0) == 4 else "70"
                draw_diagonal_tag(ui_layer, (weapon_text_x, 78), 70, 30, GOLD, f"R{refinement}", font_tiny)
                draw.text((weapon_text_x + 85, 93), f"Lv.{weapon_level}/{max_level}", font=font_small, fill=TEXT_DIM, anchor="lm")

                try:
                    star_icon_path = f"assets/icons/stars/Star{stats['weapon'].get('rarity', 5)}.png"
                    star_image = Image.open(star_icon_path).convert("RGBA").resize((110, 30), Image.Resampling.LANCZOS)
                    ui_layer.paste(star_image, (weapon_text_x, 118), star_image)
                except Exception as error:
                    logger.warning("FuturisticCharacterCardGenerator: error loading star image: %s", error)

                weapon_stats = stats["weapon"].get("stats", [])

            wstat_x = panel_x + 620
            for index, stat in enumerate(weapon_stats[:2]):
                sx = wstat_x + index * 140
                chip_box = [sx, 60, sx + 125, 100]
                stat_row_icon_bg(ui_layer, chip_box, color=accent)
                stat_icon_path = W_STAT_ICONS.get(stat["prop"], "assets/icons/atk.png")
                try:
                    stat_icon = Image.open(stat_icon_path).convert("RGBA").resize((22, 22), Image.Resampling.LANCZOS)
                    ui_layer.paste(stat_icon, (sx + 8, 70), stat_icon)
                except Exception:
                    pass
                stat_value = f"{stat['val']}"
                if any(token in str(stat["prop"]) for token in ["PERCENT", "CHARGE", "CRITICAL"]):
                    stat_value += "%"
                draw.text((sx + 38, 80), stat_value, font=font_small, fill=TEXT_MAIN, anchor="lm")

            # --- main stats panel (two columns) ---
            stat_config = [
                ("Max HP", "hp", "{:.0f}", "assets/icons/hp.png"),
                ("ATK", "atk", "{:.0f}", "assets/icons/atk.png"),
                ("DEF", "def", "{:.0f}", "assets/icons/def.png"),
                ("CRIT Rate", "cr", "{:.1f}%", "assets/icons/cr.png"),
                ("CRIT DMG", "cd", "{:.1f}%", "assets/icons/cd.png"),
                ("Energy Recharge", "er", "{:.1f}%", "assets/icons/er.png"),
                (f"{stats['element']} DMG", "elem_bonus", "{:.1f}%", f"assets/icons/{stats['element'].lower()}.png"),
                ("Elemental Mastery", "em", "{:.0f}", "assets/icons/em.png"),
            ]
            stats_box = [panel_x, 200, 1825, 630]
            draw_glass_panel(ui_layer, stats_box, radius=12, glow=(accent[0], accent[1], accent[2], 45))
            draw_corner_brackets(draw, stats_box, color=accent, length=16, width=2)

            col_width = (stats_box[2] - stats_box[0] - 30) // 2
            row_height = 100
            for index, (label, key, fmt, icon_path) in enumerate(stat_config):
                col = index // 4
                row = index % 4
                row_x = stats_box[0] + 20 + col * (col_width + 10)
                row_y = stats_box[1] + 15 + row * row_height
                row_box = [row_x, row_y, row_x + col_width - 10, row_y + row_height - 12]
                draw.rounded_rectangle(row_box, radius=8, fill=(255, 255, 255, 10))
                draw.line([(row_box[0] + 8, row_box[3] - 4), (row_box[2] - 8, row_box[3] - 4)], fill=(accent[0], accent[1], accent[2], 60), width=1)
                try:
                    icon = Image.open(icon_path).convert("RGBA").resize((32, 32), Image.Resampling.LANCZOS)
                    ui_layer.paste(icon, (row_box[0] + 12, row_box[1] + 12), icon)
                except Exception:
                    pass
                draw.text((row_box[0] + 55, row_box[1] + 14), label.upper(), font=font_tiny, fill=TEXT_DIM, anchor="lm")
                value_text = fmt.format(stats.get(key, 0))
                neon_text(ui_layer, value_text, (row_box[2] - 12, row_box[1] + 44), font_value, fill=TEXT_MAIN, glow=accent, anchor="rm")

            await draw_horizontal_artifacts_futuristic(session, ui_layer, avatar_record, 155, 650, font_small, font_tiny, accent=accent)

            final_image = Image.alpha_composite(canvas, ui_layer)
            build_layer = Image.new("RGBA", target_size, (0, 0, 0, 0))
            draw_build_column_futuristic(build_layer, 640, build_data, talent_icons, constellation_icons, font_hex, accent=accent)
            final_image = Image.alpha_composite(final_image, build_layer)
            final_image = Image.alpha_composite(final_image, scanlines(target_size, spacing=4, alpha=10))
            apply_watermark(final_image)

            buffer = BytesIO()
            final_image.convert("RGB").save(buffer, format="JPEG", quality=95)
            buffer.seek(0)
            buffer.name = f"{char_id}_futuristic.jpg"
            return buffer