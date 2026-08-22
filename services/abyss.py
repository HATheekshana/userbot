"""
HoYoLAB Spiral Abyss lookups, authenticated via cookies.

Unlike the character card (which pulls public Enka showcase data - no login
needed) or ranking.py (public akasha.cv data), Spiral Abyss stats are
private per-account data. HoYoLAB only serves them to whoever's cookies are
making the request, so this needs a logged-in session's cookies rather than
just a UID.

Setup:
- Log into https://www.hoyolab.com and obtain the `ltuid_v2` and
  `ltoken_v2` cookie values.
- Put their values in `.env` as `LTUID_V2` and `LTOKEN_V2`.
- If it's a CN/Miyoushe account rather than overseas HoYoLAB, also set
  `HOYOLAB_REGION=cn` in .env (defaults to overseas otherwise).

Uses the `genshin` package (genshin.py) rather than hand-rolling HoYoLAB's
request-signing (the "DS" header), which is undocumented and changes
without notice - same reasoning as using cloudscraper for akasha.cv instead
of reimplementing its Cloudflare challenge.
"""

import logging
import os

import genshin

logger = logging.getLogger("genshin_userbot")

_client = None


def _get_client():
    global _client
    if _client is None:
        ltuid = (os.getenv("LTUID_V2") or "").strip()
        ltoken = (os.getenv("LTOKEN_V2") or "").strip()
        if not ltuid or not ltoken:
            raise RuntimeError(
                "LTUID_V2 and LTOKEN_V2 are required for !abyss. "
                "Add both values to .env or Wispbyte's environment variables."
            )
        region = (
            genshin.types.Region.CHINESE
            if os.getenv("HOYOLAB_REGION", "os").strip().lower() == "cn"
            else genshin.types.Region.OVERSEAS
        )
        _client = genshin.Client(
            cookies={"ltuid_v2": ltuid, "ltoken_v2": ltoken},
            region=region,
            game=genshin.types.Game.GENSHIN,
            lang="en-us",
        )
    return _client


async def get_abyss(uid, previous=False):
    """Returns a genshin.models.SpiralAbyss for the given uid.

    previous=True fetches last season's results instead of the current one
    (useful right after a season just ended, before the new one has data).
    """
    client = _get_client()
    return await client.get_genshin_spiral_abyss(int(uid), previous=previous)
