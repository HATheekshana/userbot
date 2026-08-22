"""
Run this ONCE on your own computer (not on Wispbyte) to log in to Telegram
interactively and generate a SESSION_STRING.

Wispbyte's console can't reliably handle Pyrogram's interactive phone/OTP
prompts, and free containers can lose their filesystem on redeploy, which
would wipe a file-based session and force you to log in again. Generating a
string session once and pasting it into a SESSION_STRING environment
variable in the Wispbyte panel avoids both problems.

Usage:
    1. pip install pyrogram tgcrypto python-dotenv
    2. Put API_ID and API_HASH in a local .env file (same values you'll use
       on Wispbyte), or just enter them when prompted below.
    3. python generate_session.py
    4. Follow the phone number / login code / 2FA prompts.
    5. Copy the printed string and set it as SESSION_STRING in the
       Wispbyte panel's environment variables (Startup tab).
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from pyrogram import Client

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")

api_id = os.getenv("API_ID") or input("API_ID: ").strip()
api_hash = os.getenv("API_HASH") or input("API_HASH: ").strip()

with Client("session_generator", api_id=int(api_id), api_hash=api_hash, in_memory=True) as app:
    session_string = app.export_session_string()

print("\n=== Copy everything between the lines below into SESSION_STRING ===")
print(session_string)
print("=== End of session string ===\n")
