"""
Refresh the bot's static game-data files from EnkaNetwork's public repo.

Why this exists
----------------
char.json, avatars.json, data.json and new.json are one-time *snapshots* of
files EnkaNetwork maintains here:

    https://github.com/EnkaNetwork/API-docs/tree/master/store

Sites like nanoka.cc and lunaris.moe don't have their own database of
characters/namecards/text - they're Enka-showcase style sites that read
straight from Enka's data instead of shipping a frozen copy of it. That's
the entire reason they "update fast": there's nothing to update, they never
go stale. Your bot goes stale because char.json etc. were downloaded once
and zipped up, so a new patch (new characters like Odette/Alyosha) simply
isn't in the file anymore.

The fix isn't "download those sites' data" (they don't expose a stable
data API for that, and scraping a live site is a bad way to depend on
data) - it's to point at the same upstream source they use and re-pull it
on a schedule, instead of re-zipping and re-uploading by hand.

What this script does
----------------------
Downloads the store files this project actually consumes and overwrites
the local copies in place (data/char.json, data/data.json, data/avatars.json),
using the exact filenames/paths the bot already expects (see
cards/character_card.py and cards/hoyolab_character_detail.py):

    store/characters.json  -> data/char.json     (character_card.py char_map_path)
    store/namecards.json   -> data/data.json     (character_card.py namecard_path)
    store/characters.json  -> data/avatars.json  (hoyolab_character_detail.py loads
                                                   this separately; it's the same
                                                   character store, just with
                                                   '/ui/' + '.png' added to every
                                                   icon path - see note below)

new.json is deliberately NOT touched by this script - see "Note on
new.json" below for why, and what to do about it.

Note on new.json
-----------------
new.json has ~6,000 entries (weapon names, artifact names, achievement
names, etc.), not just character names. EnkaNetwork's own text file
(store/loc.json, English locale) only has a few hundred entries - it
covers characters/stats, not the full item catalog - so it is NOT a
drop-in replacement and this script won't silently truncate your file by
using it. The full catalog new.json was built from almost certainly comes
from a datamined TextMapEN.json (the kind of file that ships in
Dimbreath's AnimeGameData repo, hosted on GitLab, not GitHub - so it isn't
reachable with the same raw.githubusercontent.com trick used below). If
new.json needs new entries too, that file has to be re-pulled from wherever
it originally came from; check whatever script or note produced the
original new.json in this project for that source.

Run it manually (from the project root):

    python data/update_data.py

Or, better, run it automatically so you never have to think about it again -
see the bottom of this file for a cron one-liner and a "run on bot startup"
snippet.

Note on avatars.json
---------------------
Your avatars.json is NOT EnkaNetwork's pfps.json (that's profile-picture
icons, unrelated). It's characters.json with every icon-ish field turned
from "UI_Talent_S_Ayaka_01" into "/ui/UI_Talent_S_Ayaka_01.png". This
script reproduces that transform locally instead of trusting a second
stale copy to stay in sync with the first.
"""

import asyncio
import json
import sys
from pathlib import Path

import aiohttp

BASE = "https://raw.githubusercontent.com/EnkaNetwork/API-docs/master/store"

FILES = {
    "characters.json": "char.json",
    "namecards.json": "data.json",
}

HERE = Path(__file__).resolve().parent

# Fields in each character entry that get the "/ui/" + ".png" treatment
# when producing avatars.json from characters.json.
_ICON_FIELDS = ("Consts",)  # list of icon names
_ICON_KEYS = ("SideIconName",)  # single icon-name string fields


def _wrap_icon(name: str) -> str:
    if not isinstance(name, str) or name.startswith("/ui/"):
        return name
    return f"/ui/{name}.png"


def _wrap_skill_map(d: dict) -> dict:
    return {k: _wrap_icon(v) for k, v in d.items()} if isinstance(d, dict) else d


def _wrap_costumes(costumes: dict) -> dict:
    if not isinstance(costumes, dict):
        return costumes
    out = {}
    for cid, entry in costumes.items():
        out[cid] = {k: (_wrap_icon(v) if isinstance(v, str) else v) for k, v in entry.items()}
    return out


def build_avatars_json(characters: dict) -> dict:
    """Reproduce the '/ui/....png' shaped avatars.json from raw characters.json."""
    out = {}
    for cid, entry in characters.items():
        new_entry = dict(entry)
        if "Consts" in new_entry:
            new_entry["Consts"] = [_wrap_icon(x) for x in new_entry["Consts"]]
        if "Skills" in new_entry:
            new_entry["Skills"] = _wrap_skill_map(new_entry["Skills"])
        if "SideIconName" in new_entry:
            new_entry["SideIconName"] = _wrap_icon(new_entry["SideIconName"])
        if "Costumes" in new_entry:
            new_entry["Costumes"] = _wrap_costumes(new_entry["Costumes"])
        out[cid] = new_entry
    return out


async def fetch_json(session: aiohttp.ClientSession, path: str):
    url = f"{BASE}/{path}"
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
        resp.raise_for_status()
        return await resp.json(content_type=None)


async def main() -> None:
    async with aiohttp.ClientSession() as session:
        print("Fetching characters.json ...")
        characters = await fetch_json(session, "characters.json")

        print("Fetching namecards.json ...")
        namecards = await fetch_json(session, "namecards.json")

    print("Rebuilding avatars.json from characters.json ...")
    avatars = build_avatars_json(characters)

    written = {
        "char.json": characters,
        "data.json": namecards,
        "avatars.json": avatars,
        # new.json is intentionally excluded - see module docstring.
    }

    for filename, payload in written.items():
        out_path = HERE / filename
        before_count = None
        if out_path.exists():
            try:
                before_count = len(json.loads(out_path.read_text(encoding="utf-8")))
            except Exception:
                pass
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        after_count = len(payload)
        delta = f" ({before_count} -> {after_count})" if before_count is not None else f" ({after_count} entries)"
        print(f"Wrote {filename}{delta}")

    print("Done.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except aiohttp.ClientError as exc:
        print(f"Network error while updating data files: {exc}", file=sys.stderr)
        sys.exit(1)

# ---------------------------------------------------------------------------
# Automating this instead of running it by hand
# ---------------------------------------------------------------------------
#
# 1) Cron, once a day (Enka usually has new-patch data within a day of a
#    Genshin update going live, sometimes sooner via CBT dataminers):
#
#       0 6 * * * cd /path/to/bot && /usr/bin/python3 data/update_data.py >> update_data.log 2>&1
#
# 2) Or run it once at bot startup, before the client logs in - add near the
#    top of bot.py's startup path:
#
#       import subprocess
#       subprocess.run([sys.executable, "data/update_data.py"], check=False)
#
# Either way: if a brand-new character (e.g. Odette, Alyosha) still isn't
# showing up right after this runs, it means EnkaNetwork itself hasn't
# published that character yet - check
# https://github.com/EnkaNetwork/API-docs/commits/master/store/characters.json
# for the latest update timestamp before assuming the bot is broken.