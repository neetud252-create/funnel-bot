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


async def tap_premium(bot_mod, fake_db, tg_id, state=None):
    """One tap of the Unlock Premium button, through the real handler."""
    fake_bot = H.FakeBot()
    cb = H.FakeCB(tg_id, "menu:premium", 500)
    await bot_mod.menu_premium(cb, fake_bot, state or H.FakeState())
    sent = [c for c in fake_bot.calls if c["kind"] != "delete"]
    return (sent[-1]["body"] if sent else ""), fake_bot


async def send_uid(bot_mod, tg_id, uid, state):
    """One game-UID message, through the real Premium handler."""
    fake_bot = H.FakeBot()
    m = H.FakeMessage(tg_id, uid)
    await bot_mod.premium_uid(m, fake_bot, state)
    sent = [c for c in fake_bot.calls if c["kind"] != "delete"]
    return (sent[-1]["body"] if sent else ""), fake_bot


# The Premium screens are written in real-currency terms by request, so the
# in-game phrasing is what must NOT appear on them. This is the exact inverse
# of the rule these tests carried before the copy was changed.
BANNED = ("game token", "game tokens", "game uid", "virtual balance",
          "in-game", "fictional", "credits")


def _code_only(src):
    """Strip docstrings and # comments, leaving executable lines.

    The comments in this codebase name the things they promise NOT to do
    ("never calls panelbot"), so a scan of the raw text would flag exactly the
    lines that document the guarantee. Only real statements are of interest.
    """
    src = re.sub(r'""".*?"""', "", src, flags=re.S)
    src = re.sub(r"'''.*?'''", "", src, flags=re.S)
    return "\n".join(line.split("#", 1)[0] for line in src.splitlines())


def assert_game_only(label, text):
    """No in-game phrasing on a Premium screen (the copy is real-currency)."""
    low = (text or "").lower()
    for word in BANNED:
        check("%s: never says %r" % (label, word.strip()),
              word not in low, repr(text))


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

    # Cases 1-5: the shortfall itself, straight from the single calculation.
    for tokens, want_needed in ((0, 100), (50, 50), (99, 1), (100, 0), (150, 0)):
        check("%d tokens -> needed = %d" % (tokens, want_needed),
              config.tokens_needed(tokens) == want_needed,
              str(config.tokens_needed(tokens)))
    check("needed never goes negative above the cost",
          config.tokens_needed(10 ** 6) == 0)

    # The screen the tap puts up, for each balance. The tap NEVER unlocks - it
    # states the position and asks for an account ID.
    cases = [
        # (tokens, expect_needed_line, expect_ready)
        (0,   100, False),
        (50,  50,  False),
        (99,  1,   False),
        (100, 0,   True),
        (150, 0,   True),
    ]
    for tokens, want_needed, ready in cases:
        tg_id = 70000 + tokens
        _mkuser(fake_db, tg_id, tokens=tokens)
        text, _ = await tap_premium(bot_mod, fake_db, tg_id)
        row = fake_db._users[tg_id]

        check("%d tokens: the tap alone never unlocks Premium" % tokens,
              not row["is_premium"], "is_premium=%r" % row["is_premium"])
        check("%d tokens: the tap alone deducts nothing" % tokens,
              row["game_tokens"] == tokens, str(row["game_tokens"]))
        check("%d tokens: leads with the money custom emoji" % tokens,
              text.startswith(config.pe(config.E_MONEY, "\U0001F4B0")
                              + " <b>Almost there.</b>"), repr(text[:80]))
        check("%d tokens: quotes the threshold as $%d" % (tokens, COST),
              ("<b>$%d</b>" % COST) in text, repr(text))
        check("%d tokens: asks for the account ID" % tokens,
              "account ID" in text, repr(text))
        check("%d tokens: uses a real em dash, not a hyphen" % tokens,
              "\U00002014" in text, repr(text))
        if ready:
            check("%d tokens: says the threshold is met" % tokens,
                  "meets the" in text, repr(text))
        else:
            check("%d tokens: asks them to top up" % tokens,
                  "top up your balance" in text, repr(text))
            check("%d tokens: says 'registered through our link'" % tokens,
                  "registered through our link" in text, repr(text))
        # No in-game phrasing left on any Premium screen.
        assert_game_only("%d tokens" % tokens, text)

    # --- the insufficient-balance screen, to the byte ------------------------
    # Asserted as the rendered text plus its entity offsets, not just as a
    # substring: the offsets are what Telegram uses, and they are measured in
    # UTF-16 code units, so the emoji at the front counts as 2 rather than 1.
    print("\n[copy] the insufficient-balance screen matches the spec exactly")
    html = config.MSG_PREMIUM_SHORT.format(cost=COST)
    plain, ents, pos = [], [], 0
    kind = bstart = None
    for tok in re.split(r"(<[^>]+>)", html):
        if not tok:
            continue
        if tok.startswith("<"):
            if tok.startswith("<tg-emoji"):
                kind = (pos, re.search(r'emoji-id="(\d+)"', tok).group(1))
            elif tok == "</tg-emoji>":
                ents.append(("custom_emoji", kind[0], pos - kind[0], kind[1]))
            elif tok == "<b>":
                bstart = pos
            elif tok == "</b>":
                ents.append(("bold", bstart, pos - bstart, None))
        else:
            plain.append(tok)
            pos += len(tok.encode("utf-16-le")) // 2
    plain = "".join(plain)

    EXPECT = ("\U0001F4B0 Almost there.\n\nYour account is registered through "
              "our link. To unlock access, top up your balance with $100 or "
              "more \U00002014 then send your account ID here again to "
              "complete verification.")
    check("rendered text matches the supplied string exactly",
          plain == EXPECT, repr(plain))
    check("entities are exactly the three specified",
          sorted(ents, key=lambda e: e[1])
          == [("custom_emoji", 0, 2, "5224257782013769471"),
              ("bold", 3, 13, None), ("bold", 106, 4, None)], str(ents))
    check("the money emoji uses the supplied id",
          'emoji-id="5224257782013769471"' in html, html[:60])
    check("the emoji falls back to a literal money bag, not nothing",
          ">\U0001F4B0</tg-emoji>" in html, html[:60])
    check("exactly two bold spans", html.count("<b>") == 2, str(html.count("<b>")))
    check("em dash U+2014 present", "\U00002014" in plain)
    check("no hyphen stands in for the em dash", "-" not in plain, repr(plain))
    check("blank line between heading and paragraph",
          plain.split("\n")[1] == "", repr(plain.split("\n")[:2]))
    check("the threshold is rendered from PREMIUM_UNLOCK_COST, not hardcoded",
          "{cost}" in config.MSG_PREMIUM_SHORT)

    # --- cases 11-14: every UID message verifies again -----------------------
    # The point of this block: no one-time flag, no memo, no "already verified"
    # shortcut. The real _verify_account_id is wrapped in a counter that calls
    # through, so this counts REAL invocations rather than a stand-in.
    print("\n[uid] every UID message triggers verification again")
    calls = []
    real_verify = bot_mod._verify_account_id

    async def counting_verify(tg_id, uid):
        result = await real_verify(tg_id, uid)
        calls.append((tg_id, uid, result))
        return result

    bot_mod._verify_account_id = counting_verify
    try:
        tg_id = 76000
        state = H.FakeState()
        _mkuser(fake_db, tg_id, tokens=0)
        await tap_premium(bot_mod, fake_db, tg_id, state)
        check("the tap arms the Premium UID state",
              state.state == bot_mod.Premium.waiting_uid, str(state.state))

        # The SAME uid three times. Each send must verify again.
        SAME = "123456789"
        for attempt in (1, 2, 3):
            before = len(calls)
            text, _ = await send_uid(bot_mod, tg_id, SAME, state)
            check("UID send #%d triggers a fresh verification" % attempt,
                  len(calls) == before + 1,
                  "%d verifications recorded" % (len(calls) - before))
            check("UID send #%d verified the uid that was sent" % attempt,
                  calls[-1][1] == SAME, str(calls[-1]))
            check("UID send #%d is not waved through as already verified"
                  % attempt, "already" not in text.lower(), repr(text[:60]))
            check("UID send #%d stays armed for another send" % attempt,
                  state.state == bot_mod.Premium.waiting_uid, str(state.state))
        check("three sends produced three verifications, not one",
              len(calls) == 3, str(len(calls)))

        # A previously verified funnel user must NOT skip the check.
        tg_id = 76001
        state = H.FakeState()
        _mkuser(fake_db, tg_id, tokens=0, verified=True)
        fake_db._users[tg_id]["uid"] = SAME
        await tap_premium(bot_mod, fake_db, tg_id, state)
        before = len(calls)
        await send_uid(bot_mod, tg_id, SAME, state)
        check("an already-verified user's UID is still re-verified",
              len(calls) == before + 1, str(len(calls) - before))

        # Case 14: an invalid UID can be submitted again, as often as needed.
        tg_id = 76002
        state = H.FakeState()
        _mkuser(fake_db, tg_id, tokens=200)
        await tap_premium(bot_mod, fake_db, tg_id, state)
        for bad in ("abc", "12", "not-a-uid", ""):
            before = len(calls)
            text, _ = await send_uid(bot_mod, tg_id, bad, state)
            check("invalid UID %r is checked, not ignored" % bad,
                  len(calls) == before + 1, str(len(calls) - before))
            check("invalid UID %r is rejected" % bad,
                  "not valid" in text.lower(), repr(text[:60]))
            check("invalid UID %r leaves the state armed for a retry" % bad,
                  state.state == bot_mod.Premium.waiting_uid, str(state.state))
            check("invalid UID %r never unlocks Premium" % bad,
                  not fake_db._users[tg_id]["is_premium"])
            check("invalid UID %r deducts nothing" % bad,
                  fake_db._users[tg_id]["game_tokens"] == 200,
                  str(fake_db._users[tg_id]["game_tokens"]))
            assert_game_only("invalid uid", text)
        # ...and a good one still works afterwards.
        text, _ = await send_uid(bot_mod, tg_id, SAME, state)
        check("a valid UID after several invalid ones still unlocks",
              fake_db._users[tg_id]["is_premium"], repr(text[:60]))

        # --- case 15: valid UID, not enough tokens --------------------------
        print("\n[uid] a valid UID does not unlock without the tokens")
        for tokens, want_needed in ((0, 100), (50, 50), (99, 1)):
            tg_id = 77000 + tokens
            state = H.FakeState()
            _mkuser(fake_db, tg_id, tokens=tokens)
            await tap_premium(bot_mod, fake_db, tg_id, state)
            text, _ = await send_uid(bot_mod, tg_id, SAME, state)
            row = fake_db._users[tg_id]
            check("%d tokens + valid UID: still not Premium" % tokens,
                  not row["is_premium"], "is_premium=%r" % row["is_premium"])
            check("%d tokens + valid UID: nothing deducted" % tokens,
                  row["game_tokens"] == tokens, str(row["game_tokens"]))
            check("%d tokens + valid UID: confirms the ID was checked" % tokens,
                  "account ID has been checked" in text, repr(text))
            check("%d tokens + valid UID: quotes the $%d threshold" % (tokens, COST),
                  ("<b>$%d</b>" % COST) in text, repr(text))
            check("%d tokens + valid UID: invites another ID send" % tokens,
                  "send your account id here again" in text.lower(), repr(text))
            check("%d tokens + valid UID: stays armed" % tokens,
                  state.state == bot_mod.Premium.waiting_uid, str(state.state))
            assert_game_only("%d tokens + valid uid" % tokens, text)

        # --- cases 16/17: valid UID + enough tokens -------------------------
        print("\n[uid] a valid UID with enough tokens unlocks, once")
        for tokens in (100, 150, 250):
            tg_id = 78000 + tokens
            state = H.FakeState()
            _mkuser(fake_db, tg_id, tokens=tokens)
            await tap_premium(bot_mod, fake_db, tg_id, state)
            text, _ = await send_uid(bot_mod, tg_id, SAME, state)
            row = fake_db._users[tg_id]
            check("%d tokens: Premium is unlocked" % tokens, row["is_premium"])
            check("%d tokens: exactly the cost was deducted" % tokens,
                  row["game_tokens"] == tokens - COST, str(row["game_tokens"]))
            check("%d tokens: screen confirms the UID was verified" % tokens,
                  "verified successfully" in text, repr(text))
            check("%d tokens: screen quotes the Premium allowance" % tokens,
                  ("<b>%d signals per day</b>" % config.PREMIUM_DAILY_SIGNALS)
                  in text, repr(text))
            check("%d tokens: the UID state is released" % tokens,
                  state.state is None, str(state.state))
            assert_game_only("%d tokens unlocked" % tokens, text)

            # Case 6 restated on this path: a second UID send cannot re-charge.
            before_balance = row["game_tokens"]
            text, _ = await send_uid(bot_mod, tg_id, SAME, H.FakeState(
                bot_mod.Premium.waiting_uid))
            check("%d tokens: a second UID send charges nothing" % tokens,
                  row["game_tokens"] == before_balance, str(row["game_tokens"]))
            check("%d tokens: a second send does not claim a fresh unlock" % tokens,
                  "Premium Unlocked" not in text, repr(text[:60]))
    finally:
        bot_mod._verify_account_id = real_verify

    # --- an already-Premium user cannot unlock again ------------------------
    # Distinct from the "second send" checks above: this user holds Premium
    # before the flow is ever entered, so it covers the entry path too.
    print("\n[unlock] an existing Premium user cannot unlock a second time")
    tg_id = 79000
    _mkuser(fake_db, tg_id, tokens=500, premium=True)
    state = H.FakeState()
    text, _ = await tap_premium(bot_mod, fake_db, tg_id, state)
    row = fake_db._users[tg_id]
    check("an existing Premium user is not asked for a UID",
          state.state is None, str(state.state))
    check("the tap deducts nothing from a Premium user",
          row["game_tokens"] == 500, str(row["game_tokens"]))
    check("the tap tells them Premium is already active",
          "already active" in text, repr(text[:60]))
    assert_game_only("already premium", text)

    # ...and even if a UID reaches the handler, nothing is spent.
    text, _ = await send_uid(bot_mod, tg_id, "123456789",
                             H.FakeState(bot_mod.Premium.waiting_uid))
    check("a UID from a Premium user deducts nothing",
          fake_db._users[tg_id]["game_tokens"] == 500,
          str(fake_db._users[tg_id]["game_tokens"]))
    check("a UID from a Premium user does not re-unlock",
          "Premium Unlocked" not in text, repr(text[:60]))
    check("they are still Premium afterwards", row["is_premium"])

    # --- case 18: two simultaneous unlocks ----------------------------------
    # Both coroutines are started before either is awaited, so they interleave
    # at the same await points a real double-send would.
    print("\n[unlock] two simultaneous unlocks cannot spend 200")
    tg_id = 72000
    _mkuser(fake_db, tg_id, tokens=150)
    await asyncio.gather(
        send_uid(bot_mod, tg_id, "123456789", H.FakeState(bot_mod.Premium.waiting_uid)),
        send_uid(bot_mod, tg_id, "123456789", H.FakeState(bot_mod.Premium.waiting_uid)))
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
    await asyncio.gather(
        send_uid(bot_mod, tg_id, "123456789", H.FakeState(bot_mod.Premium.waiting_uid)),
        send_uid(bot_mod, tg_id, "123456789", H.FakeState(bot_mod.Premium.waiting_uid)))
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
    # "uid" and "verify" are EXPECTED here now - the flow is a game-UID check.
    # What must stay out is the real-money machinery.
    for banned in ("deposit", "panelbot", "payment", "invoice", "usd"):
        check("the unlock screen handler never mentions %r" % banned,
              banned not in handler.lower(), banned)

    # The Premium UID handler is the one that must never reach the affiliate
    # panel: that is the trading-account check, and this flow is game-only.
    #
    # Scanned as CODE, not as prose: both of these carry comments that name
    # panelbot and users.verified precisely to say they are not used, and a raw
    # substring search would flag the comment that documents the guarantee.
    uid_handler = _code_only(
        bot_src[bot_src.index("async def premium_uid"):
                bot_src.index("@dp.message(Reg.waiting_uid)")])
    for banned in ("panelbot", "deposit", "lookup_trader", "set_verified",
                   "campaign", "min_deposit", "payment", "verified"):
        check("the Premium UID handler never touches %r" % banned,
              banned not in uid_handler.lower(), uid_handler)

    # The game-UID check itself must be self-contained for the same reason.
    verify_fn = _code_only(
        bot_src[bot_src.index("async def _verify_account_id"):
                bot_src.index("@dp.message(Premium.waiting_uid)")])
    for banned in ("panelbot", "db.", "deposit", "verified"):
        check("_verify_account_id never touches %r" % banned,
              banned not in verify_fn.lower(), verify_fn)

    # Registration order is load-bearing: aiogram dispatches in definition
    # order, and uid_anytime matches any bare number in ANY state. If the
    # Premium handler were registered below it, a UID sent during the unlock
    # flow would fall into the funnel's capture path - which short-circuits on
    # users.verified and would silently skip the re-verification.
    check("the Premium UID handler is registered above Reg.waiting_uid",
          bot_src.index("@dp.message(Premium.waiting_uid)")
          < bot_src.index("@dp.message(Reg.waiting_uid)"))
    check("the Premium UID handler is registered above the bare-number catch-all",
          bot_src.index("@dp.message(Premium.waiting_uid)")
          < bot_src.index('@dp.message(F.text.regexp'))
    check("Premium.waiting_uid is a state of its own, not a flag on Reg",
          "class Premium(StatesGroup)" in bot_src)

    # The funnel's own capture path must be untouched: it still short-circuits
    # on users.verified, which is what keeps it from re-querying the panel.
    capture = _code_only(
        bot_src[bot_src.index("async def _capture_uid"):
                bot_src.index("async def retry_worker")])
    check("the funnel's capture path still short-circuits on verified",
          'user["verified"]' in capture, capture[:200])
    # The capture path reaches the panel through the single-flight wrapper
    # rather than calling _run_verification directly. Assert the whole chain,
    # not just one name: the guarantee is that the funnel still ends at
    # panelbot, and each link is checked so the chain cannot be cut anywhere.
    check("the funnel's capture path submits a real verification",
          "_verify_once" in capture, capture[:200])
    verify_once = _code_only(
        bot_src[bot_src.index("async def _verify_once"):
                bot_src.index("@dp.message(Reg.waiting_uid)")])
    check("the single-flight wrapper calls _run_verification",
          "_run_verification" in verify_once, verify_once[:200])
    check("_run_verification still reaches panelbot",
          "panelbot.lookup_trader(uid)" in bot_src)
    check("the capture path still never reaches the game-only format check",
          "_verify_account_id" not in capture, capture[:200])

    # No one-time flag anywhere on the Premium path.
    for flag in ("already_verified", "_verified_uids", "uid_verified",
                 "verified_once", "seen_uid"):
        check("no one-time verification flag named %r exists" % flag,
              flag not in bot_src.lower(), flag)

    print("\n%d checks, %d failed" % (CHECKS[0], len(FAILURES)))
    if FAILURES:
        for f in FAILURES:
            print("  FAILED: " + f)
        return 1
    print("PASS - Premium unlock flow verifies every ID and unlocks atomically.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
