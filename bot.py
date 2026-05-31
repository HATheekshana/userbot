import json
import os
import aiohttp

from pyrogram import Client, filters
from dotenv import load_dotenv

from character_card import compare_characters

load_dotenv()

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")

UID1 = os.getenv("UID1")
UID2 = os.getenv("UID2")

current_uid = UID1

with open("char.json", "r", encoding="utf-8") as f:
    CHAR_MAP = json.load(f)

NAME_TO_ID = {}

for char_id, info in CHAR_MAP.items():
    if "-" in char_id:
        continue

    name = info["name"].lower()

    NAME_TO_ID[name] = int(char_id)
    NAME_TO_ID[name.replace(" ", "")] = int(char_id)

app = Client(
    "genshin_userbot",
    api_id=API_ID,
    api_hash=API_HASH
)


@app.on_message(filters.me & filters.text)
async def commands(client, message):
    global current_uid

    text = message.text.strip()

    # -------------------------
    # SWITCH UID
    # -------------------------
    if text.lower() == "!switch":
        if current_uid == UID1:
            current_uid = UID2
            active = "UID2"
        else:
            current_uid = UID1
            active = "UID1"

        await message.edit(
            f"✅ Switched to {active}\n"
            f"UID: {current_uid}"
        )
        return

    # -------------------------
    # SHOW CURRENT UID
    # -------------------------
    if text.lower() == "!uid":
        await message.edit(
            f"Current UID:\n{current_uid}"
        )
        return

    # -------------------------
    # SHOW CHARACTER
    # -------------------------
    if not text.lower().startswith("!show"):
        return

    parts = text.split(maxsplit=1)

    if len(parts) < 2:
        await message.edit(
            "Usage:\n"
            "!show Furina\n"
            "!show Arlecchino\n"
            "!show Mavuika"
        )
        return

    query = parts[1].strip().lower()

    char_id = NAME_TO_ID.get(query)

    if not char_id:
        for name, cid in NAME_TO_ID.items():
            if query in name:
                char_id = cid
                break

    if not char_id:
        await message.edit(
            f"❌ Character not found:\n{parts[1]}"
        )
        return

    await message.edit("🎴 Generating card...")

    try:
        image_buffer = await compare_characters(
            current_uid,
            char_id
        )

        if not image_buffer:
            await message.edit("❌ Card generation failed")
            return

        # -------------------------
        # Ranking
        # -------------------------
        ranking_text = ""

        try:
            ranking_api = (
                f"https://test-xehj.onrender.com/get/ranking/{current_uid}"
            )

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    ranking_api,
                    timeout=5
                ) as rank_resp:

                    if rank_resp.status == 200:
                        all_ranks = await rank_resp.json()

                        char_rank_data = all_ranks.get(
                            str(char_id)
                        )

                        if char_rank_data:
                            rank = char_rank_data.get("ranking")
                            out_of = char_rank_data.get("outOf")
                            percent = char_rank_data.get("percent")

                            ranking_text = (
                                f"\n\nʚଓ Global Rank: {rank}/{out_of}"
                                f"\nʚଓ Top: {percent}%"
                            )

        except Exception as rank_error:
            print(
                f"Ranking API Error: {rank_error}"
            )
        character_name = CHAR_MAP[str(char_id)]["name"]

        caption = (
                    f"<b>{character_name}</b>"
                    f"{ranking_text}"
                )
        await client.send_photo(
        chat_id=message.chat.id,
        photo=image_buffer,
        caption=caption,
        parse_mode="html"
            )

        await message.delete()

    except Exception as e:
        await message.edit(
            f"❌ Error:\n{e}"
        )


print("Userbot started...")
print(f"Default UID: {UID1}")

app.run()