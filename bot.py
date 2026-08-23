import json
import logging
import os
import sys
import time
import aiohttp
import traceback
from pathlib import Path

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from dotenv import load_dotenv

from cards.character_card import CharacterCardGenerator
from cards.futuristic_character_card import FuturisticCharacterCardGenerator
from services.net import new_session, is_dns_error
from services.ranking import get_ranking
from services.abyss import get_abyss
from cards.abyss_card import generate_abyss_card
from services.stygian import get_stygian, normalize_stygian
from cards.stygian_card import generate_stygian_card
from services.theater import get_theater
from cards.theater_card import generate_theater_card
from services.notes import get_notes, format_notes

# !myc's character picker: how many character buttons per page (2 per row).
MYC_PAGE_SIZE = 10

# Wispbyte (and some other panels) can launch the process with a working
# directory that isn't the project root, which would break every relative
# path in this project (char.json, assets/, custom_splash/, etc). Anchor
# the working directory to this file's location so it works no matter how
# the startup command invokes it.
BASE_DIR = Path(__file__).resolve().parent
os.chdir(BASE_DIR)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("genshin_userbot")

REQUIRED_ENV_VARS = ("API_ID", "API_HASH", "UID1")


class UserBot:
    def __init__(self):
        env_path = BASE_DIR / ".env"
        load_dotenv(dotenv_path=env_path)

        logger.info("Loaded .env from: %s (exists: %s)", env_path, env_path.exists())
        logger.info("SESSION_STRING set: %s", bool(os.getenv("SESSION_STRING")))

        self._validate_env()

        self.api_id = int(os.getenv("API_ID"))
        self.api_hash = os.getenv("API_HASH")
        self.uid1 = os.getenv("UID1")
        self.uid2 = os.getenv("UID2") or self.uid1
        self.current_uid = self.uid1

        session_string = os.getenv("SESSION_STRING")
        if session_string:
            # Preferred on Wispbyte: no interactive login needed, and the
            # login survives a redeploy/container rebuild that wipes disk.
            logger.info("Using SESSION_STRING for authentication (no login prompt needed).")
            self.client = Client(
                "genshin_userbot",
                api_id=self.api_id,
                api_hash=self.api_hash,
                session_string=session_string,
                in_memory=True,
                parse_mode=ParseMode.HTML,
            )
        else:
            logger.warning(
                "No SESSION_STRING set. Falling back to interactive login via the "
                "console; run generate_session.py locally to avoid this on Wispbyte."
            )
            self.client = Client(
                "genshin_userbot",
                api_id=self.api_id,
                api_hash=self.api_hash,
                parse_mode=ParseMode.HTML,
            )

        # Two interchangeable card renderers sharing the same char.json /
        # data.json / new.json / custom_splash data. self.card_style picks
        # which one !show and !myc use; "!change" toggles it at runtime.
        self.card_generators = {
            "classic": CharacterCardGenerator(),
            "futuristic": FuturisticCharacterCardGenerator(),
        }
        self.card_style = "classic"
        self.char_map = self._load_character_map()
        self.name_to_id = self._build_name_to_id_map()
        self.myc_character_list = self._build_myc_character_list()
        self._register_handlers()

    def _validate_env(self):
        missing = [name for name in REQUIRED_ENV_VARS if not os.getenv(name)]
        if missing:
            logger.error(
                "Missing required environment variable(s): %s. "
                "Set them in the Wispbyte panel's Startup > Environment Variables tab "
                "(or in a local .env file) and restart.",
                ", ".join(missing),
            )
            sys.exit(1)

    def _load_character_map(self):
        with open("data/char.json", "r", encoding="utf-8") as handle:
            return json.load(handle)

    def _resolve_character_name(self, info):
        """Best-effort display name for a char.json entry.

        char.json is a raw EnkaNetwork `characters.json` snapshot (see
        data/update_data.py) - it does NOT ship a "name" field directly,
        only a NameTextMapHash that has to be resolved against a text map.
        Older copies of this project may have had a char.json with names
        baked in, so we still check "name" first for backward compatibility,
        then fall back through progressively less reliable sources instead
        of crashing on KeyError:

          1. info["name"]                          - if present
          2. data/new.json[str(NameTextMapHash)]    - proper localized name
          3. info["SideIconName"] with the
             "UI_AvatarIcon_Side_" prefix stripped  - internal codename
             (e.g. "Qin" for Jean, "Tohma" for Thoma) - not always what
             players type, but stable and non-crashing.

        Returns None if nothing usable is found, so the caller can skip
        the entry instead of poisoning the map with a bad key.
        """
        name = info.get("name")
        if name:
            return name

        text_map = self.card_generators["classic"].text_map
        name_hash = str(info.get("NameTextMapHash", ""))
        text_name = text_map.get(name_hash)
        if text_name:
            return text_name

        side_icon = info.get("SideIconName") or ""
        prefix = "UI_AvatarIcon_Side_"
        if side_icon.startswith(prefix):
            return side_icon[len(prefix):]

        return None

    def _build_name_to_id_map(self):
        mapping = {}
        skipped = []
        for char_id, info in self.char_map.items():
            if "-" in char_id:
                continue
            name = self._resolve_character_name(info)
            if not name:
                skipped.append(char_id)
                continue
            normalized_name = name.lower()
            mapping[normalized_name] = int(char_id)
            mapping[normalized_name.replace(" ", "")] = int(char_id)
        if skipped:
            logger.warning(
                "Skipped %d char.json entr%s with no resolvable name (char_id(s): %s). "
                "Run data/update_data.py or refresh data/new.json to fix this.",
                len(skipped),
                "y" if len(skipped) == 1 else "ies",
                ", ".join(skipped),
            )
        return mapping

    def _build_myc_character_list(self):
        entries = []
        for char_id, info in self.char_map.items():
            if "-" in char_id:
                continue
            name = self._resolve_character_name(info)
            if not name:
                continue
            entries.append((int(char_id), name))
        entries.sort(key=lambda entry: entry[1].lower())
        return entries

    def _build_myc_keyboard(self, page):
        total_pages = max(1, -(-len(self.myc_character_list) // MYC_PAGE_SIZE))
        page = max(0, min(page, total_pages - 1))
        start = page * MYC_PAGE_SIZE
        page_entries = self.myc_character_list[start:start + MYC_PAGE_SIZE]

        rows = []
        for index in range(0, len(page_entries), 2):
            pair = page_entries[index:index + 2]
            rows.append([
                InlineKeyboardButton(name, callback_data=f"myc:show:{char_id}")
                for char_id, name in pair
            ])

        nav_row = []
        if page > 0:
            nav_row.append(InlineKeyboardButton("◀️ Prev", callback_data=f"myc:page:{page - 1}"))
        nav_row.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="myc:noop"))
        if page < total_pages - 1:
            nav_row.append(InlineKeyboardButton("Next ▶️", callback_data=f"myc:page:{page + 1}"))
        rows.append(nav_row)
        rows.append([InlineKeyboardButton("✖️ Close", callback_data="myc:close")])

        return InlineKeyboardMarkup(rows)

    async def _resolve_character_id(self, query, uid=None):
        query_lower = query.lower()
        char_id = self.name_to_id.get(query_lower)
        if char_id:
            return char_id
        for name, resolved_id in self.name_to_id.items():
            if query_lower in name:
                return resolved_id

        # Not in our local cache - almost certainly a character newer than
        # our last char.json refresh (e.g. Odette/Alyosha day one of a new
        # patch). Try a live HoYoLAB lookup instead of failing outright.
        live_uid = uid or self.current_uid
        # resolve_by_name is inherited unchanged by FuturisticCharacterCardGenerator
        # and persists to the shared char.json, so either instance works - use
        # "classic" explicitly rather than whatever self.card_style is right now.
        result = await self.card_generators["classic"].resolve_by_name(query, live_uid)
        if not result:
            return None

        resolved_id, entry = result
        # card_generator already persisted this to char.json and updated
        # its own in-memory char_map - mirror that into bot.py's separate
        # copies so !show/!myc/!add_splash see it for the rest of this run
        # without needing a restart.
        self.char_map[str(resolved_id)] = entry
        normalized_name = entry["name"].lower()
        self.name_to_id[normalized_name] = resolved_id
        self.name_to_id[normalized_name.replace(" ", "")] = resolved_id
        self.myc_character_list.append((resolved_id, entry["name"]))
        self.myc_character_list.sort(key=lambda item: item[1].lower())

        return resolved_id

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

        @self.client.on_callback_query()
        async def callbacks(_, query):
            await self.handle_callback_query(query)

    async def _fetch_ranking(self, uid, char_id):
        try:
            ranking = await get_ranking(uid, char_id)
            if ranking:
                return (
                    f"\n\nʚଓ Global Rank: {ranking['ranking']}/{ranking['outOf']}"
                    f"\nʚଓ Top: {ranking['percent']}%"
                )
        except Exception as error:
            # Ranking is a "nice to have" caption add-on, never worth
            # failing card generation over - log one line and move on.
            reason = "DNS resolution failed" if is_dns_error(error) else str(error)
            logger.warning("Ranking lookup skipped for uid=%s: %s", uid, reason)
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

        if low_text.startswith("!change"):
            parts = text.split(maxsplit=1)
            requested = parts[1].strip().lower() if len(parts) > 1 else None

            if requested in ("classic", "futuristic"):
                self.card_style = requested
            elif requested:
                await message.edit(
                    "❌ Unknown style. Usage:\n!change - toggle\n!change classic\n!change futuristic"
                )
                return
            else:
                self.card_style = "futuristic" if self.card_style == "classic" else "classic"

            await message.edit(f"✅ Card style set to <b>{self.card_style}</b>")
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
            char_id = await self._resolve_character_id(query)
            if not char_id:
                await message.edit("❌ Character not found")
                return

            os.makedirs("data/custom_splash", exist_ok=True)
            for current_file in os.listdir("data/custom_splash"):
                if current_file.startswith(str(char_id)):
                    os.remove(os.path.join("data/custom_splash", current_file))

            file_path = await self.client.download_media(
                message.reply_to_message,
                file_name="data/custom_splash/temp",
            )
            extension = os.path.splitext(file_path)[1] or ".jpg"
            final_path = f"data/custom_splash/{char_id}{extension}"
            os.replace(file_path, final_path)
            await message.edit(
                f"✅ Splash saved for:\n<b>{self.char_map[str(char_id)]['name']}</b>"
            )
            return

        if low_text == "!myc":
            if not self.myc_character_list:
                await message.edit("❌ No characters found in char.json")
                return
            await message.edit("🧑‍🤝‍🧑 Pick a character:", reply_markup=self._build_myc_keyboard(0))
            return

        if low_text.startswith("!abyss"):
            parts = text.split(maxsplit=1)
            previous = len(parts) > 1 and parts[1].strip().lower() in ("prev", "previous", "last")
            await message.edit("📜 Generating abyss report...")
            try:
                abyss = await get_abyss(self.current_uid, previous=previous)
                if not abyss.floors:
                    await message.edit(
                        "❌ No Spiral Abyss data for that season yet "
                        "(clear at least one chamber first, or try `!abyss previous`)."
                    )
                    return
                image_buffer = await generate_abyss_card(self.current_uid, abyss)
                await self.client.send_photo(
                    message.chat.id,
                    photo=image_buffer,
                    caption=f"<b>Spiral Abyss Report</b> — Season {abyss.season}",
                )
                await message.delete()
            except RuntimeError as error:
                await message.edit(f"❌ {error}")
            except Exception as error:
                logger.error("Exception generating abyss report for uid=%s: %s", self.current_uid, error)
                logger.debug("Full traceback for the error above:", exc_info=True)
                # Validation errors can contain dozens of lines. Telegram only allows
                # short message edits, so keep the detail in the server log instead.
                await message.edit(
                    "❌ Could not generate the Abyss report. "
                    "Check the server console for details, then try again."
                )
            return

        if low_text.startswith("!stygian"):
            await message.edit("📜 Generating stygian onslaught report...")
            try:
                stygian = await get_stygian(self.current_uid)
                data = normalize_stygian(self.current_uid, stygian)
                if not data:
                    await message.edit(
                        "❌ No Stygian Onslaught data for this cycle yet "
                        "(clear at least one battlefield first)."
                    )
                    return
                image_buffer = await generate_stygian_card(data)
                await self.client.send_photo(
                    message.chat.id,
                    photo=image_buffer,
                    caption="<b>Stygian Onslaught Report</b>",
                )
                await message.delete()
            except RuntimeError as error:
                await message.edit(f"❌ {error}")
            except Exception as error:
                logger.error("Exception generating stygian report for uid=%s: %s", self.current_uid, error)
                logger.debug("Full traceback for the error above:", exc_info=True)
                await message.edit(
                    "❌ Could not generate the Stygian Onslaught report. "
                    "Check the server console for details, then try again."
                )
            return

        if low_text.startswith("!theater"):
            await message.edit("📜 Generating Imaginarium Theater report...")
            try:
                theater = await get_theater(self.current_uid)

                # genshin.py newer versions wrap data inside datas
                if hasattr(theater, "datas"):
                    if not theater.datas:
                        await message.edit(
                            "❌ No Imaginarium Theater data for this cycle yet."
                        )
                        return
                else:
                    if not theater:
                        await message.edit(
                            "❌ No Imaginarium Theater data for this cycle yet."
                        )
                        return

                image_buffer = await generate_theater_card(
                    self.current_uid,
                    theater
                )

                await self.client.send_photo(
                    message.chat.id,
                    photo=image_buffer,
                    caption="<b>Imaginarium Theater Report</b>",
                )
                await message.delete()

            except RuntimeError as error:
                await message.edit(f"❌ {error}")

            except Exception as error:
                logger.error(
                    "Exception generating theater report for uid=%s: %s",
                    self.current_uid,
                    error
                )
                logger.debug(
                    "Full traceback for the error above:",
                    exc_info=True
                )

                await message.edit(
                    "❌ Could not generate the Imaginarium Theater report. "
                    "Check the server console for details, then try again."
                )
            return
        if low_text.startswith("!note"):
            await message.edit("📜 Fetching real-time notes...")
            try:
                notes = await get_notes(self.current_uid)
                await message.edit(format_notes(notes))
            except RuntimeError as error:
                await message.edit(f"❌ {error}")
            except Exception as error:
                error_text = "Connection/DNS issue reaching HoYoLAB" if is_dns_error(error) else (str(error) or "Unknown error")
                logger.error("Exception fetching notes for uid=%s: %s", self.current_uid, error_text)
                logger.debug("Full traceback for the error above:", exc_info=True)
                await message.edit(f"❌ Could not fetch real-time notes: {error_text}")
            return
        if not low_text.startswith("!show"):
            return

        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            await message.edit("Usage:\n!show Furina")
            return

        query = parts[1].strip()
        char_id = await self._resolve_character_id(query)
        if not char_id:
            await message.edit("❌ Character not found")
            return

        await message.edit("🎴 Generating card...")

        try:
            await self._send_character_card(message.chat.id, char_id)
            await message.delete()
        except Exception as error:
            error_text = "Connection/DNS issue reaching Enka" if is_dns_error(error) else (str(error) or "Unknown error")
            logger.error("Exception generating card for uid=%s, char_id=%s: %s", self.current_uid, char_id, error_text)
            logger.debug("Full traceback for the error above:", exc_info=True)
            await message.edit(f"❌ Error generating card: {error_text}")

    async def _send_character_card(self, chat_id, char_id):
        """Generates a character card for self.current_uid and sends it to
        chat_id. Raises on failure - callers decide how to surface that
        (message.edit for !show, query.answer(show_alert=True) for !myc)."""
        generator = self.card_generators[self.card_style]
        image_buffer = await generator.generate_card(self.current_uid, char_id)
        if not image_buffer:
            logger.warning("Card generation returned no image for uid=%s, char_id=%s", self.current_uid, char_id)
            raise RuntimeError("Card generation failed. Check service logs for details.")

        ranking_text = await self._fetch_ranking(self.current_uid, char_id)
        name = self.char_map[str(char_id)]["name"]

        await self.client.send_photo(
            chat_id,
            photo=image_buffer,
            caption=f"<b>{name}</b>{ranking_text}",
        )

    async def handle_callback_query(self, query):
        data = query.data or ""

        if data == "myc:noop":
            await query.answer()
            return

        if data == "myc:close":
            await query.answer()
            await query.message.delete()
            return

        if data.startswith("myc:page:"):
            page = int(data.split(":")[2])
            await query.message.edit_reply_markup(self._build_myc_keyboard(page))
            await query.answer()
            return

        if data.startswith("myc:show:"):
            char_id = int(data.split(":")[2])
            await query.answer("🎴 Generating card...")
            try:
                await self._send_character_card(query.message.chat.id, char_id)
            except Exception as error:
                error_text = "Connection/DNS issue reaching Enka" if is_dns_error(error) else (str(error) or "Unknown error")
                logger.error("Exception generating card via !myc for uid=%s, char_id=%s: %s", self.current_uid, char_id, error_text)
                logger.debug("Full traceback for the error above:", exc_info=True)
                await query.answer(f"❌ {error_text}"[:200], show_alert=True)
            return

        await query.answer()

    def run(self):
        logger.info("Userbot started...")
        logger.info("Default UID: %s", self.uid1)
        self.client.run()


def main():
    # Wispbyte restarts the process if it exits, but retrying in-process
    # first avoids a full container restart cycle for transient network
    # errors (e.g. a brief Telegram/API outage) and gets you back online
    # faster.
    max_retries = 5
    for attempt in range(1, max_retries + 1):
        try:
            UserBot().run()
            break  # client.run() only returns on a clean stop
        except Exception:
            logger.error("Userbot crashed (attempt %d/%d):", attempt, max_retries)
            traceback.print_exc()
            if attempt == max_retries:
                logger.error("Max retries reached, exiting so the host can restart the container.")
                sys.exit(1)
            time.sleep(10)


if __name__ == "__main__":
    main()