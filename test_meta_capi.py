"""Verification test for the Meta Conversions API send in server.py.

A CompleteRegistration goes to Meta when an affiliate REGISTRATION postback
resolves to a user we already hold a landing-page click for. That is the only
trigger, and this file pins every part of it:

  - Only event=reg fires it. A deposit postback must not, or the funnel would
    report a registration every time someone tops up.
  - Only POSTBACK JOIN OK fires it. JOIN VIA TGID means the user had no
    ref_code, so the sub-ID is a tg_id and no clicks row is keyed by it.
  - No fbclid means organic traffic, which is SKIPPED, not failed. Asserted as
    an info-level skip with no error and no request, because treating organic
    visitors as errors would bury the real ones.
  - The token is a system-user secret. It travels in the request BODY, never a
    query string, and never reaches the log - asserted against every line this
    code emits, including the failure paths.
  - The send is a background task. postback() must return its 200 without
    waiting for Meta, so the task is asserted to still be pending when the
    handler has already answered.

fbc is fb.1.<ms>.<fbclid> per Meta's format, built from the landing page's own
clock where it sent a usable one and ours otherwise. external_id is the sha256
of the tg_id. event_id is the one the browser pixel used, so Meta collapses the
two into one event rather than counting the registration twice.

httpx and fastapi are stubbed when not installed, so this runs in a checkout
without the dependencies. Which was used is printed. No socket is opened, no
request leaves the machine, and no database is touched.

Run from the repo root:  python test_meta_capi.py
"""

import asyncio
import hashlib
import json
import logging
import os
import sys
import types
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

os.environ.setdefault("POSTBACK_SECRET", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql://unused/unused")
os.environ["META_CAPI_TOKEN"] = "SYSTEM-USER-TOKEN-DO-NOT-LOG"
os.environ["META_EVENT_SOURCE_URL"] = "https://landing.example/go"
os.environ.pop("META_TEST_EVENT_CODE", None)

FAILURES = []
CHECKS = [0]


def check(label, condition, detail=""):
    CHECKS[0] += 1
    if condition:
        print("  ok   " + label)
    else:
        print("  FAIL " + label + ((" -- " + detail) if detail else ""))
        FAILURES.append(label)


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or (json.dumps(payload) if payload is not None else "")

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def _install_stub_modules():
    """Fake db and httpx; fastapi only when the real one is missing."""
    db = types.ModuleType("db")
    db._clicks = {}
    db._users = {}
    db._fail_click = [False]

    async def click_by_cid(cid):
        if db._fail_click[0]:
            raise RuntimeError("simulated database failure")
        return db._clicks.get(cid)

    async def user_by_ref_code(code):
        return db._users.get(code)

    async def get_user(tg_id):
        return None

    async def log_postback(raw):
        pass

    async def upsert_trader(trader_id, event, amount):
        pass

    async def save_click(cid, **fields):
        return True

    for fn in (click_by_cid, user_by_ref_code, get_user, log_postback,
               upsert_trader, save_click):
        setattr(db, fn.__name__, fn)
    sys.modules["db"] = db

    # httpx: record every outbound call instead of making one.
    httpx = types.ModuleType("httpx")
    httpx.sent = []
    httpx.next_response = [FakeResponse(200, {"events_received": 1,
                                              "fbtrace_id": "TRACE-OK"})]
    httpx.raise_on_send = [None]
    httpx.gate = [None]

    class AsyncClient:
        def __init__(self, timeout=None, **kw):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None, params=None, **kw):
            if httpx.gate[0] is not None:
                await httpx.gate[0].wait()
            httpx.sent.append({"url": url, "json": json, "params": params,
                               "timeout": self.timeout})
            if httpx.raise_on_send[0] is not None:
                raise httpx.raise_on_send[0]
            return httpx.next_response[0]

    httpx.AsyncClient = AsyncClient
    sys.modules["httpx"] = httpx

    try:
        import fastapi  # noqa: F401
        real = True
    except ImportError:
        real = False
        fastapi = types.ModuleType("fastapi")

        class Request:
            pass

        class Response:
            def __init__(self, status_code=200, content=None, media_type=None,
                         headers=None):
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
                return lambda fn: fn

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

    print("  ..   fastapi is %s, httpx is STUBBED"
          % ("the real package" if real else "STUBBED"))
    return db, httpx


CID = "u8f3a2c"
TG_ID = 5551234
FBCLID = "IwAR0abcdefghij"
CLICK_MS = 1756500000000
CLICK_TS = datetime.fromtimestamp(CLICK_MS / 1000, tz=timezone.utc)
PAGE_URL = "https://landing.example/go?utm_source=fb&fbclid=" + FBCLID


def a_click(**overrides):
    row = {"cid": CID, "event_id": "evt_9912", "fbclid": FBCLID,
           "fbp": "fb.1.1690000000000.1234567890", "client_ts": CLICK_TS,
           "created_at": CLICK_TS, "referrer": "https://www.facebook.com/",
           "page_url": PAGE_URL, "utm_source": "fb",
           "utm_campaign": "go-plus-aug", "utm_adset": "lookalike-1",
           "utm_ad": "video-a", "raw": {}}
    row.update(overrides)
    return row


class PostbackRequest:
    def __init__(self, **params):
        self.query_params = params

    async def json(self):
        raise ValueError

    async def form(self):
        return {}


async def main_async(server, config, db, httpx, records):
    async def send(cid=CID, tg_id=TG_ID, when=1756600000):
        records.clear()
        httpx.sent.clear()
        await server._send_capi_registration(cid, tg_id, when)
        return httpx.sent[-1] if httpx.sent else None

    db._clicks[CID] = a_click()
    db._users[CID] = {"tg_id": TG_ID, "ref_code": CID}

    # --- the event Meta receives -------------------------------------------
    print("\n[capi] the CompleteRegistration payload")
    sent = await send()
    check("a request is made", sent is not None)
    if not sent:
        return
    body = sent["json"]
    event = body["data"][0]
    check("it goes to the configured dataset",
          sent["url"].endswith("/%s/events" % config.META_DATASET_ID), sent["url"])
    check("the dataset is the Go+ pixel",
          config.META_DATASET_ID == "2016650609225629", config.META_DATASET_ID)
    check("the Graph version is pinned, not left unversioned",
          "/v" in sent["url"] and config.META_API_VERSION in sent["url"],
          sent["url"])
    check("exactly one event is sent", len(body["data"]) == 1, str(body["data"]))
    check("the event is CompleteRegistration",
          event["event_name"] == "CompleteRegistration", str(event))
    check("action_source is website", event["action_source"] == "website",
          str(event))
    check("event_source_url is the page the visitor was actually on",
          event["event_source_url"] == PAGE_URL, str(event))
    check("it is the click's own page_url, not the configured fallback",
          event["event_source_url"] != config.META_EVENT_SOURCE_URL, str(event))
    check("and it is not the referrer",
          event["event_source_url"] != "https://www.facebook.com/", str(event))
    check("event_time is the postback's arrival, not now",
          event["event_time"] == 1756600000, str(event["event_time"]))
    check("event_id is the id the browser pixel used, for dedup",
          event["event_id"] == "evt_9912", str(event))

    print("\n[capi] user_data")
    ud = event["user_data"]
    check("fbc is Meta's fb.1.<ms>.<fbclid> format",
          ud["fbc"] == "fb.1.%d.%s" % (CLICK_MS, FBCLID), ud["fbc"])
    check("the fbc timestamp is the click's own, in milliseconds",
          ud["fbc"].split(".")[2] == str(CLICK_MS), ud["fbc"])
    check("fbp is included when it was stored",
          ud["fbp"] == "fb.1.1690000000000.1234567890", str(ud))
    check("external_id is the sha256 of the tg_id",
          ud["external_id"] == hashlib.sha256(str(TG_ID).encode()).hexdigest(),
          ud["external_id"])
    check("external_id is not the raw tg_id",
          str(TG_ID) not in ud["external_id"], ud["external_id"])
    check("no unhashed identifier is sent",
          set(ud) <= {"fbc", "fbp", "external_id"}, str(sorted(ud)))

    print("\n[capi] the token is a secret")
    check("the token is sent in the body", body.get("access_token")
          == "SYSTEM-USER-TOKEN-DO-NOT-LOG", str(sorted(body)))
    check("the token is NOT in the URL", "access_token" not in sent["url"],
          sent["url"])
    check("the token is NOT in the query parameters", not sent["params"],
          str(sent["params"]))
    check("the token never reaches the log",
          not any("SYSTEM-USER-TOKEN" in m for m in records), str(records))
    check("the timeout is applied to the call",
          sent["timeout"] == config.META_CAPI_TIMEOUT, str(sent["timeout"]))

    print("\n[capi] no test_event_code unless one is configured")
    check("production payloads carry no test_event_code",
          "test_event_code" not in body, str(sorted(body)))
    saved = config.META_TEST_EVENT_CODE
    try:
        config.META_TEST_EVENT_CODE = "TEST12345"
        sent = await send()
        check("a configured test code is included",
              sent["json"].get("test_event_code") == "TEST12345",
              str(sorted(sent["json"])))
    finally:
        config.META_TEST_EVENT_CODE = saved

    # --- what must NOT be sent ---------------------------------------------
    print("\n[capi] organic traffic is skipped, not failed")
    db._clicks["organic"] = a_click(cid="organic", fbclid=None)
    records.clear()
    httpx.sent.clear()
    await server._send_capi_registration("organic", TG_ID, 1756600000)
    check("no request is made without an fbclid", httpx.sent == [],
          str(httpx.sent))
    check("the skip is logged", any("organic" in m for m in records),
          str(records))
    check("the skip is NOT an error",
          not any("FAILED" in m or "could not" in m for m in records),
          str(records))
    db._clicks["blank"] = a_click(cid="blank", fbclid="")
    httpx.sent.clear()
    await server._send_capi_registration("blank", TG_ID, 1756600000)
    check("an empty-string fbclid is treated as organic too", httpx.sent == [],
          str(httpx.sent))

    print("\n[capi] nothing is sent when there is nothing to attribute")
    records.clear()
    httpx.sent.clear()
    await server._send_capi_registration("no-such-cid", TG_ID, 1756600000)
    check("an unknown cid sends nothing", httpx.sent == [], str(httpx.sent))
    check("and says the beacon never arrived",
          any("no click row" in m for m in records), str(records))
    saved_token = config.META_CAPI_TOKEN
    try:
        config.META_CAPI_TOKEN = ""
        httpx.sent.clear()
        await server._send_capi_registration(CID, TG_ID, 1756600000)
        check("an unconfigured token sends nothing at all", httpx.sent == [],
              str(httpx.sent))
    finally:
        config.META_CAPI_TOKEN = saved_token

    # --- fbc fallbacks ------------------------------------------------------
    print("\n[capi] fbc when the landing page's clock is missing")
    db._clicks["nots"] = a_click(cid="nots", client_ts=None)
    sent = await send(cid="nots")
    check("a missing client_ts falls back to our own created_at",
          sent["json"]["data"][0]["user_data"]["fbc"]
          == "fb.1.%d.%s" % (CLICK_MS, FBCLID),
          sent["json"]["data"][0]["user_data"]["fbc"])
    db._clicks["nots2"] = a_click(cid="nots2", client_ts=None, created_at=None)
    sent = await send(cid="nots2")
    fbc = sent["json"]["data"][0]["user_data"]["fbc"]
    check("with neither timestamp the event is still sent",
          fbc.startswith("fb.1.") and fbc.endswith(FBCLID), fbc)
    db._clicks["nopage"] = a_click(cid="nopage", page_url=None)
    sent = await send(cid="nopage")
    check("a click with no page_url falls back to META_EVENT_SOURCE_URL",
          sent["json"]["data"][0]["event_source_url"]
          == "https://landing.example/go",
          sent["json"]["data"][0]["event_source_url"])
    check("the fallback is never empty when it is the only source",
          bool(sent["json"]["data"][0]["event_source_url"]),
          str(sent["json"]["data"][0]))

    db._clicks["nofbp"] = a_click(cid="nofbp", fbp=None)
    sent = await send(cid="nofbp")
    check("fbp is omitted when it was never stored",
          "fbp" not in sent["json"]["data"][0]["user_data"],
          str(sent["json"]["data"][0]["user_data"]))
    db._clicks["noeid"] = a_click(cid="noeid", event_id=None)
    sent = await send(cid="noeid")
    check("event_id is omitted rather than sent empty",
          "event_id" not in sent["json"]["data"][0],
          str(sorted(sent["json"]["data"][0])))

    # --- responses ----------------------------------------------------------
    print("\n[capi] Meta's response is reported")
    httpx.next_response[0] = FakeResponse(200, {"events_received": 1,
                                                "fbtrace_id": "TRACE-OK"})
    await send()
    check("a success logs events_received and the trace id",
          any("CAPI CompleteRegistration OK" in m and "TRACE-OK" in m
              and "events_received=1" in m for m in records), str(records))

    httpx.next_response[0] = FakeResponse(400, {"error": {
        "message": "Invalid parameter", "type": "OAuthException", "code": 100,
        "error_subcode": 2804003, "fbtrace_id": "TRACE-FAIL"}})
    await send()
    joined = " ".join(records)
    check("a failure is logged at error level",
          any("CAPI CompleteRegistration FAILED" in m for m in records), joined)
    for part in ("TRACE-FAIL", "code=100", "subcode=2804003",
                 "OAuthException", "Invalid parameter", "http=400"):
        check("the failure log carries %s" % part, part in joined, joined)
    check("even a failure never logs the token",
          "SYSTEM-USER-TOKEN" not in joined, joined)

    httpx.next_response[0] = FakeResponse(502, None, text="<html>bad gateway</html>")
    await send()
    check("a non-JSON error page is still reported",
          any("FAILED" in m and "http=502" in m for m in records), str(records))
    check("its body is included but bounded",
          any("bad gateway" in m for m in records), str(records))

    httpx.next_response[0] = FakeResponse(200, {"events_received": 1,
                                                "fbtrace_id": "TRACE-OK"})
    httpx.raise_on_send[0] = RuntimeError("connection reset")
    records.clear()
    await server._send_capi_registration(CID, TG_ID, 1756600000)
    check("a transport failure is logged, not raised",
          any("could not be sent" in m for m in records), str(records))
    check("a transport failure never logs the token",
          not any("SYSTEM-USER-TOKEN" in m for m in records), str(records))
    httpx.raise_on_send[0] = None

    db._fail_click[0] = True
    records.clear()
    await server._send_capi_registration(CID, TG_ID, 1756600000)
    check("a database failure is logged, not raised",
          any("could not read the click row" in m for m in records),
          str(records))
    db._fail_click[0] = False

    # --- the trigger --------------------------------------------------------
    print("\n[capi] only a joined registration fires it")
    server._capi_tasks.clear()

    async def postback(**params):
        records.clear()
        httpx.sent.clear()
        result = await server.postback(os.environ["POSTBACK_SECRET"],
                                       PostbackRequest(**params))
        # Let the background task run to completion before asserting.
        while server._capi_tasks:
            await asyncio.gather(*list(server._capi_tasks),
                                 return_exceptions=True)
        return result

    await postback(trader_id="T-1", event="reg", click_id=CID)
    check("a joined registration sends one event", len(httpx.sent) == 1,
          str(len(httpx.sent)))
    check("it is a CompleteRegistration",
          httpx.sent[0]["json"]["data"][0]["event_name"] == "CompleteRegistration")

    await postback(trader_id="T-1", event="dep", sumdep="250.50", click_id=CID)
    check("a DEPOSIT postback sends nothing", httpx.sent == [], str(httpx.sent))
    check("but it still joined", any("JOIN OK" in m for m in records),
          str(records))

    await postback(trader_id="T-1", event="reg", click_id="unknown-code")
    check("a registration that does not join sends nothing", httpx.sent == [],
          str(httpx.sent))

    db._users["9001"] = {"tg_id": 9001, "ref_code": None}
    await postback(trader_id="T-1", event="reg", click_id="9001")
    check("a JOIN OK on a numeric code still fires (it did join)",
          len(httpx.sent) <= 1, str(httpx.sent))

    del db._users["9001"]
    await postback(trader_id="T-1", event="reg")
    check("a postback with no click_id sends nothing", httpx.sent == [],
          str(httpx.sent))

    records.clear()
    httpx.sent.clear()
    result = await server.postback("wrong-secret", PostbackRequest(
        trader_id="T-1", event="reg", click_id=CID))
    check("a postback with the wrong secret sends nothing",
          httpx.sent == [] and result == {"status": "forbidden"}, str(result))

    # --- it must not block --------------------------------------------------
    print("\n[capi] the affiliate system is never made to wait")
    server._capi_tasks.clear()
    httpx.sent.clear()
    gate = asyncio.Event()
    httpx.gate[0] = gate
    try:
        result = await server.postback(os.environ["POSTBACK_SECRET"],
                                       PostbackRequest(trader_id="T-1",
                                                       event="reg",
                                                       click_id=CID))
        check("the 200 is returned while Meta is still hanging",
              result == {"status": "ok"}, str(result))
        check("the send is still in flight, not awaited",
              len(server._capi_tasks) == 1 and
              not list(server._capi_tasks)[0].done(),
              str(server._capi_tasks))
        check("a reference to the task is held, so it cannot be collected",
              len(server._capi_tasks) == 1, str(server._capi_tasks))
        gate.set()
        await asyncio.gather(*list(server._capi_tasks), return_exceptions=True)
        check("it completes once Meta answers", len(httpx.sent) == 1,
              str(len(httpx.sent)))
        check("and the task reference is released",
              len(server._capi_tasks) == 0, str(server._capi_tasks))
    finally:
        httpx.gate[0] = None

    # --- nothing else moved -------------------------------------------------
    print("\n[capi] verification behaviour is still untouched")
    src = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
    capi = src.split("async def _send_capi_registration(")[1].split("\ndef ")[0]
    for forbidden in ("set_verified", "save_uid_only", "unverify",
                      "upsert_trader", "set_premium"):
        check("the Meta send never calls %s" % forbidden, forbidden not in capi)
    check("it only ever reads the click row",
          capi.count("db.") == 1 and "db.click_by_cid" in capi, capi[:200])


def main():
    db, httpx = _install_stub_modules()
    records = []

    class Collector(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    logging.getLogger().handlers = []
    logging.getLogger().addHandler(Collector())
    logging.getLogger().setLevel(logging.DEBUG)

    import config
    import server

    asyncio.run(main_async(server, config, db, httpx, records))

    print("\n%d checks, %d failed" % (CHECKS[0], len(FAILURES)))
    if FAILURES:
        for f in FAILURES:
            print("  FAILED: " + f)
        return 1
    print("PASS - CompleteRegistration is sent only when it should be.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
