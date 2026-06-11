import json
import os
import aiohttp

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from dotenv import load_dotenv

from character_card import compare_characters

load_dotenv()

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")

UID1 = os.getenv("UID1")
UID2 = os.getenv("UID2")

current_uid = UID1

# -------------------------
# LOAD CHARACTER DATA
# -------------------------
with open("char.json", "r", encoding="utf-8") as f:
    CHAR_MAP = json.load(f)

NAME_TO_ID = {}

for char_id, info in CHAR_MAP.items():
    if "-" in char_id:
        continue

    name = info["name"].lower()
    NAME_TO_ID[name] = int(char_id)
    NAME_TO_ID[name.replace(" ", "")] = int(char_id)

# -------------------------
# CLIENT
# -------------------------
app = Client(
    "genshin_userbot",
    api_id=API_ID,
    api_hash=API_HASH,
    parse_mode=ParseMode.HTML
)

# -------------------------
# COMMAND HANDLER
# -------------------------
@app.on_message(filters.me & filters.text)
async def commands(client, message):
    global current_uid

    text = message.text.strip()
    low = text.lower()

    # SWITCH UID
    if low == "!switch":
        current_uid = UID2 if current_uid == UID1 else UID1
        await message.edit(f"✅ Switched UID\n{current_uid}")
        return

    # SHOW UID
    if low == "!uid":
        await message.edit(f"Current UID:\n{current_uid}")
        return

    # -------------------------
    # ADD SPLASH (REPLY SYSTEM)
    # -------------------------
    if low.startswith("!add_splash"):
        parts = text.split(maxsplit=1)

        if len(parts) < 2:
            await message.edit("Usage:\nReply to image + !add_splash Chasca")
            return

        # MUST BE REPLY TO IMAGE
        if not message.reply_to_message or not message.reply_to_message.photo:
            await message.edit("❌ Reply to an image first")
            return

        query = parts[1].strip().lower()

        char_id = NAME_TO_ID.get(query)

        if not char_id:
            for name, cid in NAME_TO_ID.items():
                if query in name:
                    char_id = cid
                    break

        if not char_id:
            await message.edit("❌ Character not found")
            return

        os.makedirs("custom_splash", exist_ok=True)

        # remove old splash
        for f in os.listdir("custom_splash"):
            if f.startswith(str(char_id)):
                os.remove(os.path.join("custom_splash", f))

        # download replied image
        file_path = await client.download_media(
            message.reply_to_message,
            file_name="custom_splash/temp"
        )

        ext = os.path.splitext(file_path)[1]
        if not ext:
            ext = ".jpg"

        final_path = f"custom_splash/{char_id}{ext}"
        os.replace(file_path, final_path)

        await message.edit(
            f"✅ Splash saved for:\n<b>{CHAR_MAP[str(char_id)]['name']}</b>"
        )
        return

    # -------------------------
    # SHOW CHARACTER
    # -------------------------
    if not low.startswith("!show"):
        return

    parts = text.split(maxsplit=1)

    if len(parts) < 2:
        await message.edit("Usage:\n!show Furina")
        return

    query = parts[1].strip().lower()

    char_id = NAME_TO_ID.get(query)

    if not char_id:
        for name, cid in NAME_TO_ID.items():
            if query in name:
                char_id = cid
                break

    if not char_id:
        await message.edit("❌ Character not found")
        return

    await message.edit("🎴 Generating card...")

    try:
        image_buffer = await compare_characters(current_uid, char_id)

        if not image_buffer:
            await message.edit("❌ Card generation failed")
            return

        # RANKING
        ranking_text = ""

        try:
            url = f"https://test-xehj.onrender.com/get/ranking/{current_uid}"

            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=5) as resp:
                    if resp.status == 200:
                        data = await resp.json()

                        c = data.get(str(char_id))
                        if c:
                            ranking_text = (
                                f"\n\nʚଓ Global Rank: {c['ranking']}/{c['outOf']}"
                                f"\nʚଓ Top: {c['percent']}%"
                            )

        except Exception as e:
            print("Ranking error:", e)

        name = CHAR_MAP[str(char_id)]["name"]

        await client.send_photo(
            message.chat.id,
            photo=image_buffer,
            caption=f"<b>{name}</b>{ranking_text}"
        )

        await message.delete()

    except Exception as e:
        await message.edit(f"❌ Error:\n{e}")


print("Userbot started...")
print(f"Default UID: {UID1}")

app.run()