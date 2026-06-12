import json
import os
import aiohttp
import traceback
from pathlib import Path

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from dotenv import load_dotenv

from character_card import CharacterCardGenerator


class UserBot:
    def __init__(self):
        env_path = Path(__file__).resolve().parent / ".env"
        load_dotenv(dotenv_path=env_path)

        print(f"Loaded .env from: {env_path}")
        print("HOYOLAB_COOKIE set:", bool(os.getenv("HOYOLAB_COOKIE")))
        print("ITUID/ltuid set:", bool(os.getenv("ITUID") or os.getenv("ltuid")))
        print("ITOKEN_V2/itoken_v2 set:", bool(os.getenv("ITOKEN_V2") or os.getenv("itoken_v2")))

        self.api_id = int(os.getenv("API_ID"))
        self.api_hash = os.getenv("API_HASH")
        self.uid1 = os.getenv("UID1")
        self.uid2 = os.getenv("UID2")
        self.current_uid = self.uid1

        self.client = Client(
            "genshin_userbot",
            api_id=self.api_id,
            api_hash=self.api_hash,
            parse_mode=ParseMode.HTML,
        )

        self.card_generator = CharacterCardGenerator()
        self.char_map = self._load_character_map()
        self.name_to_id = self._build_name_to_id_map()
        self._register_handlers()

    def _load_character_map(self):
        with open("char.json", "r", encoding="utf-8") as handle:
            return json.load(handle)

    def _build_name_to_id_map(self):
        mapping = {}
        for char_id, info in self.char_map.items():
            if "-" in char_id:
                continue
            normalized_name = info["name"].lower()
            mapping[normalized_name] = int(char_id)
            mapping[normalized_name.replace(" ", "")] = int(char_id)
        return mapping

    def _resolve_character_id(self, query):
        query = query.lower()
        char_id = self.name_to_id.get(query)
        if char_id:
            return char_id
        for name, resolved_id in self.name_to_id.items():
            if query in name:
                return resolved_id
        return None

    def _is_image_message(self, message):
        if message.photo:
            return True
        if message.document:
            mime = message.document.mime_type or ""
            return mime.startswith("image/")
        return False

    def _register_handlers(self):
        @self.client.on_message(filters.me & filters.text)
        async def commands(_, message):
            await self.handle_message(message)

    async def _fetch_ranking(self, uid, char_id):
        url = f"https://test-xehj.onrender.com/get/ranking/{uid}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=5) as response:
                    if response.status == 200:
                        data = await response.json()
                        ranking = data.get(str(char_id))
                        if ranking:
                            return (
                                f"\n\nʚଓ Global Rank: {ranking['ranking']}/{ranking['outOf']}"
                                f"\nʚଓ Top: {ranking['percent']}%"
                            )
        except Exception as error:
            print("Ranking error:", error)
        return ""

    async def handle_message(self, message):
        text = message.text.strip()
        low_text = text.lower()

        if low_text == "!switch":
            self.current_uid = self.uid2 if self.current_uid == self.uid1 else self.uid1
            await message.edit(f"✅ Switched UID\n{self.current_uid}")
            return

        if low_text == "!uid":
            await message.edit(f"Current UID:\n{self.current_uid}")
            return

        if low_text.startswith("!add_splash"):
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                await message.edit("Usage:\nReply to image/file + !add_splash Chasca")
                return
            if not message.reply_to_message or not self._is_image_message(message.reply_to_message):
                await message.edit("❌ Reply to an IMAGE or FILE (png/jpg/webp)")
                return
            query = parts[1].strip()
            char_id = self._resolve_character_id(query)
            if not char_id:
                await message.edit("❌ Character not found")
                return

            os.makedirs("custom_splash", exist_ok=True)
            for current_file in os.listdir("custom_splash"):
                if current_file.startswith(str(char_id)):
                    os.remove(os.path.join("custom_splash", current_file))

            file_path = await self.client.download_media(
                message.reply_to_message,
                file_name="custom_splash/temp",
            )
            extension = os.path.splitext(file_path)[1] or ".jpg"
            final_path = f"custom_splash/{char_id}{extension}"
            os.replace(file_path, final_path)
            await message.edit(
                f"✅ Splash saved for:\n<b>{self.char_map[str(char_id)]['name']}</b>"
            )
            return

        if not low_text.startswith("!show"):
            return

        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            await message.edit("Usage:\n!show Furina")
            return

        query = parts[1].strip()
        char_id = self._resolve_character_id(query)
        if not char_id:
            await message.edit("❌ Character not found")
            return

        await message.edit("🎴 Generating card...")

        try:
            image_buffer = await self.card_generator.generate_card(self.current_uid, char_id)
            if not image_buffer:
                print(f"Card generation returned no image for uid={self.current_uid}, char_id={char_id}")
                await message.edit("❌ Card generation failed. Check service logs for details.")
                return

            ranking_text = await self._fetch_ranking(self.current_uid, char_id)
            name = self.char_map[str(char_id)]["name"]

            await self.client.send_photo(
                message.chat.id,
                photo=image_buffer,
                caption=f"<b>{name}</b>{ranking_text}",
            )
            await message.delete()

        except Exception as error:
            error_text = str(error) or "Unknown error"
            print(f"Exception generating card for uid={self.current_uid}, char_id={char_id}: {error_text}")
            traceback.print_exc()
            await message.edit(f"❌ Error generating card: {error_text}")

    def run(self):
        print("Userbot started...")
        print(f"Default UID: {self.uid1}")
        self.client.run()


if __name__ == "__main__":
    UserBot().run()
