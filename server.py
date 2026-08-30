import base64
import json
import logging
import os
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
import config
import db

app = FastAPI()
log = logging.getLogger(__name__)

# Browsers will not POST /click cross-origin without these headers. Added only
# when an origin is configured: an empty list would otherwise register a
# middleware that permits nothing, and the "not configured" case is worth a
# line in the log rather than a silent no-op.
if config.CLICK_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.CLICK_ORIGINS,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["content-type"],
        max_age=600,
    )
else:
    log.warning("CLICK_ORIGIN is not set - no browser origin may POST /click. "
                "Set it to the landing page's origin to enable the endpoint.")

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

# --- /click rate limiting ---------------------------------------------------
# ip -> [window_start_monotonic, count]. In memory on purpose: this guards one
# process, a restart handing out one fresh window is harmless, and a shared
# store would be a dependency the endpoint does not otherwise need.
_click_hits = {}


def _client_ip(request):
    """Best available client address for rate limiting.

    Takes the RIGHTMOST X-Forwarded-For entry, not the leftmost. The leftmost
    is whatever the client itself sent and is trivially forged - keying the
    limiter on it would let one caller mint a fresh bucket per request. The
    rightmost is the one the closest proxy appended, so with a single trusted
    hop in front (Railway's edge) it is the real peer. Falls back to the socket
    address when the header is absent, which is the direct-connection case.
    """
    xff = request.headers.get("x-forwarded-for", "")
    parts = [p.strip() for p in xff.split(",") if p.strip()]
    if parts:
        return parts[-1]
    client = getattr(request, "client", None)
    return getattr(client, "host", None) or "unknown"


def _rate_ok(ip, now):
    entry = _click_hits.get(ip)
    if entry is not None and now - entry[0] < config.CLICK_RATE_WINDOW:
        entry[1] += 1
        return entry[1] <= config.CLICK_RATE_MAX
    if len(_click_hits) >= config.CLICK_RATE_MAX_IPS:
        _prune_hits(now)
    _click_hits[ip] = [now, 1]
    return True


def _prune_hits(now):
    # Expired windows first. If every window is still live the table is under
    # genuine load, so the oldest are evicted rather than turning a memory
    # ceiling into a refusal to serve.
    for k in [k for k, v in _click_hits.items()
              if now - v[0] >= config.CLICK_RATE_WINDOW]:
        _click_hits.pop(k, None)
    if len(_click_hits) >= config.CLICK_RATE_MAX_IPS:
        for k in sorted(_click_hits, key=lambda k: _click_hits[k][0])[
                :len(_click_hits) // 4 or 1]:
            _click_hits.pop(k, None)


def _clip(v, limit):
    # Strings only, truncated. Anything else (a nested object, a number, null)
    # becomes None rather than being coerced: the columns are TEXT and a
    # str(dict) would store something no one can parse back.
    if not isinstance(v, str):
        return None
    v = v.strip()
    return v[:limit] or None


def _parse_ts(v):
    """The landing page's own clock, or None if it did not send a usable one.

    Accepts epoch seconds, epoch milliseconds, or an ISO-8601 string, because
    which of those a page sends depends on how it was written. Anything else
    is None rather than a guess - clicks.created_at is our own clock and is
    what any report should actually trust.
    """
    if isinstance(v, bool) or v is None:
        return None
    try:
        if isinstance(v, (int, float)):
            seconds = float(v)
            # Current epoch seconds are ~1.7e9 and milliseconds ~1.7e12, so
            # anything past 1e11 is milliseconds by any reading.
            if abs(seconds) > 1e11:
                seconds /= 1000.0
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        if isinstance(v, str):
            text = v.strip()
            # A query-string beacon delivers Date.now() as the STRING
            # "1756500000000", so a numeric string is an epoch, not an ISO
            # date. Tried first: fromisoformat would simply fail on it and
            # every GET beacon would store a NULL client_ts.
            try:
                return _parse_ts(float(text))
            except ValueError:
                pass
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None
    return None


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


# A 1x1 transparent GIF. The GET path is fired as `new Image().src = ...`,
# which is what survives a navigation that kills fetch: an image request is
# issued by the browser itself and outlives the document that started it.
# Answering with a real image makes onload fire and keeps the console clean;
# the request is already delivered by the time the body matters at all.
_PIXEL = base64.b64decode(
    b"R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")
_PIXEL_HEADERS = {
    # Without this the browser serves a repeat beacon for the same URL out of
    # cache and never sends the request at all.
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
}

# Query-string spellings of the nested utm object. The flat form is what an
# image beacon builds by hand; the bracket form is what most query-string
# serialisers emit. Both fold back into the same {"utm": {...}} shape the JSON
# body carries, so _store_click cannot tell the two transports apart.
_UTM_FIELDS = ("source", "campaign", "adset", "ad")


def _payload_from_query(params):
    data = dict(params)
    utm = {}
    for field in _UTM_FIELDS:
        value = data.pop("utm_" + field, None)
        if value is None:
            value = data.pop("utm[%s]" % field, None)
        if value is not None:
            utm[field] = value
    if utm:
        data["utm"] = utm
    return data


def _rate_check(request):
    """Shared gate. Returns (ip, None) to proceed or (ip, 429) to refuse.

    Both transports run this before reading anything else, so the limit is one
    budget per IP across GET and POST together - splitting it would let a
    caller take double by alternating.
    """
    ip = _client_ip(request)
    if _rate_ok(ip, time.monotonic()):
        return ip, None
    log.warning("/click rate limited ip=%s (over %s per %ss)",
                ip, config.CLICK_RATE_MAX, config.CLICK_RATE_WINDOW)
    return ip, 429


async def _store_click(data, ip, transport):
    """Validate and store one click, whichever transport carried it.

    Returns 400 when the cid is unusable and 204 when the click was accepted -
    stored, a duplicate, or lost to a database error. The caller cannot act on
    the difference between those three and is not told them apart.

    This is the ONLY place a click is validated or written, so the GET and POST
    paths cannot drift into two different sets of rules.
    """
    cid = data.get("cid")
    if not isinstance(cid, str) or not config.REF_CODE_RE.match(cid):
        # Length and type only. An invalid cid is exactly the input that must
        # not be echoed, since it is the field an attacker controls most
        # directly and the one most likely to be crafted.
        log.warning("/click rejected cid via %s ip=%s type=%s len=%s",
                    transport, ip, type(cid).__name__,
                    len(cid) if isinstance(cid, str) else "n/a")
        return 400

    utm = data.get("utm")
    if not isinstance(utm, dict):
        utm = {}
    try:
        stored = await db.save_click(
            cid,
            event_id=_clip(data.get("event_id"), 128),
            fbclid=_clip(data.get("fbclid"), 512),
            fbp=_clip(data.get("fbp"), 128),
            client_ts=_parse_ts(data.get("ts")),
            referrer=_clip(data.get("referrer"), 1024),
            utm_source=_clip(utm.get("source"), 128),
            utm_campaign=_clip(utm.get("campaign"), 128),
            utm_adset=_clip(utm.get("adset"), 128),
            utm_ad=_clip(utm.get("ad"), 128),
            raw=data,
        )
    except Exception:
        # Accepted even so. The landing page cannot act on a failure and a
        # retry would only repeat it; the exception is on record for us.
        log.exception("/click could not store cid=%s", cid)
        return 204

    log.info("/click cid=%s via=%s ip=%s %s", cid, transport, ip,
             "stored" if stored else "duplicate, first write kept")
    return 204


@app.get("/click")
async def click_get(request: Request):
    """Image-beacon transport: GET /click?cid=...&event_id=...&utm_source=...

    Exists because a POST the page has to negotiate is a POST the page can
    lose. A simple GET is never preflighted, and fired as an image it is not
    subject to CORS at all - nothing on this path depends on CLICK_ORIGIN being
    right, and nothing is left in flight when the document goes away.

    Validation, rate limiting and storage are the POST path's, unchanged; only
    the way the fields arrive differs. Nested utm arrives flattened, which
    _payload_from_query folds back into the shape the JSON body has.
    """
    ip, refused = _rate_check(request)
    if refused:
        return Response(status_code=refused)

    params = dict(request.query_params)
    # Same ceiling as the body, for the same reason. A query string past this
    # is not a beacon, and most front ends would have truncated it anyway.
    size = sum(len(str(k)) + len(str(v)) for k, v in params.items())
    if size > config.CLICK_MAX_BYTES:
        log.warning("/click oversized query ip=%s chars=%d limit=%d",
                    ip, size, config.CLICK_MAX_BYTES)
        return Response(status_code=400)

    status = await _store_click(_payload_from_query(params), ip, "get")
    if status != 204:
        return Response(status_code=status)
    return Response(content=_PIXEL, media_type="image/gif",
                    headers=_PIXEL_HEADERS)


@app.post("/click")
async def click_post(request: Request):
    """Beacon transport: POST /click with a JSON body.

    Kept as a SIMPLE request so no preflight is involved: the body is read raw
    and the content type is never inspected, so the page may send text/plain -
    one of the three types CORS treats as simple - and the browser issues the
    POST directly. sendBeacon does this by default. A page that sends
    application/json instead forces a preflight, and a navigation landing
    between the OPTIONS and the POST loses the beacon entirely; that is what
    the GET path above exists to sidestep.

    Answers 204 with an empty body because the caller discards it. 400 and 429
    are returned rather than folded into 204 so the endpoint is observable from
    the outside - the browser ignores them, but a person testing it does not.

    NOTHING from the request is ever logged verbatim except the cid, and the
    cid only after it has matched config.REF_CODE_RE.
    """
    ip, refused = _rate_check(request)
    if refused:
        return Response(status_code=refused)

    # Read the body ourselves rather than declaring a JSON body parameter.
    # Requiring application/json would both reject the text/plain a simple
    # request must use and force the preflight this endpoint avoids.
    raw = await request.body()
    if len(raw) > config.CLICK_MAX_BYTES:
        log.warning("/click oversized body ip=%s bytes=%d limit=%d",
                    ip, len(raw), config.CLICK_MAX_BYTES)
        return Response(status_code=400)
    try:
        data = json.loads(raw or b"{}")
    except (ValueError, UnicodeDecodeError):
        log.warning("/click unparseable body ip=%s bytes=%d", ip, len(raw))
        return Response(status_code=400)
    if not isinstance(data, dict):
        log.warning("/click body is %s, not an object, ip=%s",
                    type(data).__name__, ip)
        return Response(status_code=400)

    return Response(status_code=await _store_click(data, ip, "post"))
