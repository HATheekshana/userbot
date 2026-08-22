"""
Direct akasha.cv ranking lookups.

This used to be a call to a separately-hosted API
(https://test-xehj.onrender.com/get/ranking/{uid}), which meant the bot had
a hard dependency on a second service staying deployed and awake on Render.

This module does the same lookup in-process instead, using `cloudscraper` to
get past Cloudflare directly against akasha.cv. That removes the Render
dependency entirely, at the cost of the bot itself now paying the (one-time,
cached) Cloudflare-challenge cost.

Notes:
- A single scraper session is created lazily on first use and reused after
  that - a userbot handles one command at a time, so there's no need for the
  small connection pool the old hosted API used for concurrent requests.
- `cloudscraper` is a *synchronous* library, so calls run in a thread via
  `asyncio.to_thread` to avoid blocking Pyrogram's event loop.
- If the session looks stale (e.g. its Cloudflare clearance expired), it's
  discarded and re-created once before giving up.
"""

import asyncio
import logging
import time

import cloudscraper

logger = logging.getLogger("genshin_userbot")

_scraper = None
_lock = asyncio.Lock()

# akasha.cv only recalculates a uid's rankings when its refresh endpoint is
# explicitly hit - getCalculationsForUser alone just returns whatever was
# last calculated, which can go stale as soon as you change gear/artifacts.
# akasha.cv itself rate-limits refreshes per-uid (its own UI shows a ~1-2min
# cooldown), so we mirror that here with a small per-uid timer to avoid
# hammering it with a refresh on every single !show call.
_REFRESH_COOLDOWN_SECONDS = 90
_last_refresh_at = {}  # uid (str) -> monotonic timestamp of last refresh attempt


def _create_scraper():
    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "desktop": True}
    )
    # Prime it against akasha.cv once so the Cloudflare challenge is solved
    # up front rather than on the first real lookup.
    scraper.get("https://akasha.cv", timeout=60)
    return scraper


def _refresh_sync(scraper, uid):
    """Ask akasha.cv to recalculate this uid's builds before we read them.

    Best-effort: akasha.cv rate-limits this per-uid (server-side cooldown,
    separate from our own client-side one above), so a rejection here isn't
    fatal - we just fall back to whatever calculations currently exist.
    """
    url = f"https://akasha.cv/api/user/refresh/{uid}"
    try:
        response = scraper.post(url, timeout=30)
        if response.status_code not in (200, 201, 202, 204):
            logger.info(
                "akasha.cv refresh for uid=%s returned status %s (likely on cooldown "
                "server-side); continuing with existing calculations.",
                uid, response.status_code,
            )
    except Exception as error:
        logger.info("akasha.cv refresh for uid=%s failed (%s); continuing with existing calculations.", uid, error)


def _maybe_refresh_sync(scraper, uid):
    uid = str(uid)
    now = time.monotonic()
    last = _last_refresh_at.get(uid)
    if last is not None and (now - last) < _REFRESH_COOLDOWN_SECONDS:
        return
    _last_refresh_at[uid] = now
    _refresh_sync(scraper, uid)


def _fetch_sync(scraper, uid):
    url = f"https://akasha.cv/api/getCalculationsForUser/{uid}"
    response = scraper.get(url, timeout=20)
    if response.status_code != 200:
        raise RuntimeError(f"akasha.cv returned status {response.status_code}")
    return response.json()


def _extract(payload, char_id):
    for char in payload.get("data", []):
        if str(char.get("characterId")) != str(char_id):
            continue
        fit = char.get("calculations", {}).get("fit")
        if not fit:
            return None
        ranking = int(str(fit.get("ranking", 0)).replace("~", ""))
        out_of = int(fit.get("outOf", 0))
        percent = round((ranking / out_of) * 100, 2) if out_of else 0
        return {"ranking": ranking, "outOf": out_of, "percent": percent}
    return None


async def get_ranking(uid, char_id):
    """Returns {"ranking": int, "outOf": int, "percent": float} for the given
    character, or None if the character/UID has no ranking data available."""
    global _scraper

    async with _lock:
        if _scraper is None:
            _scraper = await asyncio.to_thread(_create_scraper)

        await asyncio.to_thread(_maybe_refresh_sync, _scraper, uid)

        try:
            payload = await asyncio.to_thread(_fetch_sync, _scraper, uid)
        except Exception:
            logger.warning("Ranking scraper session looked stale, refreshing once.")
            _scraper = await asyncio.to_thread(_create_scraper)
            payload = await asyncio.to_thread(_fetch_sync, _scraper, uid)

    return _extract(payload, char_id)
