import asyncio
import logging
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger("genshin_userbot")

# Stat Mapping for icons (Local files)
STAT_MAP = {
    "FIGHT_PROP_HP": "hp", "FIGHT_PROP_HP_PERCENT": "hp",
    "FIGHT_PROP_ATTACK": "atk", "FIGHT_PROP_ATTACK_PERCENT": "atk",
    "FIGHT_PROP_DEFENSE": "def", "FIGHT_PROP_DEFENSE_PERCENT": "def",
    "FIGHT_PROP_ELEMENT_MASTERY": "em", "FIGHT_PROP_CHARGE_EFFICIENCY": "er",
    "FIGHT_PROP_CRITICAL": "cr", "FIGHT_PROP_CRITICAL_HURT": "cd",
    "FIGHT_PROP_FIRE_ADD_HURT": "pyro", "FIGHT_PROP_WATER_ADD_HURT": "hydro",
    "FIGHT_PROP_GRASS_ADD_HURT": "dendro", "FIGHT_PROP_ELEC_ADD_HURT": "electro",
    "FIGHT_PROP_ICE_ADD_HURT": "cryo", "FIGHT_PROP_WIND_ADD_HURT": "anemo",
    "FIGHT_PROP_ROCK_ADD_HURT": "geo", "FIGHT_PROP_PHYSICAL_ADD_HURT": "phys"
}

# Flower, Feather, Sands, Goblet, Circlet
EQUIP_ORDER = ["EQUIP_BRACER", "EQUIP_NECKLACE", "EQUIP_SHOES", "EQUIP_RING", "EQUIP_DRESS"]


def _compute_cv(main_data, sub_stats):
    """CV (crit value) = CR% * 2 + CD%, summed across the main stat (only
    relevant for circlets, whose main stat can itself be CR or CD) and all
    substats. Both crit stats stack additively across an artifact, so this
    is just a running total, not an average."""
    cr = 0.0
    cd = 0.0

    main_prop = main_data.get("mainPropId")
    if main_prop == "FIGHT_PROP_CRITICAL":
        cr += main_data.get("statValue", 0) or 0
    elif main_prop == "FIGHT_PROP_CRITICAL_HURT":
        cd += main_data.get("statValue", 0) or 0

    for stat in sub_stats:
        prop_id = stat.get("appendPropId")
        if prop_id == "FIGHT_PROP_CRITICAL":
            cr += stat.get("statValue", 0) or 0
        elif prop_id == "FIGHT_PROP_CRITICAL_HURT":
            cd += stat.get("statValue", 0) or 0

    return cr * 2 + cd


async def draw_artifact_card(session, base_image, x, y, art_data, font):
    draw = ImageDraw.Draw(base_image)
    CARD_W, CARD_H = 330, 200 
    ICONS_PATH = "assets/icons/"
    STARS_PATH = "assets/icons/stars/" 
    
    # 1. Background Card - Using a slightly more transparent fill for that glassmorphism look
    draw.rounded_rectangle([x, y, x + CARD_W, y + CARD_H], radius=12, fill=(0, 0, 0, 80), outline=(255, 255, 255, 40))

    flat = art_data.get("flat", {})
    relic_core = art_data.get("reliquary", {})
    main_data = flat.get("reliquaryMainstat", {})
    sub_stats = flat.get("reliquarySubstats", [])

    # 2. Artifact Piece Icon (The Gear piece)
    icon_name = flat.get("icon")
    # Live HoYoLAB data (character/detail fallback) gives a full,
    # directly-fetchable CDN URL - it's hash-named, so it can't be
    # reconstructed as an enka.network/ui/... path the way Enka's own
    # semantic icon names can.
    icon_url = flat.get("icon_url") or (f"https://enka.network/ui/{icon_name}.png" if icon_name else None)
    if icon_url:
        url = icon_url
        async with session.get(url) as response:
            if response.status == 200:
                img_data = await response.read()
                art_img = Image.open(BytesIO(img_data)).convert("RGBA").resize((140, 140), Image.Resampling.LANCZOS)
                # Icon background
                base_image.paste(art_img, (x + 15, y + 30), art_img)

    # 4. Rarity Stars (NEW)
    rarity = flat.get("rankLevel", 5)
    try:
        # 1. Open and convert
        star_img = Image.open(f"{STARS_PATH}Star{rarity}.png").convert("RGBA")
        
        # 2. Resize directly to the target size (Re-assigning the variable!)
        star_img = star_img.resize((98, 28), Image.Resampling.LANCZOS)
        
        # 3. Paste
        base_image.paste(star_img, (x + 30, y + 140), star_img)
    except Exception as e:
        logger.warning("Error loading star image Star%s.png: %s", rarity, e)
    # 3. Main Stat Icon - PASTE THIS LAST so it's on top
    main_prop = main_data.get("mainPropId")
    if main_prop:
        icon_key = STAT_MAP.get(main_prop, "atk").lower()
        try:
            # We move the main stat icon to the top right of the gear box
            m_icon = Image.open(f"{ICONS_PATH}{icon_key}.png").convert("RGBA").resize((28, 28))
            base_image.paste(m_icon, (x + 10, y + 10), m_icon)
        except Exception:
            pass

    # 4. Sub-Stat Grid
    grid_x = x + 150
    for i, stat in enumerate(sub_stats[:4]):
        col, row = i % 1, i // 1
        bx = grid_x + (col * 135)
        by = y + 15 + (row * 45)
        
        # Substat box
        draw.rounded_rectangle([bx, by, bx + 170, by + 38], radius=10, fill=(20, 20, 30, 150))
        
        prop_id = stat.get("appendPropId")
        icon_file = STAT_MAP.get(prop_id, "atk").lower()
        try:
            s_icon = Image.open(f"{ICONS_PATH}{icon_file}.png").convert("RGBA").resize((22, 22))
            base_image.paste(s_icon, (bx + 8, by + 8), s_icon)
        except Exception:
            pass
        
        val = stat.get("statValue")
        is_percent = any(k in prop_id for k in ["PERCENT", "CRITICAL", "EFFICIENCY", "HURT"])
        val_str = f"+{val}%" if is_percent else f"+{val}"
        draw.text((bx + 55, by + 19), val_str, font=font, fill=(255, 255, 255), anchor="lm")

    # 5. Main Stat Value - bottom left of the card (where the level used to sit).
    if main_prop:
        main_val = main_data.get("statValue")
        is_percent = any(k in main_prop for k in ["PERCENT", "CRITICAL", "EFFICIENCY", "HURT"])
        main_val_str = f"{main_val}%" if is_percent else f"{int(main_val)}"
        draw.text((x + 5, y + CARD_H - 15), main_val_str, font=font, fill=(255, 255, 255), anchor="lm", stroke_width=1, stroke_fill=(0, 0, 0))

    # 6. Enhancement level - bottom right of the card.
    raw_level = relic_core.get("level", 1)
    display_level = f"+{raw_level - 1}"
    draw.text((x + CARD_W - 185, y + CARD_H - 15), display_level, font=font, fill=(255, 204, 0), stroke_width=1, stroke_fill=(0, 0, 0), anchor="rm")

    # 7. CV (crit value) score - top right of the card. Drawn last, on top
    # of everything else (icon/stars/grid), so it's never painted over.
    cv_score = _compute_cv(main_data, sub_stats)
    cv_text = f"CV{cv_score:.1f}"
    draw.text((x + CARD_W - 185, y + 25), cv_text, font=font, fill=(255, 255, 255), stroke_width=1, stroke_fill=(0, 0, 0), anchor="rm")

async def draw_horizontal_artifacts(session, background, char_data, start_x, start_y, font):
    """
    Finds all artifacts for a single character and draws them in a 
    horizontal row (Flower -> Feather -> Sands -> Goblet -> Circlet).
    """
    # Filter only relics (artifacts)
    relics = [e for e in char_data.get("equipList", []) if "reliquary" in e]
    
    # Sort by EQUIP_ORDER
    sorted_relics = sorted(
        relics, 
        key=lambda x: EQUIP_ORDER.index(x.get("flat", {}).get("equipType", "")) 
        if x.get("flat", {}).get("equipType") in EQUIP_ORDER else 99
    )

    tasks = []
    spacing_x = 345

    for i, art in enumerate(sorted_relics[:5]):
        curr_x = start_x + (i * spacing_x)
        tasks.append(draw_artifact_card(session, background, curr_x, start_y, art, font))
    
    if tasks:
        await asyncio.gather(*tasks)