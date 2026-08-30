import logging
import os
from decimal import Decimal, InvalidOperation
from fastapi import FastAPI, Request
import db

app = FastAPI()
log = logging.getLogger(__name__)

# Read at IMPORT time, not per request. bot.py imports this module during
# startup, so a missing value now stops the process at boot with the reason,
# instead of raising KeyError inside every postback and answering the affiliate
# system with a 500 that it will retry forever.
try:
    POSTBACK_SECRET = os.environ["POSTBACK_SECRET"]
except KeyError:
    raise RuntimeError(
        "POSTBACK_SECRET is not set - the postback endpoint cannot be secured. "
        "Set it in the environment before starting the bot.") from None

# The affiliate panel's exact macro names aren't known yet. These key lists are
# best guesses; the raw postback is logged first (before any parsing) so they
# can be corrected once the first real postback arrives. Matching is
# case-insensitive.
TRADER_KEYS = ("trader_id", "traderid", "click_id", "clickid",
               "sub_id", "subid", "uid", "user_id")
EVENT_KEYS = ("event", "status", "goal", "type", "action")
AMOUNT_KEYS = ("sumdep", "amount", "sum", "deposit", "payout", "revenue", "value")
REG_EVENTS = {"reg", "registration", "signup", "lead"}

# The sub-ID specifically, for the attribution probe below. Narrower than
# TRADER_KEYS on purpose: TRADER_KEYS also matches the panel's OWN trader id,
# and the live postbacks send BOTH -
#   ?trader_id={trader_id}&event=reg&click_id={click_id}
#   ?trader_id={trader_id}&event=dep&sumdep={sumdep}&click_id={click_id}
# - so reading the sub-ID off TRADER_KEYS would pick up trader_id, a value we
# never sent, and report a match that means nothing. click_id is the confirmed
# name (it must stay in step with config.REF_SUB_PARAM, the outbound half);
# clickid is the one spelling variant worth tolerating.
SUBID_KEYS = ("click_id", "clickid")


@app.get("/")
@app.get("/health")
async def health():
    return {"ok": True}


def _lower_map(d):
    # Case-insensitive lookup: lowercased key -> value (last wins on collision).
    return {str(k).lower(): v for k, v in d.items()}


def _pick(m, keys):
    for k in keys:
        v = m.get(k)
        if v not in (None, ""):
            return v
    return None


def _norm_amount(v):
    """The running deposit total this postback reports, or None if it reports none.

    None and Decimal(0) are deliberately different: db.upsert_trader writes a
    reported total ABSOLUTELY, so None means "leave the stored total alone"
    while 0 would overwrite it with zero. A missing macro reports no total, and
    so does a malformed one - guessing 0 there would erase a real deposit.
    """
    if v is None:
        return None
    s = str(v).strip().replace(",", ".")
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        log.warning("postback: amount %r is not a number - treated as no total "
                    "reported, stored deposit left unchanged", v)
        return None


@app.api_route("/postback/{secret}", methods=["GET", "POST"])
async def postback(secret: str, request: Request):
    # Wrong secret: 200 so the affiliate system doesn't retry, but do nothing.
    if secret != POSTBACK_SECRET:
        return {"status": "forbidden"}

    # Merge query params + JSON or form body into one dict.
    data = dict(request.query_params)
    try:
        body = await request.json()
        if isinstance(body, dict):
            data.update(body)
    except Exception:
        try:
            form = await request.form()
            data.update({k: v for k, v in form.items()})
        except Exception:
            pass

    # FIRST log the raw dict, before any parsing, so unknown macros are captured.
    try:
        await db.log_postback(data)
    except Exception:
        # Still swallowed - a postback that cannot be archived must not 500 and
        # be retried - but no longer silent: losing the raw row is exactly what
        # makes the macro names below impossible to correct.
        log.exception("postback: raw log failed, continuing with the parse")

    # Best-effort parse. Always return 200 - affiliate systems retry non-200.
    try:
        m = _lower_map(data)
        trader_id = _pick(m, TRADER_KEYS)
        event = _pick(m, EVENT_KEYS)
        amount = _norm_amount(_pick(m, AMOUNT_KEYS))
        if event is not None and str(event).strip().lower() in REG_EVENTS:
            # A registration reports no deposit total. None rather than 0:
            # the total is written absolutely now, and a 0 here would erase
            # whatever an earlier deposit postback stored.
            amount = None
        if trader_id is not None:
            ev = str(event) if event is not None else None
            await db.upsert_trader(str(trader_id), ev, amount)
        await _probe_attribution(m, trader_id, event, amount)
    except Exception:
        # The 200 is still unconditional, but the reason is now on record
        # rather than dropped - a wrong macro name used to look identical to a
        # postback that carried nothing at all.
        log.exception("postback: parse failed for keys=%s", sorted(data))

    return {"status": "ok"}


async def _probe_attribution(m, trader_id, event, amount):
    """Report whether this postback's sub-ID resolves to a users row.

    READ ONLY, and deliberately so: nothing here writes, verifies, or grants
    anything. It exists to prove the sub-ID survives the round trip from the
    outbound REF_LINK to the panel and back before any behaviour is wired to
    it. Its own failures are logged and swallowed - an attribution probe must
    never be the reason a postback is not acknowledged.
    """
    sub_id = _pick(m, SUBID_KEYS)
    if sub_id is None:
        log.info("POSTBACK SUBID ABSENT trader_id=%s event=%s - no key in %s; "
                 "check REF_SUB_PARAM matches the panel's macro",
                 trader_id, event, SUBID_KEYS)
        return
    sub_id = str(sub_id).strip()
    try:
        user = await db.user_by_ref_code(sub_id)
        if user is not None:
            log.info("POSTBACK JOIN OK sub_id=%s -> tg_id=%s event=%s amount=%s",
                     sub_id, user["tg_id"], event, amount)
            return
        # bot.py falls back to the tg_id when a user has no ref_code, so an
        # all-digit sub-ID that matches no code is the expected second case,
        # not a failure. Logged under its own name so the two are countable
        # apart when the first real postbacks arrive.
        if sub_id.isdigit():
            fallback = await db.get_user(int(sub_id))
            if fallback is not None:
                log.info("POSTBACK JOIN VIA TGID sub_id=%s -> tg_id=%s (user had "
                         "no ref_code) event=%s amount=%s",
                         sub_id, fallback["tg_id"], event, amount)
                return
        log.warning("POSTBACK NO MATCH sub_id=%r matches no users.ref_code and no "
                    "tg_id - wrong REF_SUB_PARAM, or the click predates ref_code "
                    "capture", sub_id)
    except Exception:
        log.exception("postback: attribution probe failed for sub_id=%r", sub_id)
