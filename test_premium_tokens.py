"""Verification test for the in-game Premium unlock.

Premium is bought with GAME TOKENS: fictional credits that exist only inside
this bot. No money, no deposit, no Pocket Option balance and no affiliate event
may ever reach this system, and users.deposit (the real trading figure) is a
separate column that nothing here reads or writes. That separation is asserted
below, not just documented.

The rules under test:

    Start   -> config.START_DAILY_SIGNALS   signals/day
    Premium -> config.PREMIUM_DAILY_SIGNALS signals/day
    unlock  -> config.PREMIUM_UNLOCK_COST   tokens, deducted exactly once

The unlock's whole safety property is that it is ONE statement. Two taps
arriving together must not both spend the same 100 tokens, so the balance check
lives in the UPDATE's WHERE rather than in Python. Two things are therefore
checked separately, because neither implies the other:

  * behaviour, driven through the real menu_premium handler against the
    harness's row model (does the right thing happen for each balance)
  * the SQL itself, asserted as text, because a fake cannot prove that the
    statement Postgres will run is a single gated UPDATE. A read-then-write
    pair would pass every behavioural test here and still lose the race in
    production.

Run from the repo root:  python test_premium_tokens.py

No network and no database: db and panelbot are stubbed through the helpers in
test_signal_flow, which are also what let bot.py be imported at all.
"""

import asyncio
import importlib
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

import test_signal_flow as H


FAILURES = []
CHECKS = [0]


def check(label, condition, detail=""):
    CHECKS[0] += 1
    if condition:
        print("  ok   " + label)
    else:
        print("  FAIL " + label + ((" -- " + detail) if detail else ""))
        FAILURES.append(label)


def _mkuser(fake_db, tg_id, tokens=0, premium=False, verified=True):
    row = fake_db._fresh_row()
    row.update({"game_tokens": tokens, "is_premium": premium,
                "verified": verified, "ui_msg_id": 500})
    fake_db._users[tg_id] = row
    return row


async def tap_premium(bot_mod, fake_db, tg_id):
    """One tap of the Unlock Premium button, through the real handler."""
    fake_bot = H.FakeBot()
    cb = H.FakeCB(tg_id, "menu:premium", 500)
    await bot_mod.menu_premium(cb, fake_bot)
    sent = [c for c in fake_bot.calls if c["kind"] != "delete"]
    return (sent[-1]["body"] if sent else ""), fake_bot


async def main():
    import config
    fake_db = H._install_stub_modules()
    bot_mod = H._load_bot()
    import db as stub_db

    # --- balance outcomes ---------------------------------------------------
    # Cases 1-5: the balance decides, and the message quotes the shortfall.
    print("[unlock] balance decides, and the screen quotes the real numbers")
    COST = config.PREMIUM_UNLOCK_COST
    check("PREMIUM_UNLOCK_COST is 100 by default", COST == 100, str(COST))

    cases = [
        # (tokens, expect_premium, expect_balance, expect_needed)
        (0,   False, 0,   100),
        (50,  False, 50,  50),
        (99,  False, 99,  1),
        (100, True,  0,   None),
        (150, True,  50,  None),
    ]
    for tokens, want_premium, want_balance, want_needed in cases:
        tg_id = 70000 + tokens
        _mkuser(fake_db, tg_id, tokens=tokens)
        text, _ = await tap_premium(bot_mod, fake_db, tg_id)
        row = fake_db._users[tg_id]

        check("%d tokens: is_premium is %s" % (tokens, want_premium),
              bool(row["is_premium"]) is want_premium,
              "is_premium=%r" % row["is_premium"])
        check("%d tokens: balance is now %d" % (tokens, want_balance),
              row["game_tokens"] == want_balance, str(row["game_tokens"]))

        if want_premium:
            check("%d tokens: screen says Premium Unlocked" % tokens,
                  "Premium Unlocked" in text, repr(text[:80]))
            check("%d tokens: screen quotes the Premium limit" % tokens,
                  str(config.PREMIUM_DAILY_SIGNALS) in text, repr(text[:80]))
        else:
            check("%d tokens: screen says Premium Locked" % tokens,
                  "Premium Locked" in text, repr(text[:80]))
            check("%d tokens: reports balance %d" % (tokens, want_balance),
                  ("Your balance: %d" % want_balance) in text, repr(text))
            check("%d tokens: reports %d still needed" % (tokens, want_needed),
                  ("Still needed: %d tokens" % want_needed) in text, repr(text))
            check("%d tokens: nothing was deducted" % tokens,
                  row["game_tokens"] == tokens, str(row["game_tokens"]))

    # --- case 6: no double unlock ------------------------------------------
    print("\n[unlock] a second tap cannot spend a second time")
    tg_id = 71000
    _mkuser(fake_db, tg_id, tokens=250)
    await tap_premium(bot_mod, fake_db, tg_id)
    after_first = fake_db._users[tg_id]["game_tokens"]
    text, _ = await tap_premium(bot_mod, fake_db, tg_id)
    after_second = fake_db._users[tg_id]["game_tokens"]
    check("first tap charged exactly the cost",
          after_first == 250 - COST, str(after_first))
    check("second tap charged nothing",
          after_second == after_first, str(after_second))
    check("second tap still reports Premium",
          bool(fake_db._users[tg_id]["is_premium"]))
    check("second tap does not claim a fresh unlock",
          "Premium Unlocked" not in text, repr(text[:80]))

    # --- case 7: two simultaneous taps -------------------------------------
    # Both coroutines are started before either is awaited, so they interleave
    # at the same await points a real double-tap would.
    print("\n[unlock] two simultaneous taps cannot spend 200")
    tg_id = 72000
    _mkuser(fake_db, tg_id, tokens=150)
    await asyncio.gather(tap_premium(bot_mod, fake_db, tg_id),
                         tap_premium(bot_mod, fake_db, tg_id))
    row = fake_db._users[tg_id]
    check("exactly one cost was deducted, not two",
          row["game_tokens"] == 150 - COST,
          "balance=%s (200 spent would be %s)" % (row["game_tokens"], 150 - 2 * COST))
    check("balance never went negative", row["game_tokens"] >= 0,
          str(row["game_tokens"]))
    check("state is consistent: premium held, cost paid once",
          bool(row["is_premium"]) and row["game_tokens"] == 150 - COST)

    # A concurrent pair that can only afford ONE unlock must not overdraw.
    tg_id = 72001
    _mkuser(fake_db, tg_id, tokens=COST)
    await asyncio.gather(tap_premium(bot_mod, fake_db, tg_id),
                         tap_premium(bot_mod, fake_db, tg_id))
    row = fake_db._users[tg_id]
    check("exact-cost balance ends at 0, not negative",
          row["game_tokens"] == 0, str(row["game_tokens"]))
    check("exact-cost concurrent pair still grants Premium",
          bool(row["is_premium"]))

    # --- the statement itself ----------------------------------------------
    # The fake above cannot prove the real SQL is safe. Assert its text.
    print("\n[sql] the unlock is one gated UPDATE, not a read-then-write")
    src = open(os.path.join(ROOT, "db.py"), encoding="utf-8").read()
    stmt = re.search(r'_UNLOCK_PREMIUM_SQL\s*=\s*"""(.*?)"""', src, re.S)
    check("_UNLOCK_PREMIUM_SQL exists", stmt is not None)
    if stmt:
        sql = " ".join(stmt.group(1).split())
        check("it is a single statement (no ';')", ";" not in sql, sql)
        check("it is an UPDATE", sql.upper().startswith("UPDATE USERS"), sql)
        check("it gates on the balance in the WHERE",
              re.search(r"AND\s+game_tokens\s*>=\s*\$2", sql) is not None, sql)
        check("it gates on is_premium = FALSE in the WHERE",
              re.search(r"AND\s+is_premium\s*=\s*FALSE", sql, re.I) is not None, sql)
        check("it deducts the cost", "game_tokens = game_tokens - $2" in sql, sql)
        check("it sets the tier in the same statement",
              "is_premium  = TRUE" in sql or "is_premium = TRUE" in sql, sql)
        check("it is scoped to one tg_id",
              re.search(r"WHERE\s+tg_id\s*=\s*\$1", sql) is not None, sql)
        check("it returns the new balance", "RETURNING game_tokens" in sql, sql)

    check("the schema adds game_tokens with a safe default",
          "ADD COLUMN IF NOT EXISTS game_tokens INTEGER NOT NULL DEFAULT 0" in src)
    check("is_premium keeps its existing declaration",
          "ADD COLUMN IF NOT EXISTS is_premium BOOLEAN NOT NULL DEFAULT FALSE" in src)

    # No money anywhere near this. deposit is the real trading figure.
    unlock_fn = src[src.index("_UNLOCK_PREMIUM_SQL"):src.index("async def game_tokens")]
    check("the unlock path never touches users.deposit",
          "deposit" not in unlock_fn, "deposit referenced in the unlock path")

    # --- cases 8/9/10: the limits come from the env -------------------------
    print("\n[quota] limits come from the environment, never hardcoded")
    check("Start limit is START_DAILY_SIGNALS",
          config.daily_limit(False) == config.START_DAILY_SIGNALS,
          str(config.daily_limit(False)))
    check("Premium limit is PREMIUM_DAILY_SIGNALS",
          config.daily_limit(True) == config.PREMIUM_DAILY_SIGNALS,
          str(config.daily_limit(True)))
    check("defaults are 30 and 70",
          (config.START_DAILY_SIGNALS, config.PREMIUM_DAILY_SIGNALS) == (30, 70),
          str((config.START_DAILY_SIGNALS, config.PREMIUM_DAILY_SIGNALS)))

    # Case 10: change the Railway variable, reimport, and the Premium cap moves
    # with no code edit. Restored afterwards so later checks see the defaults.
    saved = os.environ.get("PREMIUM_DAILY_SIGNALS")
    try:
        os.environ["PREMIUM_DAILY_SIGNALS"] = "100"
        reloaded = importlib.reload(config)
        check("PREMIUM_DAILY_SIGNALS=100 moves the Premium limit to 100",
              reloaded.daily_limit(True) == 100, str(reloaded.daily_limit(True)))
        check("the Start limit is unaffected by it",
              reloaded.daily_limit(False) == 30, str(reloaded.daily_limit(False)))
        check("no literal 70 is left in the limit logic",
              reloaded.PREMIUM_DAILY_SIGNALS == 100)
    finally:
        if saved is None:
            os.environ.pop("PREMIUM_DAILY_SIGNALS", None)
        else:
            os.environ["PREMIUM_DAILY_SIGNALS"] = saved
        config = importlib.reload(config)
    check("the Premium limit is restored after the env test",
          config.daily_limit(True) == 70, str(config.daily_limit(True)))

    # The tier a user is actually held to, through the same helper bot.py uses.
    tg_id = 73000
    _mkuser(fake_db, tg_id, tokens=0, premium=False)
    _, limit = await bot_mod._user_quota(tg_id)
    check("a Start user is held to START_DAILY_SIGNALS",
          limit == config.START_DAILY_SIGNALS, str(limit))
    fake_db._users[tg_id]["is_premium"] = True
    _, limit = await bot_mod._user_quota(tg_id)
    check("a Premium user is held to PREMIUM_DAILY_SIGNALS",
          limit == config.PREMIUM_DAILY_SIGNALS, str(limit))

    # Enforcement really stops at the tier's number, not at a literal.
    tg_id = 73001
    _mkuser(fake_db, tg_id, tokens=0, premium=False)
    for _ in range(config.START_DAILY_SIGNALS):
        await stub_db.consume_signal(tg_id, config.START_DAILY_SIGNALS)
    ok, used, left = await stub_db.consume_signal(tg_id, config.START_DAILY_SIGNALS)
    check("Start user is refused after START_DAILY_SIGNALS signals",
          ok is False and used == config.START_DAILY_SIGNALS and left == 0,
          str((ok, used, left)))
    fake_db._users[tg_id]["is_premium"] = True
    ok, used, left = await stub_db.consume_signal(tg_id, config.PREMIUM_DAILY_SIGNALS)
    check("the same user continues once Premium raises the cap",
          ok is True and used == config.START_DAILY_SIGNALS + 1, str((ok, used)))

    # --- cases 11/12/13: the admin commands ---------------------------------
    print("\n[admin] /tokens and /tokenset are admin-only")
    admin = config.ADMIN_IDS[0] if config.ADMIN_IDS else 999001
    saved_admins = list(config.ADMIN_IDS)
    config.ADMIN_IDS = [admin]
    try:
        target = 74000
        _mkuser(fake_db, target, tokens=0)

        m = H.FakeMessage(admin, "/tokens %d 100" % target)
        await bot_mod.cmd_tokens(m)
        check("admin /tokens adds tokens",
              fake_db._users[target]["game_tokens"] == 100,
              str(fake_db._users[target]["game_tokens"]))
        check("admin /tokens confirms the new balance",
              m.replies and "100" in m.replies[-1], str(m.replies))

        m = H.FakeMessage(admin, "/tokens %d 50" % target)
        await bot_mod.cmd_tokens(m)
        check("admin /tokens is additive",
              fake_db._users[target]["game_tokens"] == 150,
              str(fake_db._users[target]["game_tokens"]))

        m = H.FakeMessage(admin, "/tokenset %d 7" % target)
        await bot_mod.cmd_tokenset(m)
        check("admin /tokenset sets an absolute balance",
              fake_db._users[target]["game_tokens"] == 7,
              str(fake_db._users[target]["game_tokens"]))

        # Case 12: a non-admin must change nothing AND learn nothing.
        intruder = admin + 12345
        m = H.FakeMessage(intruder, "/tokens %d 1000" % target)
        await bot_mod.cmd_tokens(m)
        check("non-admin /tokens changes no balance",
              fake_db._users[target]["game_tokens"] == 7,
              str(fake_db._users[target]["game_tokens"]))
        check("non-admin /tokens gets no reply at all",
              m.replies == [], str(m.replies))

        m = H.FakeMessage(intruder, "/tokenset %d 1000" % target)
        await bot_mod.cmd_tokenset(m)
        check("non-admin /tokenset changes no balance",
              fake_db._users[target]["game_tokens"] == 7,
              str(fake_db._users[target]["game_tokens"]))
        check("non-admin /tokenset gets no reply at all",
              m.replies == [], str(m.replies))

        # A non-admin must not be able to grant themselves Premium this way.
        _mkuser(fake_db, intruder, tokens=0)
        m = H.FakeMessage(intruder, "/tokens %d 100" % intruder)
        await bot_mod.cmd_tokens(m)
        check("non-admin cannot fund their own unlock",
              fake_db._users[intruder]["game_tokens"] == 0)

        # Malformed input is refused rather than half-applied.
        m = H.FakeMessage(admin, "/tokens %d" % target)
        await bot_mod.cmd_tokens(m)
        check("admin /tokens with no amount shows usage",
              m.replies and "Usage:" in m.replies[-1], str(m.replies))
        check("the malformed command changed nothing",
              fake_db._users[target]["game_tokens"] == 7)

        m = H.FakeMessage(admin, "/tokens 999999999 50")
        await bot_mod.cmd_tokens(m)
        check("admin /tokens on an unknown tg_id says so",
              m.replies and "No user" in m.replies[-1], str(m.replies))

        # A negative adjustment floors at 0 rather than going negative.
        m = H.FakeMessage(admin, "/tokens %d -999" % target)
        await bot_mod.cmd_tokens(m)
        check("a negative adjustment floors the balance at 0",
              fake_db._users[target]["game_tokens"] == 0,
              str(fake_db._users[target]["game_tokens"]))
    finally:
        config.ADMIN_IDS = saved_admins

    # --- the My level screen ------------------------------------------------
    print("\n[level] My level reports the tier, the limit and the balance")
    tg_id = 75000
    _mkuser(fake_db, tg_id, tokens=42, premium=False)
    fake_bot = H.FakeBot()
    await bot_mod.menu_level(H.FakeCB(tg_id, "menu:level", 500), fake_bot)
    body = [c for c in fake_bot.calls if c["kind"] != "delete"][-1]["body"]
    check("Start: names the Start tier", "Start" in body, repr(body))
    check("Start: shows START_DAILY_SIGNALS",
          str(config.START_DAILY_SIGNALS) in body, repr(body))
    check("Start: shows the token balance",
          "Game tokens:" in body and "42" in body, repr(body))

    _mkuser(fake_db, tg_id, tokens=8, premium=True)
    fake_bot = H.FakeBot()
    await bot_mod.menu_level(H.FakeCB(tg_id, "menu:level", 500), fake_bot)
    body = [c for c in fake_bot.calls if c["kind"] != "delete"][-1]["body"]
    check("Premium: names the Premium tier", "Premium" in body, repr(body))
    check("Premium: shows PREMIUM_DAILY_SIGNALS",
          str(config.PREMIUM_DAILY_SIGNALS) in body, repr(body))
    check("Premium: shows the token balance",
          "Game tokens:" in body and "8" in body, repr(body))

    # --- the button is untouched -------------------------------------------
    print("\n[menu] the Unlock Premium button itself is unchanged")
    menu_kb = config.SCREENS["menu"]["kb"]
    flat = [b for row in menu_kb for b in row]
    prem = [b for b in flat if b[1] == "cb:menu:premium"]
    check("the Unlock Premium button still exists", len(prem) == 1, str(prem))
    if prem:
        b = prem[0]
        check("text is still 'Unlock Premium'", b[0] == "Unlock Premium", ascii(b[0]))
        check("style is still primary (blue)", b[2] == "primary", str(b[2]))
        check("callback is still cb:menu:premium", b[1] == "cb:menu:premium")
        check("custom emoji is still E_MENU_PREMIUM",
              len(b) > 3 and b[3] == config.E_MENU_PREMIUM, str(b[3:]))

    # --- no real money anywhere --------------------------------------------
    print("\n[safety] the unlock is game-only")
    bot_src = open(os.path.join(ROOT, "bot.py"), encoding="utf-8").read()
    handler = bot_src[bot_src.index('F.data == "menu:premium"'):]
    handler = handler[:handler.index("@dp.callback_query", 10)]
    for banned in ("deposit", "panelbot", "uid", "verify", "payment", "invoice"):
        check("the unlock handler never mentions %r" % banned,
              banned not in handler.lower(), banned)

    print("\n%d checks, %d failed" % (CHECKS[0], len(FAILURES)))
    if FAILURES:
        for f in FAILURES:
            print("  FAILED: " + f)
        return 1
    print("PASS - Premium unlocks for game tokens only, atomically.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
