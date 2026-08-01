"""One-off LOCAL script: generate a Telethon StringSession for the verification
account (the user session that queries the panel bot). Never run on the server.

Usage (locally):
    pip install telethon
    TELEGRAM_API_ID=... TELEGRAM_API_HASH=... python gen_session.py

It will ask for the account's phone number (with country code, e.g. +1...),
then the login code Telegram sends to that account (and your 2FA password if
one is set), and finally print the TELETHON_SESSION value to paste into Railway.
"""
import os
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

api_id = os.environ.get("TELEGRAM_API_ID") or input("api_id: ").strip()
api_hash = os.environ.get("TELEGRAM_API_HASH") or input("api_hash: ").strip()

with TelegramClient(StringSession(), int(api_id), api_hash) as client:
    me = client.get_me()
    print("\nLogged in as:", getattr(me, "username", None) or getattr(me, "first_name", "?"),
          "(id", str(getattr(me, "id", "?")) + ")")
    print("\n=== TELETHON_SESSION (copy the entire line below) ===")
    print(client.session.save())
    print("=== end ===")
