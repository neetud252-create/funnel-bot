"""One-off LOCAL script: produce the TELETHON_SESSION string for the
verification account (the user session that queries the panel bot). Never run on
the server.

Two paths, tried in order:

  1. CONVERT - if an authorized session.session is already sitting here, it is
     converted straight to a StringSession. No phone number, no login code.
     StringSession.save() only reads dc_id/server_address/port/auth_key off the
     session object, so any session type converts.
  2. LOGIN - otherwise, a fresh interactive login (phone number, then the code
     Telegram sends, then your 2FA password if set).

Usage (locally, in a normal terminal - do NOT redirect output, or you won't see
the phone/code prompts and the run will produce an empty file):
    pip install telethon
    $env:TELEGRAM_API_ID="..."; $env:TELEGRAM_API_HASH="..."; py -3.12 gen_session.py

On success it writes the session to session.txt (gitignored). Paste that value
into the Railway TELETHON_SESSION variable, then delete session.txt.

The api_id/api_hash used here MUST be the same pair set on Railway - a session is
bound to the api_id it was created with.
"""
import os

from telethon.sync import TelegramClient
from telethon.sessions import StringSession

api_id = os.environ.get("TELEGRAM_API_ID") or input("api_id: ").strip()
api_hash = os.environ.get("TELEGRAM_API_HASH") or input("api_hash: ").strip()

session_str = None

if os.path.exists("session.session"):
    print("Found session.session - converting it (no login required)...")
    client = TelegramClient("session", int(api_id), api_hash)
    client.connect()
    try:
        if client.is_user_authorized():
            me = client.get_me()
            session_str = StringSession.save(client.session)
            print("Converted the existing login for: @%s (id=%s)" % (
                getattr(me, "username", None), getattr(me, "id", None)))
        else:
            print("session.session is NOT authorized - falling back to a fresh login.")
    finally:
        client.disconnect()

if session_str is None:
    with TelegramClient(StringSession(), int(api_id), api_hash) as client:
        if not client.is_user_authorized():
            raise SystemExit("Login did not complete - session NOT authorized. "
                             "Re-run and finish the phone+code prompts.")
        me = client.get_me()
        session_str = client.session.save()
        print("Logged in as: @%s (id=%s, name=%s)" % (
            getattr(me, "username", None), getattr(me, "id", None),
            getattr(me, "first_name", None)))

if not session_str:
    raise SystemExit("Empty session string - nothing to write. The session has no "
                     "auth key; re-run and complete the interactive login.")

with open("session.txt", "w", encoding="utf-8") as f:
    f.write(session_str)

print("\nSession authorized: True")
print("Session written to session.txt (%d chars)." % len(session_str))
print("Set it as TELETHON_SESSION on Railway, then delete session.txt.")
print("Paste the value with no surrounding quotes and no trailing newline.")
