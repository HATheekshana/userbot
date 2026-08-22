"""
HoYoLAB real-time notes lookup (resin, commissions, expeditions), authenticated
via cookies - same auth as abyss.py, since this is also private per-account
data that HoYoLAB only serves to a logged-in session.
Reuses the LTUID_V2 / LTOKEN_V2 / HOYOLAB_REGION setup documented in abyss.py.
"""
import datetime
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
                "LTUID_V2 and LTOKEN_V2 are required for !note. "
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


async def get_notes(uid):
    """Returns a genshin.models.Notes for the given uid."""
    client = _get_client()
    return await client.get_genshin_notes(int(uid))


def format_notes(notes):
    lines = [f"⚡ Resin: <b>{notes.current_resin}/{notes.max_resin}</b>"]

    if notes.current_resin < notes.max_resin:
        lines.append(f"⏳ Full in: {_format_timedelta(notes.resin_recovery_time)}")

    # genshin.py names this field differently across versions - handle both
    # so a library bump doesn't silently break the command.
    claimed = getattr(notes, "claimed_commission_reward", None)
    if claimed is None:
        claimed = (
            notes.max_commissions > 0
            and notes.completed_commissions >= notes.max_commissions
        )
    status = "✅ Claimed" if claimed else "❌ Not claimed"
    lines.append(
        f"📜 Commissions: {notes.completed_commissions}/{notes.max_commissions} ({status})"
    )

    if getattr(notes, "expeditions", None):
        finished = sum(1 for expedition in notes.expeditions if expedition.finished)
        lines.append(f"🧭 Expeditions: {finished}/{len(notes.expeditions)} finished")

    return "\n".join(lines)


def _format_timedelta(value):
    if isinstance(value, datetime.datetime):
        # This genshin.py version returns an absolute timestamp for when
        # resin (etc.) will be full, rather than a duration - convert to
        # a duration relative to now before formatting.
        now = datetime.datetime.now(value.tzinfo) if value.tzinfo else datetime.datetime.now()
        delta = value - now
        total_seconds = max(0, int(delta.total_seconds()))
    else:
        total_seconds = max(0, int(value.total_seconds()))

    hours, minutes = divmod(total_seconds // 60, 60)
    return f"{hours}h {minutes}m" if hours else f"{minutes}m"