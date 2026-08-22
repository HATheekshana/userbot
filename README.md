# Genshin Userbot

A Telegram **userbot** (runs on your own account, not a separate bot account)
that generates Genshin Impact character cards, Spiral Abyss / Stygian
Onslaught / Imaginarium Theater reports, and a real-time resin/commissions
readout - triggered by commands you type in any chat.

Everything is driven by environment variables, so anyone can clone this repo,
plug in their own credentials, and run their own copy without touching code.

---

## Commands

| Command | What it does |
|---|---|
| `!show <character>` | Generates a character card (stats, weapon, artifacts) |
| `!myc` | Interactive button picker to browse all characters |
| `!change` / `!change classic` / `!change futuristic` | Switch card art style |
| `!add_splash <character>` (reply to an image) | Set a custom splash art for a character |
| `!abyss` / `!abyss previous` | Spiral Abyss report |
| `!stygian` | Stygian Onslaught report |
| `!theater` | Imaginarium Theater report |
| `!note` | Resin, commission status, expeditions |
| `!switch` | Toggle between UID1 / UID2 |
| `!uid` | Show which UID is currently active |

---

## Requirements

- Python 3.10+
- A Telegram account (this logs in **as you**, not as a bot - see the security
  note below)
- A HoYoLAB account (for `!abyss`, `!stygian`, `!theater`, `!note`)
- A host to run it on 24/7 - these instructions cover [Wispbyte](https://wispbyte.com),
  but any VPS or Python-capable panel works the same way

---

## 1. Get your Telegram API credentials

1. Go to <https://my.telegram.org> and log in with the Telegram account you
   want the bot to run as.
2. Open **API Development Tools**, create an app (any name/description is
   fine), and copy the **App api_id** and **App api_hash**.

## 2. Generate a session string (do this once, locally)

Running the bot needs a logged-in session. Generating it locally means you
never have to do an interactive login on your host.

```bash
git clone https://github.com/<your-username>/<your-repo>.git
cd <your-repo>
pip install -r requirements.txt
python generate_session.py
```

It will prompt for your `API_ID`, `API_HASH`, phone number, and login code
(and 2FA password if enabled), then print a long `SESSION_STRING`. Save it -
you'll paste it into your `.env` in the next step.

## 3. Get your HoYoLAB cookies

1. Log into <https://www.hoyolab.com> in a desktop browser.
2. Open DevTools (F12) -> **Application** tab -> **Cookies** ->
   `https://www.hoyolab.com`.
3. Copy the values of `ltuid_v2` and `ltoken_v2`.

These act like a login token for your account - treat them like a password.
If you play on Miyoushe (CN) instead of overseas HoYoLAB, also set
`HOYOLAB_REGION=cn`.

## 4. Configure your `.env`

Copy `.env.example` to `.env` and fill in everything you gathered above:

```bash
cp .env.example .env
```

| Variable | Required | Notes |
|---|---|---|
| `API_ID`, `API_HASH` | yes | from step 1 |
| `UID1` | yes | your main Genshin UID |
| `UID2` | no | a second UID, toggled with `!switch` |
| `SESSION_STRING` | yes | from step 2 |
| `LTUID_V2`, `LTOKEN_V2` | yes (for HoYoLAB commands) | from step 3 |
| `HOYOLAB_REGION` | no | set to `cn` only for Miyoushe accounts |

**`.env` is in `.gitignore` - never commit it, and never paste its contents
anywhere public.** Anyone with `SESSION_STRING`, `LTUID_V2`, or `LTOKEN_V2`
has full access to the corresponding account.

## 5. Refresh the game-data files (recommended)

The character/namecard data snapshots in `data/` go stale after new Genshin
patches. Refresh them before first run:

```bash
python data/update_data.py
```

---

## Deploying on Wispbyte

1. Create a new server/app in the Wispbyte panel using a **Python** egg/type.
2. Upload the project (or connect the GitHub repo, if your plan supports
   Git-based deploys) so the panel's working directory is this repo's root.
3. Set the **Startup Command** to:
   ```
   python bot.py
   ```
4. In the panel's **Startup > Environment Variables** tab, add every variable
   from `.env.example` (API_ID, API_HASH, UID1, UID2, SESSION_STRING,
   LTUID_V2, LTOKEN_V2, HOYOLAB_REGION) with your real values. Do **not**
   upload your local `.env` file - the panel's env var UI is the equivalent
   of it on the host, and keeps secrets out of any file the panel might
   expose.
5. Make sure the panel installs `requirements.txt` on deploy (most Python
   eggs do this automatically on start/install - check your egg's build
   command if not).
6. Start the server. Check the console log - `bot.py` logs whether it found
   `SESSION_STRING` and will exit with a clear error naming any missing
   required variable.

The bot auto-retries in-process on a crash (see `main()` in `bot.py`) and
exits cleanly after 5 failed attempts so Wispbyte's own restart policy can
take over for anything longer-lived (e.g. a host reboot).

---

## Project layout

```
bot.py                  Entry point - Pyrogram userbot, command handlers, startup/retry loop
generate_session.py     One-off local script to produce a SESSION_STRING
requirements.txt
.env.example            Template for your own .env (copy, don't commit the real one)

cards/                  Everything that renders a Telegram-bound image
  character_card.py             Classic character card (Enka showcase + live HoYoLAB fallback)
  futuristic_character_card.py  Alternate "futuristic" theme for the same card
  hoyolab_character_detail.py   HoYoLAB /character/detail client + build-column renderer
  artifacts.py                  Classic artifact row renderer, shared by both card styles
  artifacts_futuristic.py       Futuristic-theme artifact row renderer
  futuristic_theme.py           Shared colors/panels/fx for the futuristic theme
  talents_futuristic.py         Futuristic-theme talent/constellation column
  abyss_card.py                 !abyss report renderer
  stygian_card.py                !stygian report renderer
  theater_card.py               !theater report renderer

services/               Everything that talks to an external API, no image rendering
  net.py                 Shared aiohttp session + DNS-error detection helper
  ranking.py             akasha.cv leaderboard lookups
  abyss.py               HoYoLAB Spiral Abyss data + the shared cookie-authenticated genshin.Client
  stygian.py             HoYoLAB Stygian Onslaught data (reuses abyss.py's client)
  theater.py             HoYoLAB Imaginarium Theater data (reuses abyss.py's client)
  notes.py               HoYoLAB real-time notes (resin / commissions / expeditions) - !note

data/                   Static game-data snapshots + the script that refreshes them
  char.json, data.json, avatars.json, new.json   EnkaNetwork data snapshots
  update_data.py         Re-pulls char.json/data.json/avatars.json from EnkaNetwork
  custom_splash/         User-uploaded splash art from !add_splash (created at runtime, gitignored)

assets/                 Fonts, icons, backgrounds, constellation art used by the card renderers
```

---

## Keeping data fresh

`data/char.json`, `data/data.json`, and `data/avatars.json` are point-in-time
snapshots from EnkaNetwork, so a brand-new patch character won't render until
you refresh them:

```bash
python data/update_data.py
```

Automate it with a daily cron job on your host, or run it once at bot
startup - see the comment block at the bottom of `data/update_data.py` for
both snippets.

---

## Security & account-safety notes

- **This is a userbot** - it logs into your personal Telegram account, not a
  separate bot account created via @BotFather. Telegram's own Terms of
  Service restrict automating a personal account; use at your own risk and
  keep it to commands you trigger yourself in private/small chats rather
  than mass-automating anything.
- Treat `SESSION_STRING`, `LTUID_V2`, and `LTOKEN_V2` as full account
  credentials. Regenerate them (log out other sessions / change your
  HoYoLAB password) if you ever suspect they leaked.
- `.gitignore` already excludes `.env` and `data/custom_splash/` - double
  check `git status` before your first commit to be sure nothing sensitive
  is staged.

## License

MIT - see [LICENSE](LICENSE). Fork it, modify it, run your own instance.
