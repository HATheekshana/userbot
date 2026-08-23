import asyncio
import json
import logging
from io import BytesIO
from PIL import Image, ImageDraw, ImageOps, ImageFont

from cards.artifacts import EQUIP_ORDER
from services.net import new_session, is_dns_error

logger = logging.getLogger("genshin_userbot")

# HoYoLAB's own /character/detail endpoint (the one powering the
# "Character Details" page on the game/HoYoLAB) - this is the single
# source of truth used whenever a character isn't in Enka's public
# showcase (Enka only mirrors whatever a player pinned to their in-game
# showcase, so anything else - or a brand new character - just isn't
# there). Unlike genshin.py's parsed get_genshin_detailed_characters()
# model (which this project only used for .skills/.constellations before),
# calling this endpoint directly gets us everything the page shows: full
# stats (selected_properties), the equipped weapon, and all 5 artifacts
# with their main/substats - not just talent levels and constellations.
HOYOLAB_CHARACTER_DETAIL_URL = "https://sg-act-public-api.hoyolab.com/event/game_record/genshin/api/character/detail"

# Genshin's own FightProp enum - the property_type ids HoYoLAB's
# character/detail endpoint returns are these same numeric ids, which is
# also what Enka's fightPropMap/reliquary schema already keys off of (as
# the FIGHT_PROP_* string form for equip flat-stats). Translating through
# this table lets the raw HoYoLAB data drop straight into the existing
# Enka-shaped renderer (draw_horizontal_artifacts, the weapon/character
# stat rows in character_card.py) with no changes needed there.
PROP_ID_TO_FIGHT_PROP = {
    1: "FIGHT_PROP_BASE_HP", 2: "FIGHT_PROP_HP", 3: "FIGHT_PROP_HP_PERCENT",
    4: "FIGHT_PROP_BASE_ATTACK", 5: "FIGHT_PROP_ATTACK", 6: "FIGHT_PROP_ATTACK_PERCENT",
    7: "FIGHT_PROP_BASE_DEFENSE", 8: "FIGHT_PROP_DEFENSE", 9: "FIGHT_PROP_DEFENSE_PERCENT",
    20: "FIGHT_PROP_CRITICAL", 22: "FIGHT_PROP_CRITICAL_HURT", 23: "FIGHT_PROP_CHARGE_EFFICIENCY",
    26: "FIGHT_PROP_HEAL_ADD", 27: "FIGHT_PROP_HEALED_ADD", 28: "FIGHT_PROP_ELEMENT_MASTERY",
    30: "FIGHT_PROP_PHYSICAL_ADD_HURT",
    40: "FIGHT_PROP_FIRE_ADD_HURT", 41: "FIGHT_PROP_ELEC_ADD_HURT", 42: "FIGHT_PROP_WATER_ADD_HURT",
    43: "FIGHT_PROP_GRASS_ADD_HURT", 44: "FIGHT_PROP_WIND_ADD_HURT", 45: "FIGHT_PROP_ROCK_ADD_HURT",
    46: "FIGHT_PROP_ICE_ADD_HURT",
    2000: "FIGHT_PROP_MAX_HP", 2001: "FIGHT_PROP_CUR_ATTACK", 2002: "FIGHT_PROP_CUR_DEFENSE",
}


def _recognize_server(uid):
    """uid -> HoYoLAB server string (os_asia/os_usa/os_euro/os_cht). Tries
    genshin.py's own helper first since it's kept up to date with any
    server changes; falls back to the well-known uid-prefix convention
    this project already relies on being overseas-only (see abyss.py)."""
    try:
        import genshin
        return genshin.utility.recognize_genshin_server(str(uid))
    except Exception:
        pass
    return {
        "6": "os_usa",
        "7": "os_euro",
        "8": "os_asia",
        "9": "os_cht",
    }.get(str(uid)[0], "os_asia")


def _parse_fraction(value_str):
    """'63.5%' -> 0.635, '34183' -> 34183.0 - the 0-1 fraction scale
    Enka's own fightPropMap uses for percent stats (character_card.py
    multiplies these by 100 again when displaying CR/CD/ER/elem bonus)."""
    if not value_str:
        return 0.0
    text = str(value_str).strip()
    try:
        if text.endswith("%"):
            return float(text[:-1]) / 100
        return float(text.replace(",", ""))
    except ValueError:
        return 0.0


def _parse_percent_number(value_str):
    """'6.5%' -> 6.5, '39' -> 39.0 - the human-readable scale Enka's own
    flat.reliquaryMainstat/reliquarySubstats/weaponStats already use
    (NOT the 0-1 fraction fightPropMap uses - see _parse_fraction)."""
    if not value_str:
        return 0.0
    text = str(value_str).strip()
    if text.endswith("%"):
        text = text[:-1]
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return 0.0


async def fetch_hoyolab_character_detail(uid, char_id):
    """POSTs HoYoLAB's character/detail endpoint directly for one
    character and returns its raw dict (data['list'][0]), or None.

    This is the same request the official site/app makes for the
    "Character Details" page - {role_id, server, character_ids} - and is
    what carries the full stat/weapon/artifact breakdown, not just
    talents/constellations.
    """
    try:
        from services.abyss import _get_client  # local import: avoid a hard genshin.py/cookie dependency for callers that never hit this path
        client = _get_client()
    except Exception as error:
        logger.warning("fetch_hoyolab_character_detail: no HoYoLAB client available for uid=%s: %s", uid, error)
        return None

    payload = {"role_id": str(uid), "server": _recognize_server(uid), "character_ids": [int(char_id)]}
    # Force English names in the response. Without this header HoYoLAB
    # localizes the weapon/artifact/character text to whatever language the
    # authenticated cookie account is set to (e.g. a CN account returns
    # "绝弦" instead of "The Stringless"), which then renders verbatim on the
    # card. x-rpc-language is the same header the official site sends.
    lang_headers = {"x-rpc-language": "en-us"}
    try:
        try:
            response_data = await client.request(HOYOLAB_CHARACTER_DETAIL_URL, method="POST", data=payload, headers=lang_headers)
        except TypeError:
            # Some genshin.py versions take the POST body as `json=`
            # instead of `data=` - fall back rather than guessing wrong.
            response_data = await client.request(HOYOLAB_CHARACTER_DETAIL_URL, method="POST", json=payload, headers=lang_headers)
    except Exception as error:
        logger.warning("fetch_hoyolab_character_detail: request failed for uid=%s char_id=%s: %s", uid, char_id, error)
        return None

    char_list = (response_data or {}).get("list") or []
    return char_list[0] if char_list else None


def _build_fight_prop_map(selected_properties):
    fight_props = {}
    for prop in selected_properties or []:
        key = PROP_ID_TO_FIGHT_PROP.get(prop.get("property_type"))
        if key:
            fight_props[str(prop.get("property_type"))] = _parse_fraction(prop.get("final"))
    return fight_props


def _relic_to_enka_equip(relic):
    pos = relic.get("pos", 0)
    equip_type = EQUIP_ORDER[pos - 1] if 1 <= pos <= len(EQUIP_ORDER) else "EQUIP_BRACER"
    main = relic.get("main_property", {}) or {}
    subs = []
    for sub in relic.get("sub_property_list", []) or []:
        key = PROP_ID_TO_FIGHT_PROP.get(sub.get("property_type"))
        if key:
            subs.append({"appendPropId": key, "statValue": _parse_percent_number(sub.get("value"))})
    return {
        "reliquary": {"level": (relic.get("level") or 0) + 1},
        "flat": {
            "icon": None,
            "icon_url": relic.get("icon"),
            "equipType": equip_type,
            "rankLevel": relic.get("rarity", 5),
            "reliquaryMainstat": {
                "mainPropId": PROP_ID_TO_FIGHT_PROP.get(main.get("property_type")),
                "statValue": _parse_percent_number(main.get("value")),
            },
            "reliquarySubstats": subs,
        },
    }


def hoyolab_character_detail_to_avatar_record(char_data):
    """Converts one raw character/detail entry into an Enka-schema
    avatarInfoList record (propMap/fetterInfo/fightPropMap/equipList), so
    it can be dropped straight into the existing Enka-based rendering
    pipeline (generate_card / draw_horizontal_artifacts) unchanged."""
    base = char_data.get("base", {}) or {}
    weapon_data = char_data.get("weapon", {}) or {}

    equip_list = []
    if weapon_data:
        weapon_sub = weapon_data.get("sub_property", {}) or {}
        weapon_stats = []
        sub_key = PROP_ID_TO_FIGHT_PROP.get(weapon_sub.get("property_type"))
        if sub_key:
            weapon_stats.append({"appendPropId": sub_key, "statValue": _parse_percent_number(weapon_sub.get("final"))})
        equip_list.append({
            "itemId": weapon_data.get("id"),
            "weapon": {
                "level": weapon_data.get("level", 1),
                "affixMap": {"0": max((weapon_data.get("affix_level") or 1) - 1, 0)},
            },
            "flat": {
                "icon": None,
                "icon_url": weapon_data.get("icon"),
                "rankLevel": weapon_data.get("rarity", 5),
                "weaponStats": weapon_stats,
                "weaponName": weapon_data.get("name"),
            },
        })

    for relic in char_data.get("relics", []) or []:
        equip_list.append(_relic_to_enka_equip(relic))

    # `skills` mixes the 3 real combat talents (Normal Attack, Elemental
    # Skill, Elemental Burst - skill_type 1) with passive talents
    # (skill_type 2). Only the 3 combat talents have a slot in the card, so
    # filter to skill_type 1, same as the live-build path does. Enka's
    # showcase records carry this as skillLevelMap/talentIdList; a record
    # merged from character/detail has neither, so stash the levels and the
    # active-constellation count here for _extract_character_build_data to
    # use instead of defaulting every talent to level 1.
    skills = char_data.get("skills", []) or []
    combat_skills = [skill for skill in skills if skill.get("skill_type") == 1][:3]
    constellations = char_data.get("constellations", []) or []

    return {
        "avatarId": base.get("id"),
        "propMap": {"4001": {"val": base.get("level", 1)}},
        "fetterInfo": {"expLevel": base.get("fetter", 1)},
        "fightPropMap": _build_fight_prop_map(char_data.get("selected_properties")),
        "equipList": equip_list,
        "_hoyolab_build": {
            "talents": [skill.get("level", 1) for skill in combat_skills],
            "cons_count": sum(1 for c in constellations if c.get("is_actived")),
        },
    }


class EnkaClient:
    API_BASE_URL = "https://enka.network/api"

    async def fetch_avatar_data(self, uid):
        try:
            async with new_session() as session:
                async with session.get(f"{self.API_BASE_URL}/uid/{uid}") as response:
                    if response.status != 200:
                        logger.info("EnkaClient: unexpected status %s for uid=%s", response.status, uid)
                        return None

                    data = await response.json()
                    player_info = data.get("playerInfo", {})
                    avatar_list = data.get("avatarInfoList", [])
                    return {
                        "nickname": player_info.get("nickname", ""),
                        "avatarInfoList": avatar_list,
                        "showAvatarInfoList": player_info.get("showAvatarInfoList", []),
                    }
        except Exception as error:
            if is_dns_error(error):
                logger.warning("EnkaClient: DNS resolution failed for uid=%s (%s)", uid, error)
            else:
                logger.warning("EnkaClient: request failed for uid=%s: %s", uid, error)
            return None


class CharacterBuildFetcher:
    def __init__(self, enka_client=None):
        self.enka_client = enka_client or EnkaClient()

    async def _fetch_avatar_data(self, uid):
        return await self.enka_client.fetch_avatar_data(uid)

    def _extract_character_build_data(self, avatar_list, char_id, avatars_db):
        for avatar_entry in avatar_list:
            if str(avatar_entry.get("avatarId")) != str(char_id):
                continue

            metadata = avatars_db.get(str(char_id), {})
            skill_order = metadata.get("SkillOrder", []) or []
            proud_map = metadata.get("ProudMap", {}) or {}
            skill_base = avatar_entry.get("skillLevelMap") or {}
            skill_extra = avatar_entry.get("proudSkillExtraLevelMap") or {}
            talent_ids = avatar_entry.get("talentIdList") or []
            hoyolab_build = avatar_entry.get("_hoyolab_build") or {}

            if not skill_order:
                logger.warning("CharacterBuildFetcher: missing SkillOrder metadata for char_id=%s", char_id)
                return None

            if skill_base:
                # Enka showcase record: real per-skill levels (+ any
                # ascension bonus) and constellation count.
                skill_levels = []
                for skill_id in skill_order:
                    level = skill_base.get(str(skill_id), 1)
                    if proud_map:
                        extra_key = proud_map.get(str(skill_id))
                        if extra_key is not None:
                            level += skill_extra.get(str(extra_key), 0)
                    skill_levels.append(level)
                cons_count = len(talent_ids)
            elif hoyolab_build:
                # Record merged from HoYoLAB character/detail (character not
                # in the player's Enka showcase): no skillLevelMap /
                # talentIdList, so use the talent levels and active-
                # constellation count captured at merge time. Icons still
                # come from avatars.json below, matching the showcase path.
                hoyolab_talents = hoyolab_build.get("talents") or []
                skill_levels = [
                    hoyolab_talents[index] if index < len(hoyolab_talents) else 1
                    for index in range(len(skill_order))
                ]
                cons_count = hoyolab_build.get("cons_count", 0)
            else:
                # Neither Enka nor HoYoLAB skill data is present - fall back
                # to the live build path rather than rendering every talent
                # as level 1 and every constellation as locked.
                logger.info("CharacterBuildFetcher: no skill data (Enka/HoYoLAB) for char_id=%s", char_id)
                return None

            return {
                "talents": skill_levels,
                "cons_count": cons_count,
                "cons_icons": metadata.get("Consts", []) or [],
                "skill_icons": [metadata.get("Skills", {}).get(str(skill_id)) for skill_id in skill_order],
            }
        return None

    async def _load_avatars_database(self):
        with open("data/avatars.json", "r", encoding="utf-8") as handle:
            return json.load(handle)

    async def fetch_build_assets(self, uid, char_id, avatar_data=None):
        avatars_db = await self._load_avatars_database()
        # A caller (e.g. CharacterCardGenerator) may have already fetched
        # this uid's profile this request - reuse it instead of hitting
        # Enka a second time for the same data.
        if avatar_data is None:
            avatar_data = await self._fetch_avatar_data(uid)
        if not avatar_data:
            logger.info("CharacterBuildFetcher: no avatar data for uid=%s", uid)
            return None, None, None

        avatar_list = avatar_data.get("avatarInfoList", [])
        logger.info("CharacterBuildFetcher: avatars=%d for uid=%s", len(avatar_list), uid)
        build_data = self._extract_character_build_data(avatar_list, char_id, avatars_db)

        if build_data:
            async with new_session() as session:
                talent_icons = await asyncio.gather(*[self._fetch_ui_image(session, icon) for icon in build_data["skill_icons"]])
                constellation_icons = await asyncio.gather(*[self._fetch_ui_image(session, icon) for icon in build_data["cons_icons"]])
            return build_data, talent_icons, constellation_icons

        # Enka route failed - either avatars.json doesn't know this
        # char_id yet (new patch character), or Enka's own showcase data
        # for this uid doesn't have it. Either way, fall back to live
        # HoYoLAB data for this exact uid/character instead of giving up.
        logger.info(
            "CharacterBuildFetcher: no build data via Enka/avatars.json for char_id=%s uid=%s, trying live HoYoLAB fallback",
            char_id, uid,
        )
        return await self._fetch_live_build_assets(uid, char_id)

    async def _fetch_live_build_assets(self, uid, char_id):
        """Same three-tuple contract as the Enka path above (build_data,
        talent_icons, constellation_icons), sourced straight from
        HoYoLAB's character/detail endpoint instead of avatars.json +
        Enka. Skill/constellation icons here are already full CDN URLs,
        so they're fetched directly rather than run through Enka's /ui/
        path.
        """
        char_data = await fetch_hoyolab_character_detail(uid, char_id)
        if not char_data or not char_data.get("skills"):
            logger.info("CharacterBuildFetcher: uid=%s has no live build data for char_id=%s either", uid, char_id)
            return None, None, None

        # `skills` mixes the 3 real combat talents (Normal Attack,
        # Elemental Skill, Elemental Burst - skill_type 1) with every
        # passive talent (1st/4th Ascension, utility passives - skill_type
        # 2) in one list. The card layout (draw_build_column) only has
        # room for the 3 combat talents, same as avatars.json's
        # SkillOrder, so filter to skill_type 1 rather than passing every
        # entry through.
        combat_skills = [skill for skill in char_data["skills"] if skill.get("skill_type") == 1][:3]
        constellations = char_data.get("constellations", []) or []

        build_data = {
            "talents": [skill.get("level", 1) for skill in combat_skills],
            "cons_count": sum(1 for c in constellations if c.get("is_actived")),
            "cons_icons": [c.get("icon") for c in constellations],
            "skill_icons": [skill.get("icon") for skill in combat_skills],
        }

        async with new_session() as session:
            talent_icons = await asyncio.gather(*[self._fetch_image_url(session, icon) for icon in build_data["skill_icons"]])
            constellation_icons = await asyncio.gather(*[self._fetch_image_url(session, icon) for icon in build_data["cons_icons"]])

        return build_data, talent_icons, constellation_icons

    async def _fetch_image_url(self, session, url):
        """Like _fetch_ui_image, but for URLs that are already complete
        (HoYoLAB's CDN) rather than bare Enka icon names."""
        if not url:
            return None
        try:
            async with session.get(url, timeout=10) as response:
                if response.status != 200:
                    return None
                return Image.open(BytesIO(await response.read())).convert("RGBA")
        except Exception:
            return None

    async def _fetch_ui_image(self, session, icon_path):
        if not icon_path:
            return None
        url = f"https://enka.network/ui/{icon_path.replace('/ui/', '')}"
        try:
            async with session.get(url, timeout=10) as response:
                if response.status != 200:
                    return None
                return Image.open(BytesIO(await response.read())).convert("RGBA")
        except Exception:
            return None


def draw_build_column(canvas, start_x, data, talent_icons, constellation_icons):
    draw = ImageDraw.Draw(canvas)
    font_path = "assets/fonts/Genshin_Impact.ttf"
    skill_font = ImageFont.truetype(font_path, 18)

    entry_bg = Image.open("assets/talents/bg.png").convert("RGBA")
    ten_bg = Image.open("assets/talents/10.png").convert("RGBA")
    con_bg = Image.open("assets/constant/const_adapt.png").convert("RGBA")
    lock_bg = Image.open("assets/constant/closed/CLOSED.png").convert("RGBA")
    mask = Image.open("assets/constant/maska_constant.png").convert("L")

    talent_x = start_x + 30
    talent_y_base = 330
    for index, icon in enumerate(talent_icons):
        if not icon:
            continue
        y = talent_y_base + index * 105
        level = data["talents"][index] if index < len(data["talents"]) else 1
        draw.ellipse([talent_x + 10, y + 10, talent_x + 80, y + 80], fill=(0, 0, 0, 180))
        frame = ten_bg if level >= 10 else entry_bg
        canvas.paste(frame.resize((90, 90), Image.Resampling.LANCZOS), (talent_x, y), frame.resize((90, 90), Image.Resampling.LANCZOS))
        icon_resized = icon.resize((60, 60), Image.Resampling.LANCZOS)
        canvas.paste(icon_resized, (talent_x + 15, y + 15), icon_resized)
        bubble_color = (255, 215, 0) if level >= 10 else (255, 255, 255)
        _draw_circle_bubble(draw, f"{level}", (talent_x + 45, y + 85), skill_font, text_color=bubble_color)

    const_x = start_x - 600
    const_y_base = 250
    for index, icon in enumerate(constellation_icons):
        if not icon:
            continue
        y = const_y_base + index * 95
        is_locked = index >= data["cons_count"]
        draw.ellipse([const_x + 5, y + 5, const_x + 65, y + 65], fill=(0, 0, 0, 180))
        icon_resized = icon.resize((60, 60), Image.Resampling.LANCZOS)
        if is_locked:
            lock_frame = lock_bg.resize((70, 70), Image.Resampling.LANCZOS)
            gray_icon = icon_resized.convert("L").convert("RGBA")
            canvas.paste(gray_icon, (const_x + 5, y + 5), mask.resize((60, 60), Image.Resampling.LANCZOS))
            canvas.paste(lock_frame, (const_x, y), lock_frame)
        else:
            con_frame = con_bg.resize((70, 70), Image.Resampling.LANCZOS)
            canvas.paste(con_frame, (const_x, y), con_frame)
            canvas.paste(icon_resized, (const_x + 5, y + 5), mask.resize((60, 60), Image.Resampling.LANCZOS))


def _draw_circle_bubble(draw, text, position, font, padding=10, text_color=(255, 255, 255, 255), anchor="mm"):
    bbox = draw.textbbox(position, text, font=font, anchor=anchor)
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    diameter = max(width, height) + padding * 2
    left = position[0] - diameter // 2
    top = position[1] - diameter // 2
    right = position[0] + diameter // 2
    bottom = position[1] + diameter // 2
    draw.ellipse([left, top, right, bottom], fill=(20, 20, 30, 200), outline=(255, 255, 255, 150), width=1)
    draw.text(position, text, font=font, fill=text_color, anchor=anchor)