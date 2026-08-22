"""
artifacts_futuristic.py

Futuristic-HUD counterpart to artifacts.py's draw_horizontal_artifacts.
Same data contract (an Enka/HoYoLAB-shaped avatar record with equipList),
same five-artifact horizontal layout, but drawn with the glass-panel /
neon-accent language from futuristic_theme.py instead of the classic flat
cards.
"""
import asyncio
import logging
from io import BytesIO
from PIL import Image, ImageDraw

from cards.artifacts import STAT_MAP, EQUIP_ORDER
from cards.futuristic_theme import (
    ACCENT, TEXT_MAIN, TEXT_DIM, GOLD,
    draw_glass_panel, draw_corner_brackets, neon_text, stat_row_icon_bg,
)

logger = logging.getLogger("genshin_userbot")

CARD_W, CARD_H = 330, 210
ICONS_PATH = "assets/icons/"
STARS_PATH = "assets/icons/stars/"


async def _draw_artifact_card_futuristic(session, ui_layer, x, y, art_data, font_small, font_tiny, accent):
    draw = ImageDraw.Draw(ui_layer)
    box = [x, y, x + CARD_W, y + CARD_H]
    draw_glass_panel(ui_layer, box, radius=14, glow=(accent[0], accent[1], accent[2], 40))
    draw_corner_brackets(draw, box, color=accent, length=14, width=2)

    flat = art_data.get("flat", {})
    relic_core = art_data.get("reliquary", {})

    icon_name = flat.get("icon")
    icon_url = flat.get("icon_url") or (f"https://enka.network/ui/{icon_name}.png" if icon_name else None)
    if icon_url:
        try:
            async with session.get(icon_url, timeout=10) as response:
                if response.status == 200:
                    img_data = await response.read()
                    art_img = Image.open(BytesIO(img_data)).convert("RGBA").resize((130, 130), Image.Resampling.LANCZOS)
                    ui_layer.paste(art_img, (x + 15, y + 35), art_img)
        except Exception as error:
            logger.warning("artifacts_futuristic: failed to load artifact icon: %s", error)

    raw_level = relic_core.get("level", 1)
    display_level = f"+{raw_level - 1}"
    neon_text(ui_layer, display_level, (x + CARD_W - 14, y + 22), font_small, fill=GOLD, glow=GOLD, anchor="rm")

    rarity = flat.get("rankLevel", 5)
    try:
        star_img = Image.open(f"{STARS_PATH}Star{rarity}.png").convert("RGBA").resize((96, 26), Image.Resampling.LANCZOS)
        ui_layer.paste(star_img, (x + 30, y + 148), star_img)
    except Exception as error:
        logger.warning("artifacts_futuristic: error loading star image Star%s.png: %s", rarity, error)

    main_data = flat.get("reliquaryMainstat", {})
    main_prop = main_data.get("mainPropId")
    if main_prop:
        icon_key = STAT_MAP.get(main_prop, "atk").lower()
        try:
            m_icon = Image.open(f"{ICONS_PATH}{icon_key}.png").convert("RGBA").resize((26, 26), Image.Resampling.LANCZOS)
            ui_layer.paste(m_icon, (x + 12, y + 12), m_icon)
        except Exception:
            pass

        main_val = main_data.get("statValue")
        is_percent = any(token in main_prop for token in ["PERCENT", "CRITICAL", "EFFICIENCY", "HURT"])
        main_val_str = f"{main_val}%" if is_percent else f"{int(main_val)}"
        neon_text(ui_layer, main_val_str, (x + 80, y + 185), font_small, fill=TEXT_MAIN, glow=accent, anchor="mm")

    sub_stats = flat.get("reliquarySubstats", [])
    grid_x = x + 150
    for index, stat in enumerate(sub_stats[:4]):
        row_y = y + 18 + index * 45
        chip_box = [grid_x, row_y, grid_x + 168, row_y + 36]
        stat_row_icon_bg(ui_layer, chip_box, color=accent)

        prop_id = stat.get("appendPropId")
        icon_file = STAT_MAP.get(prop_id, "atk").lower()
        try:
            s_icon = Image.open(f"{ICONS_PATH}{icon_file}.png").convert("RGBA").resize((20, 20), Image.Resampling.LANCZOS)
            ui_layer.paste(s_icon, (grid_x + 8, row_y + 8), s_icon)
        except Exception:
            pass

        val = stat.get("statValue")
        is_percent = any(token in prop_id for token in ["PERCENT", "CRITICAL", "EFFICIENCY", "HURT"])
        val_str = f"+{val}%" if is_percent else f"+{val}"
        draw.text((grid_x + 42, row_y + 18), val_str, font=font_tiny, fill=TEXT_DIM, anchor="lm")


async def draw_horizontal_artifacts_futuristic(session, ui_layer, char_data, start_x, start_y, font_small, font_tiny, accent=ACCENT):
    """Finds all artifacts for a single character and draws them in a
    horizontal row (Flower -> Feather -> Sands -> Goblet -> Circlet),
    styled to match the futuristic HUD theme."""
    relics = [entry for entry in char_data.get("equipList", []) if "reliquary" in entry]
    sorted_relics = sorted(
        relics,
        key=lambda entry: EQUIP_ORDER.index(entry.get("flat", {}).get("equipType", ""))
        if entry.get("flat", {}).get("equipType") in EQUIP_ORDER else 99
    )

    spacing_x = 345
    tasks = [
        _draw_artifact_card_futuristic(session, ui_layer, start_x + index * spacing_x, start_y, art, font_small, font_tiny, accent)
        for index, art in enumerate(sorted_relics[:5])
    ]
    if tasks:
        await asyncio.gather(*tasks)