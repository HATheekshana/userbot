import asyncio
import aiohttp
import json
import os
import traceback
from io import BytesIO
from pathlib import Path
from PIL import Image, ImageDraw, ImageOps, ImageChops, ImageEnhance, ImageFont

from t_c import CharacterBuildFetcher, HoyolabClient, EnkaClient, draw_build_column
from artifacts import draw_horizontal_artifacts

W_STAT_ICONS = {
    "FIGHT_PROP_BASE_ATTACK": "asstests/icons/atk.png",
    "FIGHT_PROP_CHARGE_EFFICIENCY": "asstests/icons/er.png",
    "FIGHT_PROP_ELEMENT_MASTERY": "asstests/icons/em.png",
    "FIGHT_PROP_CRITICAL": "asstests/icons/cr.png",
    "FIGHT_PROP_CRITICAL_HURT": "asstests/icons/cd.png",
    "FIGHT_PROP_ATTACK_PERCENT": "asstests/icons/atk.png",
    "FIGHT_PROP_HP_PERCENT": "asstests/icons/hp.png",
    "FIGHT_PROP_DEFENSE_PERCENT": "asstests/icons/def.png",
}

SPECIAL_MAPPINGS = {
    "Ambor": "Amber",
    "Noel": "Noelle",
    "Feiyan": "Yanfei",
    "Shougun": "Raiden",
    "Tohma": "Thoma",
    "Heizo": "Heizou",
    "Liney": "Lyney",
    "Liuyun": "Xianyun",
}

SPECIAL_MAPPINGS2 = {
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


def draw_text_with_shadow(draw, text, position, font_path, font_size, text_color=(255, 255, 255, 255), shadow_color=(0, 0, 0, 180), anchor="mm", shadow_offset=(2, 2)):
    font = ImageFont.truetype(font_path, font_size)
    shadow_position = (position[0] + shadow_offset[0], position[1] + shadow_offset[1])
    draw.text(shadow_position, text, font=font, fill=shadow_color, anchor=anchor)
    draw.text(position, text, font=font, fill=text_color, anchor=anchor)


async def fetch_image(session, url):
    try:
        async with session.get(url) as response:
            if response.status != 200:
                return None
            return Image.open(BytesIO(await response.read())).convert("RGBA")
    except Exception:
        return None


def paste_splash_left(ui_layer, splash_image, size):
    card_width, card_height = size
    left_width, fade_width = 760, 160
    scale = card_height / splash_image.height
    splash_image = splash_image.resize((int(splash_image.width * scale), card_height), Image.Resampling.LANCZOS)
    splash_image = splash_image.crop(((splash_image.width - left_width) // 2, 0, (splash_image.width - left_width) // 2 + left_width, card_height))

    mask = Image.new("L", (left_width, card_height), 255)
    draw = ImageDraw.Draw(mask)
    for index in range(fade_width):
        draw.line([(left_width - fade_width + index, 0), (left_width - fade_width + index, card_height)], fill=int(255 * (1 - index / fade_width)))

    splash_image.putalpha(ImageChops.multiply(splash_image.getchannel("A"), mask))
    ui_layer.paste(splash_image, (0, 0), splash_image)
    return ui_layer


class PlayerDataProvider:
    def __init__(self, hoyolab_client=None, enka_client=None):
        self.hoyolab_client = hoyolab_client or HoyolabClient()
        self.enka_client = enka_client or EnkaClient()

    async def fetch_player_profile(self, uid):
        profile = await self.hoyolab_client.fetch_player_profile(uid)
        if profile and profile.get("avatarInfoList"):
            return profile
        print(f"PlayerDataProvider: falling back to Enka for uid={uid}")
        return await self.enka_client.fetch_avatar_data(uid)


class CharacterCardGenerator:
    def __init__(self, char_map_path="char.json", namecard_path="data.json", text_map_path="new.json", splash_directory="custom_splash", font_path="asstests/fonts/Genshin_Impact.ttf"):
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

    def _lookup_character_info(self, char_id):
        return self.char_map.get(str(char_id), {"element": "Anemo", "avataricon": "UI_AvatarIcon_Qin"})

    def _get_namecard_urls(self, avatar_icon):
        base_name = avatar_icon.replace("UI_AvatarIcon_", "")
        search_name = SPECIAL_MAPPINGS2.get(base_name, base_name)
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
        name_hash = str(weapon_info.get("hash", ""))
        return self.text_map.get(name_hash, f"Weapon {weapon_info.get('id')}")

    def _find_avatar_record(self, avatar_list, char_id):
        return next((entry for entry in avatar_list if str(entry.get("avatarId")) == str(char_id)), None)

    def _extract_character_stats(self, avatar_list, char_id, element):
        element = element.capitalize()
        element_map = {
            "Pyro": 40,
            "Electro": 41,
            "Hydro": 42,
            "Dendro": 43,
            "Anemo": 45,
            "Geo": 44,
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
                    "hash": flat_data.get("nameTextMapHash"),
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

        build_data, talent_icons, constellation_icons = await self.build_fetcher.fetch_build_assets(uid, char_id)
        if not build_data:
            raise RuntimeError(f"No build/assets data found for uid={uid}, char_id={char_id}")

        character_info = self._lookup_character_info(char_id)
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

        async with aiohttp.ClientSession() as session:
            custom_splash = await self._load_custom_splash(char_id)
            splash_image = custom_splash or await self._load_image(session, self._get_splash_url(avatar_icon))
            background_url = self._get_namecard_urls(avatar_icon)
            background_image = None
            for url in background_url:
                background_image = await self._load_image(session, url)
                if background_image:
                    break

            weapon_icon = stats["weapon"].get("icon")
            weapon_image = await self._load_image(session, f"https://enka.network/ui/{weapon_icon}.png") if weapon_icon else None

            if not background_image:
                background_image = Image.new("RGBA", target_size, (30, 30, 45, 255))

            background_image = ImageOps.fit(background_image, target_size, method=Image.Resampling.LANCZOS).convert("RGBA")
            background_image = ImageEnhance.Brightness(background_image).enhance(0.45)
            ui_layer = Image.new("RGBA", target_size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(ui_layer)

            if splash_image:
                ui_layer = paste_splash_left(ui_layer, splash_image, target_size)

            draw_text_with_shadow(draw, text=character_name, position=(50, 50), font_path=self.font_path, font_size=36, anchor="lm")
            draw_text_with_shadow(draw, text=player_profile.get("nickname", ""), position=(150, 50), font_path=self.font_path, font_size=26, anchor="lm")
            draw_text_with_shadow(draw, text=f"Lvl: {character_level}/90", position=(50, 90), font_path=self.font_path, font_size=24, anchor="lm")
            draw_text_with_shadow(draw, text=f"Friendship: {friendship_level}", position=(50, 125), font_path=self.font_path, font_size=24, anchor="lm")

            weapon_position = (900, 20)
            weapon_stats = []
            if weapon_image:
                weapon_icon_resized = ImageOps.contain(weapon_image, (140, 140))
                ui_layer.paste(weapon_icon_resized, weapon_position, weapon_icon_resized)
                draw_text_with_shadow(draw, self._get_weapon_name(stats["weapon"]), (weapon_position[0] + 170, weapon_position[1] + 30), self.font_path, 32, anchor="lm")
                refinement = stats["weapon"].get("refinement", 1)
                weapon_level = stats["weapon"].get("level", 1)
                max_level = "90" if stats["weapon"].get("rank", 0) == 5 else "80" if stats["weapon"].get("rank", 0) == 4 else "70"
                level_text = f"R{refinement}      Lv.{weapon_level}/{max_level}"
                draw_text_with_shadow(draw, level_text, (weapon_position[0] + 170, weapon_position[1] + 80), self.font_path, 24, anchor="lm")
                weapon_stats = stats["weapon"].get("stats", [])

            star_icon_path = f"asstests/icons/stars/Star{stats['weapon'].get('rarity', 5)}.png"
            try:
                star_image = Image.open(star_icon_path).convert("RGBA").resize((140, 40), Image.Resampling.LANCZOS)
                ui_layer.paste(star_image, (weapon_position[0] + 10, weapon_position[1] + 120), star_image)
            except Exception as error:
                print(f"CharacterCardGenerator: error loading star image {star_icon_path}: {error}")

            stat_x_start = weapon_position[0] + 170
            stat_y = weapon_position[1] + 100
            for index, stat in enumerate(weapon_stats):
                current_x = stat_x_start + index * 125
                draw.rounded_rectangle([current_x, stat_y, current_x + 115, stat_y + 40], radius=5, fill=(255, 255, 255, 100))
                stat_icon_path = W_STAT_ICONS.get(stat["prop"], "asstests/icons/atk.png")
                try:
                    stat_icon = Image.open(stat_icon_path).convert("RGBA").resize((22, 22), Image.Resampling.LANCZOS)
                    ui_layer.paste(stat_icon, (current_x + 5, stat_y + 10), stat_icon)
                except Exception:
                    pass
                stat_value = f"{stat['val']}"
                if any(token in str(stat["prop"]) for token in ["PERCENT", "CHARGE", "CRITICAL"]):
                    stat_value += "%"
                draw.text((current_x + 40, stat_y + 20), stat_value, font=font_small, fill=(255, 255, 255), anchor="lm")

            stat_config = [
                ("Max HP", "hp", "{:.0f}", "asstests/icons/hp.png"),
                ("ATK", "atk", "{:.0f}", "asstests/icons/atk.png"),
                ("DEF", "def", "{:.0f}", "asstests/icons/def.png"),
                ("CRIT Rate", "cr", "{:.1f}%", "asstests/icons/cr.png"),
                ("CRIT DMG", "cd", "{:.1f}%", "asstests/icons/cd.png"),
                ("Energy Recharge", "er", "{:.1f}%", "asstests/icons/er.png"),
                (f"{stats['element']} DMG Bonus", "elem_bonus", "{:.1f}%", f"asstests/icons/{stats['element'].lower()}.png"),
                ("Elemental Mastery", "em", "{:.0f}", "asstests/icons/em.png"),
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

            buffer = BytesIO()
            final_image.convert("RGB").save(buffer, format="JPEG", quality=95)
            buffer.seek(0)
            buffer.name = f"{char_id}.jpg"
            return buffer
