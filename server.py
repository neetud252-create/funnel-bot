import os
from decimal import Decimal, InvalidOperation
from fastapi import FastAPI, Request
import db

app = FastAPI()

# The affiliate panel's exact macro names aren't known yet. These key lists are
# best guesses; the raw postback is logged first (before any parsing) so they
# can be corrected once the first real postback arrives. Matching is
# case-insensitive.
TRADER_KEYS = ("trader_id", "traderid", "click_id", "clickid",
               "sub_id", "subid", "uid", "user_id")
EVENT_KEYS = ("event", "status", "goal", "type", "action")
AMOUNT_KEYS = ("sumdep", "amount", "sum", "deposit", "payout", "revenue", "value")
REG_EVENTS = {"reg", "registration", "signup", "lead"}


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
    if v is None:
        return Decimal(0)
    s = str(v).strip().replace(",", ".")
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return Decimal(0)


@app.api_route("/postback/{secret}", methods=["GET", "POST"])
async def postback(secret: str, request: Request):
    # Wrong secret: 200 so the affiliate system doesn't retry, but do nothing.
    if secret != os.environ["POSTBACK_SECRET"]:
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
        pass

    # Best-effort parse. Always return 200 - affiliate systems retry non-200.
    try:
        m = _lower_map(data)
        trader_id = _pick(m, TRADER_KEYS)
        event = _pick(m, EVENT_KEYS)
        amount = _norm_amount(_pick(m, AMOUNT_KEYS))
        if event is not None and str(event).strip().lower() in REG_EVENTS:
            amount = Decimal(0)
        if trader_id is not None:
            ev = str(event) if event is not None else None
            await db.upsert_trader(str(trader_id), ev, amount)
    except Exception:
        pass

    return {"status": "ok"}
