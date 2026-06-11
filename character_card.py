import asyncio
import aiohttp
import json
from io import BytesIO
from pathlib import Path
from PIL import Image, ImageDraw, ImageOps, ImageFont, ImageChops, ImageEnhance

from graph import get_complete_radar_module
from t_c import fetch_build_assets, draw_build_column
from artifacts import draw_horizontal_artifacts


W_STAT_ICONS = {
    "FIGHT_PROP_BASE_ATTACK": "asstests/icons/atk.png",
    "FIGHT_PROP_CHARGE_EFFICIENCY": "asstests/icons/er.png",
    "FIGHT_PROP_ELEMENT_MASTERY": "asstests/icons/em.png",
    "FIGHT_PROP_CRITICAL": "asstests/icons/cr.png",
    "FIGHT_PROP_CRITICAL_HURT": "asstests/icons/cd.png",
    "FIGHT_PROP_ATTACK_PERCENT": "asstests/icons/atk.png",
    "FIGHT_PROP_HP_PERCENT": "asstests/icons/hp.png",
    "FIGHT_PROP_DEFENSE_PERCENT": "asstests/icons/def.png"
}

# Load Data
with open("new.json", "r", encoding="utf-8") as f:
    TEXT = json.load(f)

with open("data.json", "r", encoding="utf-8") as f:
    NAMECARD_DATA = json.load(f)

with open("char.json", "r", encoding="utf-8") as f:
    CHAR_MAP = json.load(f)


# ---------------- HELPERS ----------------

def draw_text_with_shadow(draw, text, position, font_path, font_size,
                          text_color=(255, 255, 255, 255),
                          shadow_color=(0, 0, 0, 180),
                          anchor="mm",
                          shadow_offset=(2, 2)):
    font = ImageFont.truetype(font_path, font_size)
    x, y = position

    draw.text((x + shadow_offset[0], y + shadow_offset[1]),
              text, font=font, fill=shadow_color, anchor=anchor)

    draw.text(position, text, font=font, fill=text_color, anchor=anchor)


async def get_enkadata(uid):
    url = f"https://enka.network/api/uid/{uid}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as r:
            if r.status != 200:
                return {"nickname": "", "avatarInfoList": []}

            data = await r.json()
            player = data.get("playerInfo", {})

            return {
                "nickname": player.get("nickname", ""),
                "avatarInfoList": data.get("avatarInfoList", []),
                "playerInfo": player
            }


def get_prop(stats_dict, prop_id):
    return stats_dict.get(str(prop_id), stats_dict.get(int(prop_id), 0))


def extract_char_stats(avatar_list, char_id, element):
    element = element.capitalize()

    element_map = {
        "Pyro": 40, "Electro": 41, "Hydro": 42,
        "Dendro": 43, "Anemo": 45, "Geo": 44,
        "Cryo": 46, "Physical": 30
    }

    bonus_id = element_map.get(element)

    for char in avatar_list:
        if str(char.get("avatarId")) != str(char_id):
            continue

        p = char.get("fightPropMap", {})

        weapon_info = {}
        for item in char.get("equipList", []):
            if item.get("weapon"):
                w = item["weapon"]
                f = item.get("flat", {})

                weapon_info = {
                    "id": item.get("itemId"),
                    "level": w.get("level"),
                    "rarity": f.get("rankLevel"),
                    "icon": f.get("icon"),
                    "hash": f.get("nameTextMapHash"),
                    "refinement": list(w.get("affixMap", {0: 0}).values())[0] + 1,
                    "stats": [
                        {"prop": s.get("appendPropId"), "val": s.get("statValue")}
                        for s in f.get("weaponStats", [])
                    ]
                }
                break

        elem_bonus = (
            get_prop(p, bonus_id) +
            get_prop(p, 26) +
            get_prop(p, 27)
        ) * 100

        return {
            "char_level": char.get("propMap", {}).get("4001", {}).get("val", "1"),
            "friendship": char.get("fetterInfo", {}).get("expLevel", 1),
            "hp": get_prop(p, 2000),
            "atk": get_prop(p, 2001),
            "def": get_prop(p, 2002),
            "em": get_prop(p, 28),
            "cr": get_prop(p, 20) * 100,
            "cd": get_prop(p, 22) * 100,
            "er": get_prop(p, 23) * 100,
            "elem_bonus": elem_bonus,
            "element": element,
            "weapon": weapon_info
        }

    return None


# ---------------- 🔥 FIXED NAMECARD SYSTEM ----------------

def get_namecard_urls(enka_data):
    """
    FULL AUTO NAMECARD RESOLVER (NO MANUAL MAP)
    """
    player = enka_data.get("playerInfo", {})
    namecard_id = str(player.get("nameCardId", ""))

    if namecard_id in NAMECARD_DATA:
        icon = NAMECARD_DATA[namecard_id]["icon"]
        banner = icon.replace("NameCardPic", "NameCardBanner")

        return [
            f"https://enka.network/ui/{banner}.png",
            f"https://enka.network/ui/{icon}.png"
        ]

    return [
        "https://enka.network/ui/UI_NameCardBanner_0_P.png"
    ]


def get_splash_url(avatar_icon):
    base = avatar_icon.replace("UI_AvatarIcon_", "")
    return f"https://enka.network/ui/UI_Gacha_AvatarImg_{base}.png"


async def fetch_image(session, url):
    try:
        async with session.get(url) as r:
            if r.status != 200:
                return None
            return Image.open(BytesIO(await r.read())).convert("RGBA")
    except:
        return None


# ---------------- MAIN ----------------

async def compare_characters(uid, char_id):

    me = await get_enkadata(uid)

    me_data, t_icons, c_icons = await fetch_build_assets(uid, char_id)

    char_id_str = str(char_id)
    char_info_map = CHAR_MAP.get(char_id_str, {})

    stats = extract_char_stats(
        me["avatarInfoList"],
        char_id,
        char_info_map.get("element", "Anemo")
    )

    char_info = next(
        c for c in me["avatarInfoList"]
        if str(c.get("avatarId")) == str(char_id)
    )

    avatar_icon = char_info_map.get("avataricon", "UI_AvatarIcon_Qin")
    char_name = avatar_icon.replace("UI_AvatarIcon_", "")

    async with aiohttp.ClientSession() as session:

        # splash
        splash_img = await fetch_image(session, get_splash_url(avatar_icon))

        # 🔥 FIXED NAMECARD (IMPORTANT CHANGE)
        bg_urls = get_namecard_urls(me)

        bg_img = None
        for url in bg_urls:
            bg_img = await fetch_image(session, url)
            if bg_img:
                break

        if not bg_img:
            bg_img = Image.new("RGBA", (1875, 890), (30, 30, 45, 255))

        # weapon
        weapon_ic = stats["weapon"].get("icon")
        weapon_img = await fetch_image(
            session,
            f"https://enka.network/ui/{weapon_ic}.png"
        )

        # background
        bg = ImageOps.fit(bg_img, (1875, 890)).convert("RGBA")
        bg = ImageEnhance.Brightness(bg).enhance(0.45)

        ui = Image.new("RGBA", (1875, 890), (0, 0, 0, 0))
        draw = ImageDraw.Draw(ui)

        # splash
        if splash_img:
            ui.paste(splash_img, (0, 0), splash_img)

        # name
        draw_text_with_shadow(draw, char_name, (50, 50), "asstests/fonts/Genshin_Impact.ttf", 36)
        draw_text_with_shadow(draw, me["nickname"], (150, 50), "asstests/fonts/Genshin_Impact.ttf", 26)

        final = Image.alpha_composite(bg, ui)

        buf = BytesIO()
        final.convert("RGB").save(buf, "JPEG", quality=95)
        buf.seek(0)

        return buf