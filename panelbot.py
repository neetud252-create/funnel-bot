"""Group F verification engine: query the @affiliatepocketbot panel bot over a
Telethon user session. Sending "/user {uid}" returns a text record we parse for
the fields we care about (Reg date, Verified, FTD amount, Sum of deposits,
Campaign ID/name). Lookups are serialized with a lock + spacing so we don't
hammer the panel. Degrades gracefully: if the session/creds are missing or the
panel is silent, verification is disabled/deferred instead of crashing.
"""
import os
import re
import time
import asyncio
import logging
from decimal import Decimal, InvalidOperation

from telethon import TelegramClient
from telethon.sessions import StringSession

import db

log = logging.getLogger("panelbot")

# Confirmed handle of the affiliate panel bot. Env-overridable so it can be
# corrected without a code change if the exact handle differs.
PANEL_BOT = os.getenv("PANEL_BOT", "@affiliatepocketbot")

REPLY_TIMEOUT = 20   # seconds to wait for the panel bot's reply
SPACING = 2.0        # minimum seconds between consecutive lookups

_client = None
_enabled = False
_lock = asyncio.Lock()
_last = 0.0


class PanelUnavailable(Exception):
    """Raised when the panel can't be reached (disabled / timeout / silent)."""


async def start():
    """Connect the Telethon user session. If creds/session are missing or the
    session isn't authorized, log it and leave verification disabled."""
    global _client, _enabled
    api_id = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")
    session = os.getenv("TELETHON_SESSION")
    if not (api_id and api_hash and session):
        log.warning("TELEGRAM_API_ID/HASH or TELETHON_SESSION not set - verification disabled")
        return
    try:
        client = TelegramClient(StringSession(session), int(api_id), api_hash)
        await client.connect()
        if not await client.is_user_authorized():
            log.error("Telethon session not authorized - verification disabled")
            await client.disconnect()
            return
        me = await client.get_me()
        _client = client
        _enabled = True
        log.info("panelbot connected as %s", getattr(me, "username", None) or getattr(me, "id", "?"))
    except Exception:
        log.exception("panelbot start failed - verification disabled")
        _client = None
        _enabled = False


def available():
    return _enabled and _client is not None


# --- parsing helpers ---
def _num(s):
    if s is None:
        return None
    t = re.sub(r"[^\d.,]", "", str(s))
    if not t:
        return None
    if "," in t and "." in t:
        t = t.replace(",", "")        # 1,234.56 -> 1234.56 (comma = thousands)
    elif "," in t:
        t = t.replace(",", ".")       # 1234,56  -> 1234.56 (comma = decimal)
    try:
        return Decimal(t)
    except InvalidOperation:
        return None


def _field(text, label):
    # "Label: value" tolerating markdown/bold, colon or dash, to end of line.
    m = re.search(re.escape(label) + r"\s*[:\-]?\s*(.+)", text, re.I)
    return m.group(1).strip() if m else None


def _amount(text, label):
    m = re.search(re.escape(label) + r"\s*[:\-]?\s*\$?\s*([0-9][0-9.,]*)", text, re.I)
    return _num(m.group(1)) if m else None


def _parse(text, uid):
    if not text:
        return None
    if re.search(r"not\s*found|no\s*such|no\s*user|no\s*data|unknown\s*(?:user|uid)", text, re.I):
        return None
    m = re.search(r"Campaign\s*ID\s*[:\-]?\s*(\d+)", text, re.I)
    campaign_id = m.group(1) if m else None
    sum_dep = _amount(text, "Sum of deposits")
    # Nothing that identifies a trader record -> treat as not found.
    if campaign_id is None and sum_dep is None and not re.search(r"\bUID\b", text, re.I):
        return None
    ver = _field(text, "Verified")
    verified = bool(ver and re.match(r"(?:yes|true|1|verified)", ver, re.I))
    return {
        "uid": uid,
        "reg_date": _field(text, "Reg date"),
        "verified": verified,
        "ftd_amount": _amount(text, "FTD amount"),
        "sum_deposits": sum_dep,
        "campaign_id": campaign_id,
        "campaign_name": _field(text, "Campaign name"),
        "raw": text,
    }


async def lookup_trader(uid):
    """Query the panel bot for `uid`.

    Returns a parsed dict on a found record, or None if the panel says
    not-found. Raises PanelUnavailable if the panel can't be reached (disabled,
    timeout, or error) so callers can defer to the retry worker. Serialized with
    a lock + spacing; caches the snapshot into the traders table.
    """
    global _last
    if not available():
        raise PanelUnavailable("disabled")
    async with _lock:
        wait = SPACING - (time.monotonic() - _last)
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            async with _client.conversation(PANEL_BOT, timeout=REPLY_TIMEOUT) as conv:
                await conv.send_message("/user " + str(uid))
                resp = await conv.get_response()
                text = resp.text or ""
        except asyncio.TimeoutError:
            log.warning("no reply from panel bot for uid %s within %ss", uid, REPLY_TIMEOUT)
            raise PanelUnavailable("timeout")
        except Exception:
            log.exception("panel lookup failed for uid %s", uid)
            raise PanelUnavailable("error")
        finally:
            _last = time.monotonic()

    info = _parse(text, str(uid))
    if info is None:
        return None
    try:
        dep = info.get("sum_deposits") or Decimal(0)
        await db.cache_trader(str(uid), dep, "panel:campaign=" + str(info.get("campaign_id")))
    except Exception:
        log.exception("cache_trader failed for uid %s", uid)
    return info
