import asyncio
import aiohttp
import json
import os
import traceback
from io import BytesIO
from PIL import Image, ImageDraw, ImageOps, ImageFont


class HoyolabClient:
    HOSTS = [
        "https://api-os-takumi.mihoyo.com",
        "https://api-takumi.mihoyo.com",
    ]
    DEFAULT_SERVER = "os_usa"

    def __init__(self):
        self.cookie = self._build_cookie()
        self.device_id = os.getenv("HOYOLAB_DEVICE_ID") or os.getenv("DEVICE_ID") or "00000000-0000-0000-0000-000000000000"
        self.app_version = os.getenv("HOYOLAB_APP_VERSION", "2.35.1")
        self.server = os.getenv("HOYOLAB_SERVER", self.DEFAULT_SERVER)

    def _build_cookie(self):
        cookie = os.getenv("HOYOLAB_COOKIE")
        if cookie:
            print("Hoyolab auth: using HOYOLAB_COOKIE")
            return cookie

        ltuid = os.getenv("ITUID") or os.getenv("ltuid")
        ltoken = os.getenv("ITOKEN_V2") or os.getenv("itoken_v2")
        if ltuid and ltoken:
            print("Hoyolab auth: built cookie from ITUID+ITOKEN_V2")
            return f"ltuid={ltuid}; ltoken={ltoken};"

        print("Hoyolab auth missing: falling back to Enka")
        return None

    @property
    def headers(self):
        if not self.cookie:
            return None
        return {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Referer": "https://webstatic.mihoyo.com/",
            "Origin": "https://webstatic.mihoyo.com",
            "Accept": "application/json, text/plain, */*",
            "x-rpc-client_type": "5",
            "x-rpc-app_version": self.app_version,
            "x-rpc-device_id": self.device_id,
            "Cookie": self.cookie,
        }

    async def fetch_player_profile(self, uid):
        headers = self.headers
        if not headers:
            return None

        nickname = ""
        async with aiohttp.ClientSession(headers=headers) as session:
            for host in self.HOSTS:
                card_url = f"{host}/game_record/app/card/wapi/getGameRecordCard?server={self.server}&uid={uid}"
                try:
                    async with session.get(card_url, timeout=10) as response:
                        print(f"HoyolabClient: card_url host={host}, status={response.status} uid={uid}")
                        if response.status == 404:
                            continue
                        if response.status != 200:
                            text = await response.text()
                            print(f"HoyolabClient: card_url unexpected status {response.status} body={text[:300]!r} host={host}")
                            continue

                        data = await response.json()
                        if data.get("retcode") != 0:
                            print(f"HoyolabClient: card_url retcode={data.get('retcode')} host={host} uid={uid}")
                            continue

                        nickname = data.get("data", {}).get("userInfo", {}).get("nickname", "")
                        break
                except Exception as error:
                    print(f"HoyolabClient: card_url exception host={host} uid={uid}: {error}")
                    traceback.print_exc()
                    continue

            if not nickname:
                print(f"HoyolabClient: failed to load nickname for uid={uid}")
                return None

            for host in self.HOSTS:
                char_url = f"{host}/game_record/app/genshin/api/character?server={self.server}&role_id={uid}"
                try:
                    async with session.get(char_url, timeout=10) as response:
                        print(f"HoyolabClient: character_url host={host}, status={response.status} uid={uid}")
                        if response.status == 404:
                            continue
                        if response.status != 200:
                            text = await response.text()
                            print(f"HoyolabClient: character_url unexpected status {response.status} body={text[:300]!r} host={host}")
                            continue

                        data = await response.json()
                        if data.get("retcode") != 0:
                            print(f"HoyolabClient: character_url retcode={data.get('retcode')} host={host} uid={uid}")
                            continue

                        avatars = data.get("data", {}).get("avatars", []) or []
                        print(f"HoyolabClient: loaded {len(avatars)} avatars for uid={uid} host={host}")
                        return {
                            "nickname": nickname,
                            "avatarInfoList": avatars,
                            "showAvatarInfoList": [],
                        }
                except Exception as error:
                    print(f"HoyolabClient: character_url exception host={host} uid={uid}: {error}")
                    traceback.print_exc()
                    continue

        print(f"HoyolabClient: failed on all hosts for uid={uid}")
        return None


class EnkaClient:
    API_BASE_URL = "https://enka.network/api"

    async def fetch_avatar_data(self, uid):
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self.API_BASE_URL}/uid/{uid}") as response:
                if response.status != 200:
                    print(f"EnkaClient: unexpected status {response.status} for uid={uid}")
                    return None

                data = await response.json()
                player_info = data.get("playerInfo", {})
                avatar_list = data.get("avatarInfoList", [])
                return {
                    "nickname": player_info.get("nickname", ""),
                    "avatarInfoList": avatar_list,
                    "showAvatarInfoList": player_info.get("showAvatarInfoList", []),
                }


class CharacterBuildFetcher:
    def __init__(self, hoyolab_client=None, enka_client=None):
        self.hoyolab_client = hoyolab_client or HoyolabClient()
        self.enka_client = enka_client or EnkaClient()

    async def _fetch_avatar_data(self, uid):
        profile = await self.hoyolab_client.fetch_player_profile(uid)
        if profile and profile.get("avatarInfoList"):
            print(f"CharacterBuildFetcher: using Hoyolab data for uid={uid}")
            return profile

        print(f"CharacterBuildFetcher: falling back to Enka for uid={uid}")
        return await self.enka_client.fetch_avatar_data(uid)

    def _extract_character_build_data(self, avatar_list, char_id, avatars_db):
        for avatar_entry in avatar_list:
            if str(avatar_entry.get("avatarId")) != str(char_id):
                continue

            metadata = avatars_db.get(str(char_id), {})
            skill_order = metadata.get("SkillOrder", [])
            proud_map = metadata.get("ProudMap", {})
            skill_base = avatar_entry.get("skillLevelMap", {})
            skill_extra = avatar_entry.get("proudSkillExtraLevelMap", {})

            skill_levels = []
            for skill_id in skill_order:
                level = skill_base.get(str(skill_id), 1) + skill_extra.get(str(proud_map.get(str(skill_id))), 0)
                skill_levels.append(level)

            return {
                "talents": skill_levels,
                "cons_count": len(avatar_entry.get("talentIdList", [])),
                "cons_icons": metadata.get("Consts", []),
                "skill_icons": [metadata["Skills"].get(str(skill_id)) for skill_id in skill_order if str(skill_id) in metadata.get("Skills", {})],
            }
        return None

    async def _load_avatars_database(self):
        with open("avatars.json", "r", encoding="utf-8") as handle:
            return json.load(handle)

    async def fetch_build_assets(self, uid, char_id):
        avatars_db = await self._load_avatars_database()
        avatar_data = await self._fetch_avatar_data(uid)
        if not avatar_data:
            print(f"CharacterBuildFetcher: no avatar data for uid={uid}")
            return None, None, None

        avatar_list = avatar_data.get("avatarInfoList", [])
        print(f"CharacterBuildFetcher: avatars={len(avatar_list)} for uid={uid}")
        build_data = self._extract_character_build_data(avatar_list, char_id, avatars_db)
        if not build_data:
            print(f"CharacterBuildFetcher: no build data found for char_id={char_id} uid={uid}")
            return None, None, None

        async with aiohttp.ClientSession() as session:
            talent_icons = await asyncio.gather(*[self._fetch_ui_image(session, icon) for icon in build_data["skill_icons"]])
            constellation_icons = await asyncio.gather(*[self._fetch_ui_image(session, icon) for icon in build_data["cons_icons"]])

        return build_data, talent_icons, constellation_icons

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
    font_path = "asstests/fonts/Genshin_Impact.ttf"
    skill_font = ImageFont.truetype(font_path, 18)

    entry_bg = Image.open("asstests/talents/bg.png").convert("RGBA")
    ten_bg = Image.open("asstests/talents/10.png").convert("RGBA")
    con_bg = Image.open("asstests/constant/const_adapt.png").convert("RGBA")
    lock_bg = Image.open("asstests/constant/closed/CLOSED.png").convert("RGBA")
    mask = Image.open("asstests/constant/maska_constant.png").convert("L")

    talent_x = start_x + 30
    talent_y_base = 330
    for index, icon in enumerate(talent_icons):
        if not icon:
            continue
        y = talent_y_base + index * 105
        level = data["talents"][index]
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
