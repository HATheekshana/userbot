"""
HoYoLAB Imaginarium Theater lookups, authenticated via cookies.

Same auth story as abyss.py and stygian.py: Imaginarium Theater results are
private per-account data, so this needs the same LTUID_V2 / LTOKEN_V2
cookies (set up once, reused by !abyss, !stygian, and !theater alike).

genshin.py's client method for this is `get_genshin_imaginarium_theater`
(the "genshin_" prefix is there for this one, unlike get_stygian_onslaught -
see the note in stygian.py). It returns a single
`genshin.models.ImgTheaterData` for the most recent theater cycle.
"""

import logging

from services.abyss import _get_client  # reuse the same cookie-authenticated client

logger = logging.getLogger("genshin_userbot")


async def get_theater(uid):
    """Returns the raw genshin.models.ImgTheaterData for the given uid."""
    client = _get_client()
    try:
        return await client.get_imaginarium_theater(int(uid))
    except AttributeError as error:
        raise RuntimeError(
            "Your installed genshin.py doesn't support Imaginarium Theater yet. "
            "Run `pip install -U genshin` and try again."
        ) from error