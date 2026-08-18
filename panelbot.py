"""Group F verification engine: query the @affiliatepocketbot panel bot over a
Telethon user session. Sending the bare {uid} returns a text record we parse for
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
# Ceiling on a whole lookup. The conversation timeout above only covers waiting
# for the reply - resolving the panel entity and acquiring the lock can hang on
# their own, and a hung holder would block every later lookup forever. wait_for
# cancels the coroutine, which releases the lock on the way out.
HARD_TIMEOUT = REPLY_TIMEOUT + 10

# Raw panel replies carry trader UIDs and deposit amounts, and Railway log
# retention is longer than anyone remembers. Default to a redacted form that
# keeps the field LABELS - which is the part you actually need to spot a reply
# format change - and masks every digit. Set PANEL_LOG_RAW=1 to log verbatim
# while actively debugging, then unset it.
LOG_RAW = os.getenv("PANEL_LOG_RAW", "").strip().lower() in ("1", "true", "yes", "on")
RAW_LOG_LIMIT = 1000

_client = None
_enabled = False
_lock = asyncio.Lock()
_last = 0.0


class PanelUnavailable(Exception):
    """Raised when the panel can't be reached (disabled / timeout / silent)."""


def _env(name):
    """Read a Railway variable, tolerating copy-paste damage.

    A StringSession is ~350 base64 chars, so it gets pasted rather than typed,
    and it routinely arrives with a trailing newline or wrapping quotes.
    StringSession() base64-decodes its argument, so one stray character raises
    inside start() and lands in the catch-all below - verification then sits
    disabled with only a generic traceback to show for it. None of these
    characters are in the base64 alphabet, so stripping them is lossless.
    """
    v = os.getenv(name)
    if v is None:
        return None
    v = v.strip().strip("\"'").strip()
    return v or None


async def start():
    """Connect the Telethon user session. If creds/session are missing or the
    session isn't authorized, log it and leave verification disabled."""
    global _client, _enabled
    api_id = _env("TELEGRAM_API_ID")
    api_hash = _env("TELEGRAM_API_HASH")
    session = _env("TELETHON_SESSION")
    # Name the offending variable(s). The old message listed all three whichever
    # one was missing, which is what made this hard to pin down.
    missing = [n for n, v in (("TELEGRAM_API_ID", api_id),
                              ("TELEGRAM_API_HASH", api_hash),
                              ("TELETHON_SESSION", session)) if not v]
    if missing:
        log.error("verification DISABLED - Railway variable(s) not set: %s. "
                  "Every lookup will raise PanelUnavailable('disabled') and the "
                  "user will see MSG_DELAYED. Generate the session with "
                  "gen_session.py, then set it on the service.", ", ".join(missing))
        return
    if not api_id.isdigit():
        # int(api_id) below would otherwise raise into the catch-all and be
        # reported as a generic start failure.
        log.error("verification DISABLED - TELEGRAM_API_ID must be all digits, got %r", api_id)
        return
    try:
        client = TelegramClient(StringSession(session), int(api_id), api_hash)
        await client.connect()
        if not await client.is_user_authorized():
            log.error("verification DISABLED - TELETHON_SESSION is not authorized. "
                      "The session was revoked, expired, or was generated against a "
                      "different TELEGRAM_API_ID. Regenerate it with gen_session.py.")
            await client.disconnect()
            return
        me = await client.get_me()
        _client = client
        _enabled = True
        log.info("panelbot connected as %s - verification ENABLED",
                 getattr(me, "username", None) or getattr(me, "id", "?"))
        # Non-fatal reachability probe. Resolving the handle at boot surfaces a
        # wrong PANEL_BOT, or an account that never pressed Start on the panel,
        # right here instead of as a 20s REPLY_TIMEOUT on the first real user.
        # Deliberately cannot disable verification - _enabled is already set.
        try:
            await client.get_entity(PANEL_BOT)
            log.info("panel bot %s resolved OK", PANEL_BOT)
        except Exception as e:
            log.warning("panel bot %s did not resolve (%s) - lookups will probably "
                        "time out; check the handle and make sure the session "
                        "account has pressed Start on it", PANEL_BOT, e)
    except Exception:
        log.exception("verification DISABLED - panelbot start failed (malformed "
                      "TELETHON_SESSION, wrong api_id/api_hash pair, or network error)")
        _client = None
        _enabled = False


def available():
    return _enabled and _client is not None


def _log_body(text):
    """Render a panel reply for the log: redacted unless PANEL_LOG_RAW is set,
    truncated either way. Redaction masks digits only, so 'Sum of deposits:
    $75.00' becomes 'Sum of deposits: $##.##' - labels intact, values gone."""
    body = text if LOG_RAW else re.sub(r"\d", "#", text)
    if len(body) > RAW_LOG_LIMIT:
        body = body[:RAW_LOG_LIMIT] + "...[truncated]"
    return body


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


def _clean(v):
    # The panel wraps values in `backticks` / **bold**; strip that off.
    if v is None:
        return None
    return v.strip().strip("`* ").strip()


def _field(text, label):
    # Capture the value after "Label:" to end of line, minus markdown wrappers.
    m = re.search(re.escape(label) + r"\s*[:\-]?\s*(.+)", text, re.I)
    return _clean(m.group(1)) if m else None


def _amount(text, label):
    # _num keeps only digits/.,, so $ signs and backticks are tolerated.
    return _num(_field(text, label))


def _parse(text, uid):
    if not text:
        return None
    if re.search(r"not\s*found|no\s*such|no\s*user|no\s*data|unknown\s*(?:user|uid)", text, re.I):
        return None
    cf = _field(text, "Campaign ID")
    m = re.search(r"\d+", cf) if cf else None
    campaign_id = m.group(0) if m else None
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


async def _query(uid):
    """Send the bare uid and return the panel bot's raw reply text. Serialized
    with a lock + spacing. Always call through lookup_trader's timeout."""
    global _last
    async with _lock:
        wait = SPACING - (time.monotonic() - _last)
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            async with _client.conversation(PANEL_BOT, timeout=REPLY_TIMEOUT) as conv:
                # The panel answers a BARE uid. "/user {uid}" was an unverified
                # assumption from the original implementation and gets no reply;
                # confirmed manually against the live panel on 2026-08-18.
                cmd = str(uid)
                log.info("PANEL SEND uid=%s -> %s : %r", uid, PANEL_BOT, cmd)
                await conv.send_message(cmd)
                resp = await conv.get_response()
                text = resp.text or ""
                # The reply is the only way to tell a genuine not-found from a
                # changed reply format. Redacted by default - see _log_body.
                log.info("PANEL REPLY uid=%s chars=%d redacted=%s body=%r",
                         uid, len(text), not LOG_RAW, _log_body(text))
                return text
        except asyncio.TimeoutError:
            log.warning("PANEL TIMEOUT uid=%s - command was sent but no reply within "
                        "%ss. Check that the session account can message %s.",
                        uid, REPLY_TIMEOUT, PANEL_BOT)
            raise PanelUnavailable("timeout")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # Classify by class NAME rather than importing telethon error classes:
            # a symbol that does not exist in the installed telethon version would
            # raise at import time and take the whole bot down with it (bot.py:11
            # imports this module at startup).
            name = type(e).__name__
            if name == "FloodWaitError":
                log.error("PANEL FLOOD_WAIT uid=%s - Telegram wants %ss before the "
                          "next request. Deferring to the retry worker; do not retry "
                          "in a tight loop.", uid, getattr(e, "seconds", "?"))
                raise PanelUnavailable("floodwait")
            if name in ("AuthKeyUnregisteredError", "SessionRevokedError",
                        "UserDeactivatedError", "UserDeactivatedBanError",
                        "AuthKeyDuplicatedError", "UnauthorizedError"):
                log.error("PANEL SESSION DEAD uid=%s - %s. TELETHON_SESSION is no "
                          "longer valid; every lookup fails until it is regenerated.",
                          uid, name)
                raise PanelUnavailable("session")
            log.exception("PANEL ERROR uid=%s - %s", uid, name)
            raise PanelUnavailable("error")
        finally:
            _last = time.monotonic()


async def lookup_trader(uid):
    """Query the panel bot for `uid`.

    Returns a parsed dict on a found record, or None if the panel says
    not-found. Raises PanelUnavailable if the panel can't be reached (disabled,
    timeout, or error) so callers can defer to the retry worker. Serialized with
    a lock + spacing; caches the snapshot into the traders table.
    """
    if not available():
        raise PanelUnavailable("disabled")
    try:
        text = await asyncio.wait_for(_query(uid), timeout=HARD_TIMEOUT)
    except asyncio.TimeoutError:
        log.warning("panel lookup for uid %s exceeded the %ss ceiling - giving up",
                    uid, HARD_TIMEOUT)
        raise PanelUnavailable("timeout")

    info = _parse(text, str(uid))
    if info is None:
        # Two very different things collapse to None here: the panel genuinely
        # said not-found, or none of the labels _parse looks for were present.
        # A relabelled reply is indistinguishable at this point and the caller
        # will tell the user MSG_WRONG_LINK - a confident wrong verdict. Compare
        # against the PANEL REPLY line above to tell them apart.
        log.info("PANEL PARSE uid=%s -> None (not-found, OR the reply no longer "
                 "carries the expected labels: 'Campaign ID' / 'Sum of deposits' "
                 "/ 'UID')", uid)
        return None
    log.info("PANEL PARSE uid=%s -> campaign_id=%s sum_deposits=%s ftd=%s verified=%s",
             uid, info.get("campaign_id"), info.get("sum_deposits"),
             info.get("ftd_amount"), info.get("verified"))
    try:
        dep = info.get("sum_deposits") or Decimal(0)
        await db.cache_trader(str(uid), dep, "panel:campaign=" + str(info.get("campaign_id")))
    except Exception:
        log.exception("cache_trader failed for uid %s", uid)
    return info
