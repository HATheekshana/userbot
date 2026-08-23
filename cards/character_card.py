import json
import logging
import os
from io import BytesIO
from pathlib import Path
from PIL import Image, ImageDraw, ImageOps, ImageChops, ImageEnhance, ImageFont

from cards.hoyolab_character_detail import (
    CharacterBuildFetcher,
    EnkaClient,
    draw_build_column,
    fetch_hoyolab_character_detail,
    hoyolab_character_detail_to_avatar_record,
)
from cards.artifacts import draw_horizontal_artifacts
from cards.watermark import apply_watermark
from services.net import new_session

logger = logging.getLogger("genshin_userbot")

# genshin.py element names are already "Anemo"/"Cryo"/etc, same as char.json,
# so no translation table is needed there - just .capitalize() defensively.


def _icon_name_from_url(icon_url):
    """HoYoLAB's `icon` field is a full CDN URL like
    '.../UI_AvatarIcon_Odette.png'. char.json/character_card.py only ever
    want the bare 'UI_AvatarIcon_Odette' part (they build their own
    enka.network URLs from it), so strip host + extension.
    """
    if not icon_url:
        return None
    name = icon_url.rsplit("/", 1)[-1]
    if "." in name:
        name = name.rsplit(".", 1)[0]
    return name

W_STAT_ICONS = {
    "FIGHT_PROP_BASE_ATTACK": "assets/icons/atk.png",
    "FIGHT_PROP_CHARGE_EFFICIENCY": "assets/icons/er.png",
    "FIGHT_PROP_ELEMENT_MASTERY": "assets/icons/em.png",
    "FIGHT_PROP_CRITICAL": "assets/icons/cr.png",
    "FIGHT_PROP_CRITICAL_HURT": "assets/icons/cd.png",
    "FIGHT_PROP_ATTACK_PERCENT": "assets/icons/atk.png",
    "FIGHT_PROP_HP_PERCENT": "assets/icons/hp.png",
    "FIGHT_PROP_DEFENSE_PERCENT": "assets/icons/def.png",
}

# Character-name quirks between char.json's icon names and enka.network's
# namecard icon names. Used by _get_namecard_urls() below.
NAMECARD_NAME_OVERRIDES = {
    "Ambor": "Amber",
    "yae": "yae1",
    "Yae Miko": "yae1",
    "Miko": "yae1",
    "Noel": "Noelle",
    "Feiyan": "Yanfei",
    "Tohma": "Thoma",
    "Heizo": "Heizou",
    "Liney": "Lyney",
    "Liuyun": "Xianyun",
}

# Direct, hardcoded namecard URLs for characters not yet present in
# data.json (e.g. brand-new patch characters like Odette/Alyosha whose
# namecard entries haven't been indexed there yet). Checked first in
# _get_namecard_urls() before falling back to the data.json search.
NAMECARD_URL_HARDCODES = {
    "Odette": [
        "https://enka.network/ui/UI_NameCardPic_Odette_P.png",
    ],
    "Alyosha": [
        "https://enka.network/ui/UI_NameCardPic_Alyosha_P.png",
    ],
}


def draw_text_with_shadow(draw, text, position, font_path, font_size, text_color=(255, 255, 255, 255), shadow_color=(0, 0, 0, 180), anchor="mm", shadow_offset=(2, 2)):
    font = ImageFont.truetype(font_path, font_size)
    shadow_position = (position[0] + shadow_offset[0], position[1] + shadow_offset[1])
    draw.text(shadow_position, text, font=font, fill=shadow_color, anchor=anchor)
    draw.text(position, text, font=font, fill=text_color, anchor=anchor)


def paste_splash_left(ui_layer, splash_image, size, left_align=False):
    """Paste a splash image into the left side of the card.

    left_align controls sizing/positioning:
      - False (default): resize to full card height, then crop a
        760px-wide slice from the horizontal *center*, with a 160px
        fade-out on the right edge. This matches enka.network's official
        gacha splash art, which is wide and keeps the character centered.
      - True: custom user-supplied splash (from custom_splash/). Resized
        directly to a fixed 730x890, pasted flush at x=0, with a 50px
        fade-out on the right edge to blend into the background. Custom
        splashes aren't framed the same way as the official art, so
        centering/cropping them shifts the subject to the right of where
        it should be - this keeps them flush at x=0 as expected.
    """
    card_width, card_height = size

    if left_align:
        left_width, fade_width = 730, 150
        # Cover-crop (not stretch) to left_width x card_height so the
        # custom image's aspect ratio is preserved - scale up until it
        # fills the box, then crop the overflow off centered.
        splash_image = ImageOps.fit(splash_image, (left_width, card_height), method=Image.Resampling.LANCZOS)
    else:
        left_width, fade_width = 760, 160
        scale = card_height / splash_image.height
        splash_image = splash_image.resize((int(splash_image.width * scale), card_height), Image.Resampling.LANCZOS)
        crop_x0 = (splash_image.width - left_width) // 2
        splash_image = splash_image.crop((crop_x0, 0, crop_x0 + left_width, card_height))

    mask = Image.new("L", (left_width, card_height), 255)
    draw = ImageDraw.Draw(mask)
    for index in range(fade_width):
        draw.line([(left_width - fade_width + index, 0), (left_width - fade_width + index, card_height)], fill=int(255 * (1 - index / fade_width)))

    splash_image.putalpha(ImageChops.multiply(splash_image.getchannel("A"), mask))
    ui_layer.paste(splash_image, (0, 0), splash_image)
    return ui_layer


class PlayerDataProvider:
    def __init__(self, enka_client=None):
        self.enka_client = enka_client or EnkaClient()

    async def fetch_player_profile(self, uid):
        return await self.enka_client.fetch_avatar_data(uid)


class CharacterCardGenerator:
    def __init__(self, char_map_path="data/char.json", namecard_path="data/data.json", text_map_path="data/new.json", splash_directory="data/custom_splash", font_path="assets/fonts/Genshin_Impact.ttf"):
        self.char_map_path = char_map_path
        self.char_map = self._load_json(char_map_path)
        self.namecard_data = self._load_json(namecard_path)
        self.text_map = self._load_json(text_map_path)
        self.splash_directory = Path(splash_directory)
        self.font_path = font_path
        self.player_data_provider = PlayerDataProvider()
        self.build_fetcher = CharacterBuildFetcher()

    @staticmethod
    def _load_json(file_path):
        with open(file_path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    async def _fetch_live_detailed_characters(self, uid):
        """Shared HoYoLAB fetch used by both ID-based (_lookup_character_info)
        and name-based (resolve_by_name) live lookups, so there's exactly
        one place that knows how to call genshin.py / handle its errors."""
        from services.abyss import _get_client  # local import: avoid a hard genshin.py/cookie dependency for callers that never hit this path
        client = _get_client()
        return await client.get_genshin_detailed_characters(int(uid))

    @staticmethod
    def _entry_from_live_character(match):
        icon_name = _icon_name_from_url(match.icon) or "UI_AvatarIcon_Qin"
        return {
            "name": match.name,
            "avataricon": icon_name,
            "rarity": match.rarity,
            "element": (match.element or "Anemo").capitalize(),
        }

    async def resolve_by_name(self, name, uid):
        """Case-insensitive live HoYoLAB lookup by character name (used when
        a name doesn't match anything in char.json - e.g. bot.py's !show
        resolving a brand-new patch character before char.json knows the
        ID at all). Returns (char_id, entry) on success, or None.

        Callers (bot.py) should merge the returned entry into their own
        name->id caches - this only updates *this* object's char_map/disk
        copy, since bot.py keeps a separate in-memory char_map/name_to_id
        built at startup.
        """
        name_lower = name.strip().lower()
        try:
            detailed = await self._fetch_live_detailed_characters(uid)
        except Exception as error:
            logger.warning("CharacterCardGenerator: live HoYoLAB name lookup failed for uid=%s: %s", uid, error)
            return None

        match = next(
            (c for c in detailed.characters if c.name.strip().lower() == name_lower),
            None,
        ) or next(
            (c for c in detailed.characters if name_lower in c.name.strip().lower()),
            None,
        )
        if not match:
            return None

        entry = self._entry_from_live_character(match)
        self.char_map[str(match.id)] = entry
        self._persist_char_map()
        logger.info("CharacterCardGenerator: resolved '%s' -> char_id=%s (%s) via live HoYoLAB name lookup", name, match.id, entry["name"])
        return match.id, entry

    async def _lookup_character_info(self, char_id, uid):
        """Look up char_id in the local char.json cache. If it's missing -
        almost always because it's a character newer than our last
        char.json refresh (new patch, e.g. Odette/Alyosha in 7.0) - fetch
        it live from HoYoLAB's own Character API instead of silently
        falling back to a wrong placeholder (the old behaviour returned
        Jean's icon/Anemo for *any* unknown ID, which renders a
        confidently wrong card instead of an obvious error).

        This is official, always-current data straight from HoYoverse -
        no third-party repo (Enka's store, nanoka.cc, etc.) to fall behind
        on. The catch: it only knows about characters uid's own account
        actually has and has made visible in Battle Chronicle, so it can
        still fail for a uid that hasn't unlocked/shown that character.
        """
        cached = self.char_map.get(str(char_id))
        if cached:
            return cached

        logger.info(
            "CharacterCardGenerator: char_id=%s not in char.json, trying live HoYoLAB lookup for uid=%s",
            char_id, uid,
        )
        try:
            detailed = await self._fetch_live_detailed_characters(uid)
        except Exception as error:
            logger.warning("CharacterCardGenerator: live HoYoLAB lookup failed for uid=%s: %s", uid, error)
            raise RuntimeError(
                f"Character {char_id} isn't in char.json yet and the live HoYoLAB lookup for "
                f"uid={uid} failed ({error}). It may be a very new character HoYoLAB hasn't "
                f"indexed for this account yet, or LTUID_V2/LTOKEN_V2 aren't configured."
            ) from error

        match = next((c for c in detailed.characters if str(c.id) == str(char_id)), None)
        if not match:
            raise RuntimeError(
                f"Character {char_id} isn't in char.json, and uid={uid}'s HoYoLAB Battle Chronicle "
                f"doesn't show it either (not unlocked, or Battle Chronicle privacy is set to private)."
            )

        entry = self._entry_from_live_character(match)

        self.char_map[str(char_id)] = entry
        self._persist_char_map()
        logger.info("CharacterCardGenerator: learned char_id=%s (%s) from live HoYoLAB lookup, saved to char.json", char_id, entry["name"])
        return entry

    def _persist_char_map(self):
        """Write self.char_map back to char.json so a live-fetched
        character only needs to be fetched once, not on every card."""
        try:
            with open(self.char_map_path, "w", encoding="utf-8") as handle:
                json.dump(self.char_map, handle, ensure_ascii=False, indent=2)
        except Exception as error:
            # Not fatal - the card can still render this once from memory,
            # it'll just have to hit the live lookup again next time.
            logger.warning("CharacterCardGenerator: failed to persist char.json: %s", error)

    def _get_namecard_urls(self, avatar_icon):
        base_name = avatar_icon.replace("UI_AvatarIcon_", "")

        # Hardcoded overrides for characters not yet indexed in data.json
        # (e.g. brand-new patch characters). Checked before the normal
        # data.json search so these always resolve correctly.
        hardcoded = NAMECARD_URL_HARDCODES.get(base_name)
        if hardcoded:
            return hardcoded

        search_name = NAMECARD_NAME_OVERRIDES.get(base_name, base_name)
        for _, info in self.namecard_data.items():
            icon_name = info.get("icon", "")
            if f"_{search_name}_" in icon_name:
                banner_url = icon_name.replace("NameCardPic", "NameCardBanner")
                return [f"https://enka.network/ui/{banner_url}.png", f"https://enka.network/ui/{icon_name}.png"]
        return ["https://enka.network/ui/UI_NameCardPic_Yae1_P.png"]

    @staticmethod
    def _get_splash_url(avatar_icon):
        base_name = avatar_icon.replace("UI_AvatarIcon_", "")
        return f"https://enka.network/ui/UI_Gacha_AvatarImg_{base_name}.png"

    def _get_weapon_name(self, weapon_info):
        # Live HoYoLAB lookups (character/detail) give us the weapon's
        # name directly - no text-map hash lookup needed or possible,
        # since that endpoint's icon URLs are hash-named, not the
        # semantic Enka names text_map.json is keyed by.
        direct_name = weapon_info.get("weaponName")
        if direct_name:
            return direct_name
        name_hash = str(weapon_info.get("hash", ""))
        return self.text_map.get(name_hash, f"Weapon {weapon_info.get('id')}")

    def _find_avatar_record(self, avatar_list, char_id):
        return next((entry for entry in avatar_list if str(entry.get("avatarId")) == str(char_id)), None)

    async def _ensure_character_record(self, uid, char_id, player_profile):
        """Merge a missing character into the Enka profile using HoYoLAB's
        own character/detail endpoint, but only when the Enka avatar list
        doesn't already contain this avatarId. This keeps the normal fast
        path on Enka and only falls back to a live, authenticated HoYoLAB
        lookup when needed - and unlike the old roster-only fallback, this
        merges the *full* record (stats/weapon/artifacts), not an empty
        skeleton, so the card can actually render them."""
        avatar_list = player_profile.setdefault("avatarInfoList", [])
        if self._find_avatar_record(avatar_list, char_id):
            return True

        try:
            char_data = await fetch_hoyolab_character_detail(uid, char_id)
        except Exception as error:
            logger.warning("CharacterCardGenerator: HoYoLAB fallback failed for uid=%s char_id=%s: %s", uid, char_id, error)
            return False

        if not char_data:
            return False

        merged_entry = hoyolab_character_detail_to_avatar_record(char_data)
        if not merged_entry.get("avatarId"):
            merged_entry["avatarId"] = int(char_id)
        avatar_list.append(merged_entry)
        logger.info("CharacterCardGenerator: merged char_id=%s into uid=%s from live HoYoLAB character/detail", char_id, uid)
        return True

    def _extract_character_stats(self, avatar_list, char_id, element):
        element = element.capitalize()
        element_map = {
            "Pyro": 40,
            "Electro": 41,
            "Hydro": 42,
            "Dendro": 43,
            "Anemo": 44,
            "Geo": 45,
            "Cryo": 46,
            "Physical": 30,
        }
        bonus_id = element_map.get(element)

        for avatar_entry in avatar_list:
            if str(avatar_entry.get("avatarId")) != str(char_id):
                continue
            fight_props = avatar_entry.get("fightPropMap", {})
            equips = avatar_entry.get("equipList", [])
            weapon_info = {}
            for item in equips:
                if not item.get("weapon"):
                    continue
                flat_data = item.get("flat", {})
                weapon_data = item.get("weapon")
                weapon_info = {
                    "id": item.get("itemId"),
                    "level": weapon_data.get("level"),
                    "rarity": flat_data.get("rankLevel"),
                    "icon": flat_data.get("icon"),
                    "icon_url": flat_data.get("icon_url"),
                    "hash": flat_data.get("nameTextMapHash"),
                    "weaponName": flat_data.get("weaponName"),
                    "refinement": list(weapon_data.get("affixMap", {0: 0}).values())[0] + 1,
                    "stats": [
                        {"prop": stat.get("appendPropId"), "val": stat.get("statValue")}
                        for stat in flat_data.get("weaponStats", [])
                    ],
                    "rank": flat_data.get("rankLevel", 5),
                }
                break

            elem_bonus = (self._get_prop(fight_props, bonus_id) + self._get_prop(fight_props, 26) + self._get_prop(fight_props, 27)) * 100
            return {
                "char_level": avatar_entry.get("propMap", {}).get("4001", {}).get("val", 1),
                "friendship": avatar_entry.get("fetterInfo", {}).get("expLevel", 1),
                "hp": self._get_prop(fight_props, 2000),
                "atk": self._get_prop(fight_props, 2001),
                "def": self._get_prop(fight_props, 2002),
                "em": self._get_prop(fight_props, 28),
                "cr": self._get_prop(fight_props, 20) * 100,
                "cd": self._get_prop(fight_props, 22) * 100,
                "er": self._get_prop(fight_props, 23) * 100,
                "elem_bonus": elem_bonus,
                "element": element,
                "weapon": weapon_info,
            }
        return None

    @staticmethod
    def _get_prop(stats_dict, prop_id):
        if prop_id is None:
            return 0
        return stats_dict.get(str(prop_id), stats_dict.get(int(prop_id), 0))

    async def _load_custom_splash(self, char_id):
        for extension in (".png", ".jpg", ".jpeg", ".webp"):
            custom_file = self.splash_directory / f"{char_id}{extension}"
            if custom_file.exists():
                return Image.open(custom_file).convert("RGBA")
        return None

    async def _load_image(self, session, url):
        try:
            async with session.get(url, timeout=10) as response:
                if response.status != 200:
                    return None
                return Image.open(BytesIO(await response.read())).convert("RGBA")
        except Exception:
            return None

    async def generate_card(self, uid, char_id):
        player_profile = await self.player_data_provider.fetch_player_profile(uid)
        if not player_profile or not player_profile.get("avatarInfoList"):
            raise RuntimeError(f"No player profile available for uid={uid}")

        if not self._find_avatar_record(player_profile["avatarInfoList"], char_id):
            if not await self._ensure_character_record(uid, char_id, player_profile):
                raise RuntimeError(f"Character record not found for char_id={char_id} in uid={uid}")

        # Reuse the profile we just fetched above instead of asking
        # Enka for the same uid's data a second time.
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
        target_size = (1875, 890)
        font_small = ImageFont.truetype(self.font_path, 20)

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
                # Live HoYoLAB data - already a full, directly-fetchable
                # CDN URL (hash-named, so it can't be reconstructed as an
                # enka.network/ui/... path the way Enka's own icons can).
                weapon_image = await self._load_image(session, weapon_icon_url)
            elif weapon_icon:
                weapon_image = await self._load_image(session, f"https://enka.network/ui/{weapon_icon}.png")
            else:
                weapon_image = None

            if not background_image:
                background_image = Image.new("RGBA", target_size, (30, 30, 45, 255))

            background_image = ImageOps.fit(background_image, target_size, method=Image.Resampling.LANCZOS).convert("RGBA")
            background_image = ImageEnhance.Brightness(background_image).enhance(0.45)
            ui_layer = Image.new("RGBA", target_size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(ui_layer)

            if splash_image:
                # Custom splashes (from custom_splash/) are left-aligned so
                # they sit flush at x=0 instead of being cropped from the
                # center like enka.network's official gacha splash art.
                ui_layer = paste_splash_left(ui_layer, splash_image, target_size, left_align=bool(custom_splash))

            # Character name, then the player nickname placed right after it
            # (measured, not a fixed x) so the two never overlap regardless of
            # how long the character's name is.
            name_font_size = 36
            name_font = ImageFont.truetype(self.font_path, name_font_size)
            name_width = draw.textlength(character_name, font=name_font)
            draw_text_with_shadow(draw, text=character_name, position=(50, 50), font_path=self.font_path, font_size=name_font_size, anchor="lm")
            draw_text_with_shadow(draw, text=player_profile.get("nickname", ""), position=(50 + name_width + 20, 52), font_path=self.font_path, font_size=24, text_color=(205, 205, 215, 255), anchor="lm")
            draw_text_with_shadow(draw, text=f"Lvl: {character_level}/90", position=(50, 90), font_path=self.font_path, font_size=24, anchor="lm")
            draw_text_with_shadow(draw, text=f"Friendship: {friendship_level}", position=(50, 125), font_path=self.font_path, font_size=24, anchor="lm")

            # Shared left edge for the whole right-hand panel so the weapon
            # header lines up with the stat list directly beneath it.
            panel_x = 950
            weapon_text_x = panel_x + 160

            weapon_position = (panel_x, 18)
            weapon_stats = []
            if weapon_image:
                weapon_icon_resized = ImageOps.contain(weapon_image, (135, 135))
                ui_layer.paste(weapon_icon_resized, weapon_position, weapon_icon_resized)
                draw_text_with_shadow(draw, self._get_weapon_name(stats["weapon"]), (weapon_text_x, 45), self.font_path, 30, anchor="lm")
                refinement = stats["weapon"].get("refinement", 1)
                weapon_level = stats["weapon"].get("level", 1)
                max_level = "90" if stats["weapon"].get("rank", 0) == 5 else "80" if stats["weapon"].get("rank", 0) == 4 else "70"
                level_text = f"R{refinement}   Lv.{weapon_level}/{max_level}"
                draw_text_with_shadow(draw, level_text, (weapon_text_x, 88), self.font_path, 22, anchor="lm")
                weapon_stats = stats["weapon"].get("stats", [])

            star_icon_path = f"assets/icons/stars/Star{stats['weapon'].get('rarity', 5)}.png"
            try:
                star_image = Image.open(star_icon_path).convert("RGBA").resize((120, 34), Image.Resampling.LANCZOS)
                ui_layer.paste(star_image, (panel_x + 8, 150), star_image)
            except Exception as error:
                logger.warning("CharacterCardGenerator: error loading star image %s: %s", star_icon_path, error)

            stat_x_start = weapon_text_x
            stat_y = 108
            for index, stat in enumerate(weapon_stats):
                current_x = stat_x_start + index * 125
                draw.rounded_rectangle([current_x, stat_y, current_x + 115, stat_y + 38], radius=5, fill=(255, 255, 255, 100))
                stat_icon_path = W_STAT_ICONS.get(stat["prop"], "assets/icons/atk.png")
                try:
                    stat_icon = Image.open(stat_icon_path).convert("RGBA").resize((22, 22), Image.Resampling.LANCZOS)
                    ui_layer.paste(stat_icon, (current_x + 5, stat_y + 9), stat_icon)
                except Exception:
                    pass
                stat_value = f"{stat['val']}"
                if any(token in str(stat["prop"]) for token in ["PERCENT", "CHARGE", "CRITICAL"]):
                    stat_value += "%"
                draw.text((current_x + 40, stat_y + 19), stat_value, font=font_small, fill=(255, 255, 255), anchor="lm")

            stat_config = [
                ("Max HP", "hp", "{:.0f}", "assets/icons/hp.png"),
                ("ATK", "atk", "{:.0f}", "assets/icons/atk.png"),
                ("DEF", "def", "{:.0f}", "assets/icons/def.png"),
                ("CRIT Rate", "cr", "{:.1f}%", "assets/icons/cr.png"),
                ("CRIT DMG", "cd", "{:.1f}%", "assets/icons/cd.png"),
                ("Energy Recharge", "er", "{:.1f}%", "assets/icons/er.png"),
                (f"{stats['element']} DMG Bonus", "elem_bonus", "{:.1f}%", f"assets/icons/{stats['element'].lower()}.png"),
                ("Elemental Mastery", "em", "{:.0f}", "assets/icons/em.png"),
            ]
            stat_start_x = 950
            stat_spacing = 50
            for index, (label, key, fmt, icon_path) in enumerate(stat_config):
                row_y = 220 + index * stat_spacing
                try:
                    icon = Image.open(icon_path).convert("RGBA").resize((35, 35), Image.Resampling.LANCZOS)
                    ui_layer.paste(icon, (stat_start_x + 10, row_y + 10), icon)
                except Exception:
                    pass
                draw_text_with_shadow(draw, label, (stat_start_x + 60, row_y + 18), self.font_path, 24, text_color=(230, 230, 230), anchor="lm")
                value_text = fmt.format(stats.get(key, 0))
                draw_text_with_shadow(draw, value_text, (stat_start_x + 660, row_y + 18), self.font_path, 26, anchor="rm")

            await draw_horizontal_artifacts(session, ui_layer, avatar_record, 150, 650, ImageFont.truetype(self.font_path, 22))
            final_image = Image.alpha_composite(background_image, ui_layer)
            draw_build_column(final_image, 650, build_data, talent_icons, constellation_icons)
            apply_watermark(final_image)

            buffer = BytesIO()
            final_image.convert("RGB").save(buffer, format="JPEG", quality=95)
            buffer.seek(0)
            buffer.name = f"{char_id}.jpg"
            return buffer