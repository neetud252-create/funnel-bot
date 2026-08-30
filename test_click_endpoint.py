"""Verification test for POST /click, the landing-page beacon in server.py.

/click is the only PUBLIC, UNAUTHENTICATED WRITE path in this service, so most
of what is asserted here is about what it refuses rather than what it stores:

  - cid must match config.REF_CODE_RE, the same pattern bot.py applies to the
    /start deep-link payload. The two halves share one definition on purpose -
    a cid that the endpoint accepts but the bot would reject is a click that
    can never be joined to a user - and this file fails if they drift apart.
  - Nothing from the request body ever reaches the log verbatim. The cid is
    logged only after it has matched; everything else is reported as a length,
    a type or a count. Crafted input is asserted absent from the log.
  - The per-IP limiter keys on the RIGHTMOST X-Forwarded-For entry. The
    leftmost is client-supplied and forgeable, and keying on it would let one
    caller mint a fresh bucket per request. A forged leftmost is asserted not
    to defeat the limit.
  - The insert is ON CONFLICT DO NOTHING, so the first write for a cid wins.
    That is what stops anyone who learns a cid from overwriting the real click
    behind it, and it is asserted here rather than left to the SQL.

The response is 204 on success because the caller is sendBeacon, which
discards the body. 400 and 429 are returned rather than folded into 204 so the
endpoint stays observable from outside; the browser ignores them either way.

Both transports are asserted, because both must survive the same abuse:

  - POST with a raw body, never inspecting the content type. Requiring
    application/json would force a CORS preflight, and a navigation landing
    between the OPTIONS and the POST loses the beacon.
  - GET with the payload flattened into query parameters, fired as an image.
    Never preflighted, not subject to CORS at all, and issued by the browser
    rather than the document, so it outlives the navigation that started it.

_store_click is the single place a click is validated or written, and this file
asserts the two paths agree: same cid rule, same limiter budget, same columns
from the same beacon.

This test drives the real handler with a fake database. It opens no socket,
runs no migration and writes nothing.

Run from the repo root:  python test_click_endpoint.py
"""

import asyncio
import json
import logging
import os
import sys
import types
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

# server.py reads POSTBACK_SECRET at import and would otherwise stop the
# process; CLICK_ORIGIN is set so the CORS branch is the one under test.
os.environ.setdefault("POSTBACK_SECRET", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql://unused/unused")
LANDING_ORIGIN = "https://landing.example"
os.environ["CLICK_ORIGIN"] = LANDING_ORIGIN


FAILURES = []
CHECKS = [0]


def check(label, condition, detail=""):
    CHECKS[0] += 1
    if condition:
        print("  ok   " + label)
    else:
        print("  FAIL " + label + ((" -- " + detail) if detail else ""))
        FAILURES.append(label)


def _install_stub_modules():
    """Fake db, plus a stand-in for fastapi when it is not installed.

    db is faked because this test must not touch a database: save_click below
    mirrors the real statement's ON CONFLICT (cid) DO NOTHING, which is the
    behaviour the idempotency and overwrite assertions depend on.

    fastapi is real when the environment has it (it is in requirements.txt and
    is what runs in production). The stub exists so a checkout without the
    dependency installed still runs this file rather than erroring out - the
    handler is plain async code and none of it is framework-specific. Which of
    the two was used is printed, so a stubbed run is never mistaken for a real
    one.
    """
    db = types.ModuleType("db")
    db._clicks = {}
    db._fail = [False]

    async def save_click(cid, **fields):
        # Mirrors: INSERT ... ON CONFLICT (cid) DO NOTHING RETURNING cid.
        if db._fail[0]:
            raise RuntimeError("simulated database failure")
        if cid in db._clicks:
            return False
        db._clicks[cid] = fields
        return True

    async def click_by_cid(cid):
        return db._clicks.get(cid)

    # The postback path shares this module; these keep it callable so the
    # "nothing else moved" section can exercise it.
    async def log_postback(raw):
        pass

    async def upsert_trader(trader_id, event, amount):
        db._last_upsert = (trader_id, event, amount)

    async def user_by_ref_code(code):
        return None

    async def get_user(tg_id):
        return None

    for fn in (save_click, click_by_cid, log_postback, upsert_trader,
               user_by_ref_code, get_user):
        setattr(db, fn.__name__, fn)
    sys.modules["db"] = db

    try:
        import fastapi  # noqa: F401
        real = True
    except ImportError:
        real = False
        fastapi = types.ModuleType("fastapi")

        class Request:
            pass

        class Response:
            def __init__(self, status_code=200, content=None,
                         media_type=None, headers=None):
                self.status_code = status_code
                self.content = content
                self.media_type = media_type
                self.headers = headers or {}

        class FastAPI:
            def __init__(self, *a, **k):
                self.user_middleware = []

            def add_middleware(self, cls, **kw):
                self.user_middleware.append((cls, kw))

            def _register(self, *a, **k):
                def deco(fn):
                    return fn
                return deco

            get = post = api_route = _register

        fastapi.Request = Request
        fastapi.Response = Response
        fastapi.FastAPI = FastAPI
        middleware = types.ModuleType("fastapi.middleware")
        cors = types.ModuleType("fastapi.middleware.cors")

        class CORSMiddleware:
            def __init__(self, *a, **k):
                pass

        cors.CORSMiddleware = CORSMiddleware
        middleware.cors = cors
        sys.modules["fastapi"] = fastapi
        sys.modules["fastapi.middleware"] = middleware
        sys.modules["fastapi.middleware.cors"] = cors

    print("  ..   fastapi is %s" % ("the real package" if real else "STUBBED"))
    return db


class FakeRequest:
    """Enough of a Starlette request for the handler: a body and headers."""

    def __init__(self, body, ip="203.0.113.7", xff=None, ctype="text/plain"):
        self._body = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.headers = {"content-type": ctype}
        if xff is not None:
            self.headers["x-forwarded-for"] = xff
        self.client = types.SimpleNamespace(host=ip)

    async def body(self):
        return self._body


class FakeGetRequest:
    """A query-string beacon: GET /click?cid=...&utm_source=..."""

    def __init__(self, params, ip="203.0.113.7", xff=None):
        self.query_params = {k: str(v) for k, v in params.items()
                             if v is not None}
        self.headers = {}
        if xff is not None:
            self.headers["x-forwarded-for"] = xff
        self.client = types.SimpleNamespace(host=ip)


def as_query(body):
    """The same beacon a POST would send, flattened the way an image tag is."""
    flat = {k: v for k, v in body.items() if k != "utm"}
    for field, value in (body.get("utm") or {}).items():
        flat["utm_" + field] = value
    return flat


# The beacon exactly as the landing page sends it. ts is epoch milliseconds
# from Date.now(), which is what the page actually produces.
BEACON = {
    "cid": "u8f3a2c",
    "event_id": "evt_9912",
    "fbclid": "IwAR0abcdefghij",
    "fbp": "fb.1.1690000000000.1234567890",
    "ts": 1756500000000,
    "referrer": "https://www.facebook.com/",
    "utm": {"source": "fb", "campaign": "go-plus-aug",
            "adset": "lookalike-1", "ad": "video-a"},
}

OPTIONAL_COLUMNS = ("event_id", "fbclid", "fbp", "client_ts", "referrer",
                    "utm_source", "utm_campaign", "utm_adset", "utm_ad")


async def main_async(server, config, db, records):
    server_src = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()

    def beacon(**overrides):
        return dict(BEACON, **overrides)

    async def post(body, keep_rate=False, **kw):
        # The limiter is process-wide and real. Sections that are not testing
        # it reset it first, or the 21st request in this file would collect a
        # 429 that has nothing to do with what is being asserted.
        records.clear()
        if not keep_rate:
            server._click_hits.clear()
        return await server.click_post(FakeRequest(body, **kw))

    # --- the documented beacon ---------------------------------------------
    print("\n[click/post] the beacon the landing page sends")
    r = await post(BEACON)
    check("a well-formed beacon is answered 204", r.status_code == 204,
          str(r.status_code))
    check("the 204 carries no body", getattr(r, "content", None) is None,
          repr(getattr(r, "content", None)))
    row = db._clicks.get("u8f3a2c")
    check("it is stored under its cid", row is not None, str(sorted(db._clicks)))
    if row:
        check("event_id is kept", row["event_id"] == "evt_9912", str(row))
        check("fbclid is kept", row["fbclid"] == "IwAR0abcdefghij", str(row))
        check("fbp is kept", row["fbp"] == "fb.1.1690000000000.1234567890",
              str(row))
        check("referrer is kept", row["referrer"] == "https://www.facebook.com/",
              str(row))
        check("all four utm fields land in their own columns",
              (row["utm_source"], row["utm_campaign"], row["utm_adset"],
               row["utm_ad"]) == ("fb", "go-plus-aug", "lookalike-1", "video-a"),
              str(row))
        check("the whole body is kept in raw, not just the columns",
              row["raw"] == BEACON, str(row["raw"])[:120])

    # --- ts is epoch milliseconds ------------------------------------------
    print("\n[click] the landing page's clock")
    if row:
        check("epoch milliseconds parse to an aware datetime",
              isinstance(row["client_ts"], datetime)
              and row["client_ts"].tzinfo is not None, repr(row["client_ts"]))
        check("Date.now() milliseconds are not read as seconds",
              row["client_ts"].year == 2025, repr(row["client_ts"]))
    for name, value in (("epoch seconds", 1756500000),
                        ("an ISO-8601 string", "2025-08-30T12:00:00Z")):
        await post(beacon(cid="ts_ok_%d" % len(name), ts=value))
        got = db._clicks["ts_ok_%d" % len(name)]["client_ts"]
        check("%s is also accepted" % name,
              got is not None and got.year == 2025 and got.tzinfo is not None,
              repr(got))
    for name, value in (("a word", "yesterday"), ("a boolean", True),
                        ("an absent value", None), ("an object", {"a": 1}),
                        ("an absurd number", 1e30)):
        cid = "ts_bad_%d" % len(name)
        await post(beacon(cid=cid, ts=value))
        check("%s is stored as NULL rather than guessed" % name,
              db._clicks[cid]["client_ts"] is None,
              repr(db._clicks[cid]["client_ts"]))
    check("created_at is the database's own clock, not the client's",
          "created_at   TIMESTAMPTZ DEFAULT now()" in
          open(os.path.join(ROOT, "db.py"), encoding="utf-8").read())

    # --- sendBeacon's content type -----------------------------------------
    print("\n[click/post] no preflight: the content type is never inspected")
    for name, ctype in (("text/plain", "text/plain;charset=UTF-8"),
                        ("an absent content-type", ""),
                        ("application/json", "application/json")):
        cid = "ct_%d" % len(name)
        r = await post(beacon(cid=cid), ctype=ctype)
        check("a beacon sent as %s is accepted" % name,
              r.status_code == 204 and cid in db._clicks, str(r.status_code))

    # --- first write wins ---------------------------------------------------
    print("\n[click] the first write for a cid wins")
    r = await post(beacon(event_id="OVERWRITTEN", fbclid="ATTACKER"))
    check("a repeated cid is still answered 204", r.status_code == 204,
          str(r.status_code))
    check("the stored row is NOT overwritten",
          db._clicks["u8f3a2c"]["event_id"] == "evt_9912",
          str(db._clicks["u8f3a2c"]))
    check("the repeat is logged as a duplicate",
          any("duplicate, first write kept" in m for m in records), str(records))

    # --- cid validation -----------------------------------------------------
    print("\n[click] cid must be exactly what a deep-link payload may be")
    # Identity, not equality: two separately-compiled but equal-looking
    # patterns in two modules is exactly the drift this file exists to catch.
    check("the endpoint validates with config's pattern, not a copy",
          server.config.REF_CODE_RE is config.REF_CODE_RE)
    check("bot.py validates the deep-link payload with the same object",
          "REF_RE = config.REF_CODE_RE" in
          open(os.path.join(ROOT, "bot.py"), encoding="utf-8").read())
    rejected = {
        "an absent cid": {k: v for k, v in BEACON.items() if k != "cid"},
        "an empty cid": beacon(cid=""),
        "a 65-character cid": beacon(cid="a" * 65),
        "a cid with a dot": beacon(cid="ab.cd"),
        "a cid with a space": beacon(cid="ab cd"),
        "a path-traversal cid": beacon(cid="../../etc/passwd"),
        "a SQL-shaped cid": beacon(cid="x'; DROP TABLE clicks;--"),
        "an HTML-shaped cid": beacon(cid="<script>alert(1)</script>"),
        "a non-ASCII cid": beacon(cid="ééé"),
        "a numeric cid": beacon(cid=12345),
        "a null cid": beacon(cid=None),
        "an object cid": beacon(cid={"a": 1}),
    }
    before = len(db._clicks)
    for name, body in rejected.items():
        r = await post(body)
        check("%s is refused with 400" % name, r.status_code == 400,
              str(r.status_code))
    check("not one rejected cid wrote a row", len(db._clicks) == before,
          "%d rows" % len(db._clicks))
    r = await post(beacon(cid="a" * 64))
    check("a 64-character cid is accepted (the boundary is inclusive)",
          r.status_code == 204, str(r.status_code))
    r = await post(beacon(cid="A-Za-z0-9_-"))
    check("every character class in the pattern is accepted",
          r.status_code == 204, str(r.status_code))

    # --- log hygiene --------------------------------------------------------
    print("\n[click] no raw request content ever reaches the log")
    records.clear()
    await server.click_post(FakeRequest(beacon(cid="<script>alert(1)</script>",
                                          referrer="javascript:alert(1)",
                                          event_id="'; DROP TABLE clicks;--")))
    joined = " ".join(records)
    for needle in ("<script>", "alert(1)", "javascript:", "DROP TABLE"):
        check("%r never appears in the log" % needle, needle not in joined,
              joined[:200])
    check("a rejected cid is reported by type and length only",
          "type=str" in joined and "len=" in joined, joined[:200])
    records.clear()
    await server.click_post(FakeRequest(b"{not json", ip="198.51.100.4"))
    check("an unparseable body is reported by size only",
          any("unparseable body" in m and "bytes=9" in m for m in records),
          str(records))
    check("the malformed bytes themselves are not echoed",
          not any("not json" in m for m in records), str(records))
    records.clear()
    await post(BEACON)
    check("a VALID cid is logged, since it has already matched the pattern",
          any("/click cid=u8f3a2c" in m for m in records), str(records))
    check("but the body's other fields are not",
          not any("fbclid" in m or "IwAR" in m or "facebook" in m
                  for m in records), str(records))

    # --- body shape and size ------------------------------------------------
    print("\n[click] the body is bounded and must be an object")
    oversized = json.dumps(beacon(cid="toobig",
                                  referrer="x" * (config.CLICK_MAX_BYTES + 100)))
    r = await post(oversized.encode())
    check("an oversized body is refused with 400", r.status_code == 400,
          str(r.status_code))
    check("an oversized body is never stored", "toobig" not in db._clicks)
    check("the size and the cap are logged, not the body",
          any("oversized body" in m for m in records), str(records))
    for name, raw in (("an array", b"[1,2,3]"), ("a bare string", b'"hello"'),
                      ("a number", b"42"), ("null", b"null"),
                      ("an empty body", b"")):
        r = await post(raw)
        check("%s is refused with 400" % name, r.status_code == 400,
              str(r.status_code))
    r = await post(b'{"cid":"bare"}')
    check("a cid with no other fields is still stored", r.status_code == 204,
          str(r.status_code))
    check("its optional columns are all NULL",
          all(db._clicks["bare"][c] is None for c in OPTIONAL_COLUMNS),
          str(db._clicks["bare"]))

    # --- utm --------------------------------------------------------------
    print("\n[click] utm is read defensively")
    r = await post(beacon(cid="utm_str", utm="not-an-object"))
    check("a non-object utm does not fail the request", r.status_code == 204,
          str(r.status_code))
    check("and leaves the utm columns NULL",
          all(db._clicks["utm_str"][c] is None for c in
              ("utm_source", "utm_campaign", "utm_adset", "utm_ad")),
          str(db._clicks["utm_str"]))
    await post(beacon(cid="utm_deep",
                      utm={"source": {"deep": 1}, "campaign": 5, "ad": None}))
    check("non-string utm values become NULL, never str(dict)",
          all(db._clicks["utm_deep"][c] is None for c in
              ("utm_source", "utm_campaign", "utm_ad")),
          str(db._clicks["utm_deep"]))
    long_ad = "z" * 500
    await post(beacon(cid="utm_long", utm=dict(BEACON["utm"], ad=long_ad)))
    check("an over-long utm value is truncated, not rejected",
          0 < len(db._clicks["utm_long"]["utm_ad"]) < len(long_ad),
          str(len(db._clicks["utm_long"]["utm_ad"] or "")))

    # --- the image-beacon transport ----------------------------------------
    print("\n[click/get] the same beacon as query parameters")

    async def get(params, keep_rate=False, **kw):
        records.clear()
        if not keep_rate:
            server._click_hits.clear()
        return await server.click_get(FakeGetRequest(params, **kw))

    r = await get(as_query(beacon(cid="g_full")))
    check("a query-string beacon is accepted", r.status_code == 200,
          str(r.status_code))
    check("it answers with an image, so onload fires",
          r.media_type == "image/gif", str(r.media_type))
    check("the image is a real GIF", (r.content or b"")[:6] == b"GIF89a",
          repr((r.content or b"")[:6]))
    check("the pixel is never cached, or a repeat beacon is never sent",
          "no-store" in (r.headers or {}).get("Cache-Control", ""),
          str(r.headers))
    row = db._clicks.get("g_full")
    check("the query beacon is stored", row is not None, str(sorted(db._clicks)))
    if row:
        check("flat utm_* parameters fill the same four columns",
              (row["utm_source"], row["utm_campaign"], row["utm_adset"],
               row["utm_ad"]) == ("fb", "go-plus-aug", "lookalike-1", "video-a"),
              str(row))
        check("the Meta fields survive the query transport",
              (row["event_id"], row["fbclid"], row["fbp"])
              == (BEACON["event_id"], BEACON["fbclid"], BEACON["fbp"]),
              str(row))
        check("Date.now() as a STRING is still read as epoch milliseconds",
              row["client_ts"] is not None and row["client_ts"].year == 2025,
              repr(row["client_ts"]))

    r = await get({"cid": "g_bracket", "utm[source]": "fb",
                   "utm[campaign]": "bracketed"})
    check("the utm[source] spelling is understood when it is the only one",
          (db._clicks["g_bracket"]["utm_source"],
           db._clicks["g_bracket"]["utm_campaign"]) == ("fb", "bracketed"),
          str(db._clicks.get("g_bracket")))
    r = await get({"cid": "g_both", "utm_source": "flat",
                   "utm[source]": "bracketed"})
    check("the flat spelling wins when a beacon sends both",
          db._clicks["g_both"]["utm_source"] == "flat",
          str(db._clicks.get("g_both")))

    print("\n[click/get] the two transports store the same thing")
    await post(beacon(cid="same_post"))
    await get(as_query(beacon(cid="same_get")))
    via_post = dict(db._clicks["same_post"])
    via_get = dict(db._clicks["same_get"])
    via_post.pop("raw"), via_get.pop("raw")
    check("every stored column matches across transports",
          via_post == via_get,
          "%s != %s" % (via_post, via_get))
    check("raw keeps whatever actually arrived on each",
          db._clicks["same_get"]["raw"] != db._clicks["same_post"]["raw"])

    print("\n[click/get] validation is the POST path's, not a second copy")
    check("there is exactly one place a click is validated",
          server_src.count("REF_CODE_RE.match") == 1, server_src)
    check("and exactly one place a click is written",
          server_src.count("db.save_click") == 1, server_src)
    before_bad = len(db._clicks)
    for name, bad_cid in (("a dotted", "ab.cd"), ("an HTML-shaped",
                          "<script>alert(1)</script>"), ("an over-long",
                          "a" * 65), ("an empty", "")):
        r = await get(dict(as_query(BEACON), cid=bad_cid))
        check("%s cid is refused with 400 on the GET path too" % name,
              r.status_code == 400, str(r.status_code))
        check("a refused GET returns no pixel to load",
              getattr(r, "content", None) is None, repr(getattr(r, "content", None)))
    r = await get({k: v for k, v in as_query(BEACON).items() if k != "cid"})
    check("a GET with no cid at all is refused", r.status_code == 400,
          str(r.status_code))
    check("not one bad GET wrote a row", len(db._clicks) == before_bad,
          "%d rows" % len(db._clicks))

    records.clear()
    await server.click_get(FakeGetRequest(
        dict(as_query(BEACON), cid="<script>alert(1)</script>",
             referrer="javascript:alert(1)")))
    joined = " ".join(records)
    for needle in ("<script>", "alert(1)", "javascript:"):
        check("%r never reaches the log from a GET either" % needle,
              needle not in joined, joined[:200])
    check("the log says which transport carried the rejection",
          "via get" in joined, joined[:200])

    r = await get(dict(as_query(BEACON), cid="g_big",
                       referrer="x" * (config.CLICK_MAX_BYTES + 100)))
    check("an oversized query string is refused with 400",
          r.status_code == 400, str(r.status_code))
    check("and stores nothing", "g_big" not in db._clicks)

    print("\n[click/get] first write wins across transports")
    await post(beacon(cid="cross"))
    r = await get(dict(as_query(BEACON), cid="cross", event_id="OVERWRITTEN"))
    check("a GET cannot overwrite a click a POST already stored",
          db._clicks["cross"]["event_id"] == BEACON["event_id"],
          str(db._clicks["cross"]))
    check("and is still answered with the pixel", r.status_code == 200,
          str(r.status_code))

    # --- rate limiting ------------------------------------------------------
    print("\n[click] the per-IP rate limit")
    server._click_hits.clear()
    limit = config.CLICK_RATE_MAX
    codes = [(await post(beacon(cid="rl%d" % i), keep_rate=True,
                         ip="192.0.2.10")).status_code for i in range(limit + 5)]
    check("the first CLICK_RATE_MAX requests pass",
          codes[:limit] == [204] * limit, str(codes))
    check("every request past the limit is refused with 429",
          set(codes[limit:]) == {429}, str(codes))
    r = await post(beacon(cid="other_ip"), keep_rate=True, ip="192.0.2.99")
    check("a different IP is unaffected by another's limit",
          r.status_code == 204, str(r.status_code))
    check("the limiter logs the address and the limit, never the payload",
          not any("fbclid" in m or "IwAR" in m for m in records), str(records))

    print("\n[click] one budget covers both transports")
    server._click_hits.clear()
    for i in range(limit):
        await get(dict(as_query(BEACON), cid="mix%d" % i), keep_rate=True,
                  ip="192.0.2.30")
    r = await post(beacon(cid="mix_post"), keep_rate=True, ip="192.0.2.30")
    check("a POST cannot take a second budget after GETs spent the first",
          r.status_code == 429, str(r.status_code))
    server._click_hits.clear()
    for i in range(limit):
        await post(beacon(cid="mixp%d" % i), keep_rate=True, ip="192.0.2.31")
    r = await get(dict(as_query(BEACON), cid="mix_get"), keep_rate=True,
                  ip="192.0.2.31")
    check("nor a GET after POSTs spent it", r.status_code == 429,
          str(r.status_code))
    check("the GET path reads the forwarded address the same way",
          server._client_ip(FakeGetRequest({}, ip="10.0.0.1",
                                           xff="1.1.1.1, 2.2.2.2")) == "2.2.2.2")

    print("\n[click] the limiter's key cannot be forged")
    check("the RIGHTMOST forwarded address is the key",
          server._client_ip(FakeRequest({}, ip="10.0.0.1",
                                        xff="1.1.1.1, 2.2.2.2")) == "2.2.2.2")
    check("the socket address is used when no header is present",
          server._client_ip(FakeRequest({}, ip="10.0.0.1")) == "10.0.0.1")
    check("a blank header falls back rather than keying on empty",
          server._client_ip(FakeRequest({}, ip="10.0.0.1", xff="  ")) == "10.0.0.1")
    server._click_hits.clear()
    for i in range(limit):
        await post(beacon(cid="fk%d" % i), keep_rate=True, ip="192.0.2.20",
                   xff="1.1.1.1, 192.0.2.20")
    r = await post(beacon(cid="fk_last"), keep_rate=True, ip="192.0.2.20",
                   xff="a-different-forged-value, 192.0.2.20")
    check("rotating the forgeable leftmost entry does NOT mint a fresh bucket",
          r.status_code == 429, str(r.status_code))

    print("\n[click] the limiter's table is bounded")
    server._click_hits.clear()
    now = 1000.0
    for i in range(config.CLICK_RATE_MAX_IPS + 50):
        server._rate_ok("ip-%d" % i, now)
    check("the table never exceeds CLICK_RATE_MAX_IPS",
          len(server._click_hits) <= config.CLICK_RATE_MAX_IPS,
          str(len(server._click_hits)))
    server._click_hits.clear()
    server._rate_ok("expired", now - config.CLICK_RATE_WINDOW * 2)
    server._rate_ok("live", now)
    server._prune_hits(now)
    check("an expired window is pruned", "expired" not in server._click_hits,
          str(sorted(server._click_hits)))
    check("a live window is kept", "live" in server._click_hits,
          str(sorted(server._click_hits)))

    # --- failure ------------------------------------------------------------
    print("\n[click] a database failure still answers the beacon")
    db._fail[0] = True
    records.clear()
    r = await post(beacon(cid="dbdown"))
    check("the beacon is answered 204 despite the failure",
          r.status_code == 204, str(r.status_code))
    check("the failure is logged with the cid",
          any("could not store cid=dbdown" in m for m in records), str(records))
    db._fail[0] = False

    # --- CORS ---------------------------------------------------------------
    print("\n[click] CORS is scoped to the landing page")
    mw = getattr(server.app, "user_middleware", [])
    entries = [(c, k) for c, k in
               [(getattr(m, "cls", m[0] if isinstance(m, tuple) else None),
                 getattr(m, "kwargs", m[1] if isinstance(m, tuple) else {}))
                for m in mw]]
    check("exactly one middleware is registered", len(entries) == 1,
          str(entries))
    if entries:
        kw = entries[0][1]
        check("only the configured origin is allowed",
              kw.get("allow_origins") == [LANDING_ORIGIN], str(kw))
        check("the origin is never a wildcard",
              "*" not in (kw.get("allow_origins") or []), str(kw))
        check("only the beacon methods and their preflight are allowed",
              kw.get("allow_methods") == ["GET", "POST", "OPTIONS"], str(kw))
    saved_origins = config.CLICK_ORIGINS
    try:
        # An image request is not a CORS request at all, so the GET transport
        # has to keep working with no allowed origin configured - which is the
        # state this service is actually deployed in right now.
        config.CLICK_ORIGINS = []
        r = await get({"cid": "no_cors"})
        check("the image transport works with no CLICK_ORIGIN configured",
              r.status_code == 200 and "no_cors" in db._clicks,
              str(r.status_code))
    finally:
        config.CLICK_ORIGINS = saved_origins

    # --- nothing else moved -------------------------------------------------
    print("\n[click] the postback endpoint is untouched")

    class PostbackRequest:
        query_params = {"trader_id": "T-1", "event": "reg",
                        "click_id": "u8f3a2c"}

        async def json(self):
            raise ValueError

        async def form(self):
            return {}

    result = await server.postback(os.environ["POSTBACK_SECRET"],
                                   PostbackRequest())
    check("a valid postback still returns ok", result == {"status": "ok"},
          str(result))
    result = await server.postback("wrong-secret", PostbackRequest())
    check("a wrong secret is still refused",
          result == {"status": "forbidden"}, str(result))
    check("/click did not become a second postback path",
          "upsert_trader" not in server_src.split("async def click_get(")[1])


def main():
    db = _install_stub_modules()

    records = []

    class Collector(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    logging.getLogger().handlers = []
    logging.getLogger().addHandler(Collector())
    logging.getLogger().setLevel(logging.INFO)

    import config
    import server

    asyncio.run(main_async(server, config, db, records))

    print("\n%d checks, %d failed" % (CHECKS[0], len(FAILURES)))
    if FAILURES:
        for f in FAILURES:
            print("  FAILED: " + f)
        return 1
    print("PASS - /click stores what it should and refuses everything else.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
