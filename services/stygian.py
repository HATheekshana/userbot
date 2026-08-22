"""
HoYoLAB Stygian Onslaught lookups, authenticated via cookies.

Same auth story as abyss.py: Stygian Onslaught results are private per-account
data, so this needs the same LTUID_V2 / LTOKEN_V2 cookies (set up once,
reused by both !abyss and !stygian).

Unlike Spiral Abyss and Imaginarium Theater, genshin.py's client method for
this is just `get_stygian_onslaught` (NOT `get_genshin_stygian_onslaught` -
there's no "genshin_" in the name for this one). It returns a *list* of
`genshin.models.HardChallenge`, one per season the API considers currently
valid (in practice this is almost always a single-item list). Each
HardChallenge has a `.season` and separate `.single_player` / `.multi_player`
buckets, each with their own `best_record` and `challenges`.

`normalize_stygian` below flattens all of that into the same plain-dict shape
stygian_card.py expects, the same way abyss.py's caller flattens
`SpiralAbyss` - so the card renderer never has to know about genshin.py's
model classes.
"""

import logging

from services.abyss import _get_client  # reuse the same cookie-authenticated client

logger = logging.getLogger("genshin_userbot")

_ROMAN = ["", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"]


def _roman(n):
    try:
        n = int(n)
    except (TypeError, ValueError):
        return str(n)
    if 0 < n < len(_ROMAN):
        return _ROMAN[n]
    return str(n)


async def get_stygian(uid):
    """Returns the raw list[genshin.models.HardChallenge] for the given uid."""
    client = _get_client()
    try:
        return await client.get_stygian_onslaught(int(uid))
    except AttributeError as error:
        raise RuntimeError(
            "Your installed genshin.py doesn't support Stygian Onslaught yet. "
            "Run `pip install -U genshin` and try again."
        ) from error


def _character_dict(character):
    return {
        "icon": character.icon,
        "level": character.level,
        "element": character.element,
        "rarity": character.rarity,
        "constellation": character.constellation,
    }


def _best_character_value(challenge, best_type):
    """HardChallengeChallenge.best_characters is a small list of "best" entries
    tagged STRIKE (strongest single hit) or DAMAGE (highest total damage);
    `.value` comes back as a string, so this also handles the int conversion.
    """
    for best in challenge.best_characters:
        if best.type == best_type:
            try:
                return int(float(best.value))
            except (TypeError, ValueError):
                return 0
    return 0


def _challenge_dict(challenge):
    import genshin  # local import: only needed for the BestCharacterType enum

    return {
        "boss_name": challenge.name,
        "time_elapsed": challenge.time_used or 0,
        "team": [_character_dict(c) for c in challenge.team[:4]],
        "boss_icon": challenge.enemy.icon if challenge.enemy else "",
        "boss_level": challenge.enemy.level if challenge.enemy else 90,
        "strongest_strike": _best_character_value(
            challenge, genshin.models.HardChallengeBestCharacterType.STRIKE
        ),
        "highest_total_damage": _best_character_value(
            challenge, genshin.models.HardChallengeBestCharacterType.DAMAGE
        ),
    }


def normalize_stygian(uid, stygian_list):
    """Flattens the list[HardChallenge] genshin.py returns into the plain
    dict shape stygian_card.py renders. Returns None if there's no Stygian
    Onslaught data for this cycle yet (no seasons, or no single-player runs
    logged in the current one).
    """
    if not stygian_list:
        return None

    # The API only returns seasons it considers currently valid, which in
    # practice is a single entry - but pick the latest by start date just in
    # case more than one comes back.
    current = max(stygian_list, key=lambda item: item.season.start_at)

    single = current.single_player
    if not single or not single.has_data or not single.challenges:
        return None

    best_record = single.best_record

    return {
        "uid": uid,
        "period_start": current.season.start_at.strftime("%Y.%m.%d"),
        "period_end": current.season.end_at.strftime("%Y.%m.%d"),
        "mode": "Single-Player Mode",
        "difficulty_label": _roman(best_record.difficulty) if best_record else "",
        "best_record_seconds": best_record.time_used if best_record else 0,
        "stages": [_challenge_dict(c) for c in single.challenges],
    }
