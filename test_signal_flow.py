"""Verification test for the signal waiting screen.

Asserts that every code path that can produce a signal puts up the same
two-message waiting screen, in the same order, sends no media whatsoever while
doing it, and always waits the full config.SIGNAL_COUNTDOWN regardless of which
expiration was tapped:

    1. the chart custom emoji, alone in its own text message
    2. the two-line analysis notice
    -- config.SIGNAL_COUNTDOWN seconds --
    3. the finished signal

Run from the repo root:  python test_signal_flow.py

No network, no database and no real waiting: aiogram's Bot, db and the 30s
sleep are all stubbed. asyncio.sleep is replaced by a recorder, so the test
checks the delay that was *requested* rather than sitting through it.
"""

import asyncio
import os
import sys
import types

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

# Screen copy is full of emoji, and a failure detail quotes it back. On a
# Windows console (cp1252) that raises UnicodeEncodeError from inside the
# reporting itself, which kills the run and hides the failure it was printing.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# --- stub out everything bot.py imports but this test does not exercise ------

def _install_stub_modules():
    db = types.ModuleType("db")
    db._users = {}
    # Stand-in for Postgres CURRENT_DATE. Tests advance it to cross midnight.
    db._today = ["2026-08-25"]

    # Mirrors the SCHEMA defaults, so a row created here looks exactly like a
    # brand-new user's - including the columns upstream added (verified_at,
    # nudge_msg_id) that /start and _clear_nudge now read.
    def _fresh_row():
        return {"ui_msg_id": None, "album_ids": None, "is_premium": False,
                "signals_used_today": 0, "last_reset_date": None,
                "verified": False, "verified_at": None, "uid": None,
                "deposit": 0, "last_checked": None, "nudge_msg_id": None,
                "username": None}

    def _u(tg_id):
        return db._users.setdefault(tg_id, _fresh_row())

    db._fresh_row = _fresh_row

    async def get_user(tg_id):
        return dict(_u(tg_id))

    async def set_ui_msg(tg_id, msg_id):
        _u(tg_id)["ui_msg_id"] = msg_id

    async def set_album(tg_id, ids):
        _u(tg_id)["album_ids"] = ids

    # The quota columns are modelled rather than faked out, so the limit tests
    # exercise the same accept/refuse decision the real SQL makes. db._today
    # stands in for Postgres CURRENT_DATE; advancing it is what a new UTC day
    # looks like from here.
    def _rollover(row):
        # Mirrors _ROLLOVER_SQL and the CASE inside _CONSUME_SQL: a stored date
        # that is not today reads as 0 used and is rewritten on first touch.
        if row.get("last_reset_date") != db._today[0]:
            row["signals_used_today"] = 0
            row["last_reset_date"] = db._today[0]
        return row.get("signals_used_today", 0)

    async def signal_state(tg_id, limit):
        used = _rollover(_u(tg_id))
        return used, max(0, limit - used)

    async def consume_signal(tg_id, limit):
        # Mirrors _CONSUME_SQL's WHERE: at or over the cap nothing increments
        # and the caller is told no.
        row = _u(tg_id)
        used = _rollover(row)
        if used >= limit:
            return False, used, max(0, limit - used)
        row["signals_used_today"] = used + 1
        return True, used + 1, max(0, limit - used - 1)

    async def set_premium(tg_id, flag):
        if tg_id not in db._users:
            return False
        db._users[tg_id]["is_premium"] = bool(flag)
        return True

    async def is_premium(tg_id):
        return bool(_u(tg_id).get("is_premium", False))

    async def touch_user(tg_id, username=None):
        row = _u(tg_id)
        if username is not None:
            row["username"] = username

    # Mirrors _RESET_SQL: one row, every column back to its SCHEMA default,
    # including the ones upstream added to the user lifecycle.
    async def reset_user(tg_id):
        if tg_id not in db._users:
            return False
        db._users[tg_id].update({
            "verified": False, "verified_at": None, "uid": None, "deposit": 0,
            "signals_used_today": 0, "last_reset_date": None,
            "is_premium": False, "ui_msg_id": None, "album_ids": None,
            "nudge_msg_id": None, "last_checked": None,
        })
        return True

    # Upstream's narrower testing helper, kept distinct from reset_user: it
    # leaves uid, the quota and the tier alone.
    async def unverify(tg_id):
        _u(tg_id).update({"verified": False, "verified_at": None,
                          "deposit": 0, "last_checked": None})

    async def set_nudge_msg(tg_id, msg_id):
        _u(tg_id)["nudge_msg_id"] = msg_id

    async def uid_owners(uid):
        return [tg for tg, row in db._users.items() if row.get("uid") == uid]

    async def save_uid_only(tg_id, uid):
        _u(tg_id)["uid"] = uid

    async def set_verified(tg_id, deposit):
        _u(tg_id).update({"verified": True, "deposit": deposit,
                          "verified_at": "now", "last_checked": "now"})

    # Media cache: bot.py writes through to these whenever Telegram hands back a
    # file_id. The fakes never produce one, so these exist to fail loudly if
    # that ever changes rather than to be exercised.
    async def load_media_cache():
        return []

    async def save_media_cache(asset_key, file_id, content_hash):
        db._media_cache[asset_key] = (file_id, content_hash)

    async def drop_media_cache(asset_key):
        db._media_cache.pop(asset_key, None)

    db._media_cache = {}

    for fn in (get_user, set_ui_msg, set_album, signal_state, consume_signal,
               set_premium, is_premium, touch_user, reset_user, unverify,
               set_nudge_msg, uid_owners, save_uid_only, set_verified,
               load_media_cache, save_media_cache, drop_media_cache):
        setattr(db, fn.__name__, fn)
    sys.modules["db"] = db

    panelbot = types.ModuleType("panelbot")
    panelbot.PanelUnavailable = type("PanelUnavailable", (Exception,), {})
    sys.modules["panelbot"] = panelbot

    server = types.ModuleType("server")
    server.app = object()
    sys.modules["server"] = server

    uvicorn = types.ModuleType("uvicorn")
    uvicorn.Config = object
    uvicorn.Server = object
    sys.modules["uvicorn"] = uvicorn
    return db


def _load_bot():
    # bot.py ends in asyncio.run(main()), so it cannot simply be imported.
    src = open(os.path.join(ROOT, "bot.py"), encoding="utf-8").read()
    marker = "asyncio.run(main())"
    assert marker in src, "bot.py no longer ends in asyncio.run(main())"
    src = src.replace(marker, "pass")
    mod = types.ModuleType("botmod")
    mod.__file__ = os.path.join(ROOT, "bot.py")
    exec(compile(src, "bot.py", "exec"), mod.__dict__)
    return mod


# --- fakes ------------------------------------------------------------------

class FakeMsg:
    def __init__(self, message_id, text=None, caption=None):
        self.message_id = message_id
        self.text = text
        self.caption = caption
        self.photo = None


class FakeBot:
    """Records every outbound call in order."""

    def __init__(self):
        self.calls = []
        self._next = 1000

    def _mid(self):
        self._next += 1
        return self._next

    async def send_photo(self, chat_id, photo, caption=None, parse_mode=None,
                         reply_markup=None):
        mid = self._mid()
        # photo_for() hands us an FSInputFile, so .path is the asset actually
        # going over the wire - that is what proves which image was sent.
        self.calls.append({"kind": "photo", "id": mid, "body": caption,
                           "markup": reply_markup, "parse_mode": parse_mode,
                           "asset": str(getattr(photo, "path", photo))})
        return FakeMsg(mid, caption=caption)

    async def send_message(self, chat_id, text, parse_mode=None, reply_markup=None):
        mid = self._mid()
        self.calls.append({"kind": "text", "id": mid, "body": text,
                           "markup": reply_markup, "parse_mode": parse_mode,
                           "asset": None})
        return FakeMsg(mid, text=text)

    async def send_video(self, chat_id, video, caption=None, parse_mode=None,
                         reply_markup=None):
        mid = self._mid()
        self.calls.append({"kind": "video", "id": mid, "body": caption,
                           "markup": reply_markup, "parse_mode": parse_mode,
                           "asset": str(getattr(video, "path", video))})
        return FakeMsg(mid, caption=caption)

    async def send_media_group(self, chat_id, media):
        # Recorded rather than left to raise AttributeError, so an album sneaking
        # into the analysis stage fails as a readable assertion.
        out = []
        for item in media:
            mid = self._mid()
            self.calls.append({"kind": "album", "id": mid, "body": None,
                               "markup": None, "parse_mode": None,
                               "asset": str(getattr(item, "media", item))})
            out.append(FakeMsg(mid))
        return out

    async def delete_message(self, chat_id, message_id):
        self.calls.append({"kind": "delete", "id": message_id, "body": None,
                           "markup": None, "parse_mode": None, "asset": None})


class FakeUser:
    def __init__(self, tg_id, username=None):
        self.id = tg_id
        self.username = username


class FakeCB:
    """Minimal CallbackQuery: what the two signal handlers actually touch."""

    def __init__(self, tg_id, data, message_id):
        self.from_user = FakeUser(tg_id)
        self.data = data
        self.message = FakeMsg(message_id)
        self.answers = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))


class FakeMessage:
    """Minimal Message: what the two admin level commands actually touch."""

    def __init__(self, tg_id, text):
        self.from_user = FakeUser(tg_id)
        self.text = text
        self.replies = []

    async def answer(self, text=None, **kw):
        self.replies.append(text)


class FakeState:
    """Minimal FSMContext: the three calls the entry points make."""

    def __init__(self, state=None):
        self.state = state
        self.cleared = 0

    async def clear(self):
        self.state = None
        self.cleared += 1

    async def set_state(self, state):
        self.state = state

    async def get_state(self):
        return self.state


# --- harness ----------------------------------------------------------------

FAILURES = []
CHECKS = [0]


def check(label, condition, detail=""):
    CHECKS[0] += 1
    if condition:
        print("  ok   " + label)
    else:
        print("  FAIL " + label + ((" -- " + detail) if detail else ""))
        FAILURES.append(label)


async def drive(bot_mod, fake_bot, cb, sleeps):
    """Run one signal to completion and return the recorded calls."""
    tg_id = cb.from_user.id
    start = len(fake_bot.calls)
    sleeps.clear()
    if cb.data.startswith("m:"):
        await bot_mod.m_action(cb, fake_bot)
    else:
        await bot_mod.new_signal(cb, fake_bot)
    task = bot_mod._signal_tasks.get(tg_id)
    assert task is not None, "no signal task was started"
    await task
    return fake_bot.calls[start:]


def assert_layout(label, calls, config, wait_label):
    """The heart of the test: the exact message sequence, in order."""
    kinds = [c["kind"] for c in calls]

    # Everything before the first send is teardown of the tapped screen.
    first_send = next((i for i, k in enumerate(kinds) if k != "delete"), None)
    check(label + ": sends something", first_send is not None)
    if first_send is None:
        return
    seq = calls[first_send:]

    chart = seq[0]
    check(label + ": chart is its own text message", chart["kind"] == "text",
          "got " + chart["kind"])
    check(label + ": chart message is the chart emoji and nothing else",
          chart["body"] == config.SIGNAL_CHART, repr(chart["body"]))
    check(label + ": chart message is parsed as HTML",
          chart["parse_mode"] == "HTML")
    check(label + ": chart emoji is a custom emoji entity",
          "<tg-emoji" in config.SIGNAL_CHART)

    analysis = seq[1]
    expected = config.SIGNAL_ANALYZING.format(wait=wait_label)
    check(label + ": message 2 is the analysis text", analysis["kind"] == "text")
    check(label + ": analysis text matches exactly", analysis["body"] == expected,
          repr(analysis["body"]))
    check(label + ": analysis text does NOT contain the chart emoji",
          config.SIGNAL_CHART not in analysis["body"])
    check(label + ": analysis first line is bold",
          analysis["body"].startswith(config.T_SIG_LENS + " <b>"))
    check(label + ": blank line between the two paragraphs",
          "\n\n" in analysis["body"])

    # The finished signal is the last thing sent.
    final = [c for c in seq[2:] if c["kind"] != "delete"]
    check(label + ": the signal is delivered after the wait", len(final) == 1,
          str(len(final)) + " trailing sends")
    if final:
        body = final[0]["body"] or ""
        check(label + ": delivered message is the signal result",
              "Currency pair" in body, repr(body[:60]))

    # No media of any kind during the analysis stage. Everything the flow sends
    # before the finished signal must be a plain text message - the delivered
    # signal itself is a separate stage and is allowed to carry artwork.
    final_ids = {c["id"] for c in final}
    stage = [c for c in calls if c["kind"] != "delete" and c["id"] not in final_ids]
    check(label + ": analysis stage is exactly two messages", len(stage) == 2,
          str([c["kind"] for c in stage]))
    check(label + ": analysis stage sends no media at all",
          all(c["kind"] == "text" for c in stage),
          str([(c["kind"], c["asset"]) for c in stage if c["kind"] != "text"]))
    check(label + ": analysis stage references no asset file",
          all(not c["asset"] for c in stage),
          str([c["asset"] for c in stage if c["asset"]]))

    # Nothing from the waiting screen is left on the chat.
    deleted = {c["id"] for c in calls if c["kind"] == "delete"}
    waiting_ids = [c["id"] for c in stage]
    check(label + ": every waiting message is cleaned up",
          all(i in deleted for i in waiting_ids),
          "left behind: " + str([i for i in waiting_ids if i not in deleted]))


# --- levels, limits and the daily rollover ----------------------------------

def _fresh_user(fake_db, tg_id, premium=False, used=0, day=None):
    """Put one user in a known quota state and return their row."""
    fake_db._users[tg_id] = {
        "ui_msg_id": None, "album_ids": None, "is_premium": premium,
        "signals_used_today": used,
        "last_reset_date": day if day is not None else fake_db._today[0],
    }
    return fake_db._users[tg_id]


async def tap(bot_mod, fake_bot, tg_id, data, sleeps, message_id=700):
    """One signal tap, refusal included.

    Unlike drive(), this tolerates a tap the cap refuses - that path starts no
    task at all - and hands back the callback so the alert can be inspected.
    """
    cb = FakeCB(tg_id, data, message_id)
    start = len(fake_bot.calls)
    sleeps.clear()
    if data.startswith("m:"):
        await bot_mod.m_action(cb, fake_bot)
    else:
        await bot_mod.new_signal(cb, fake_bot)
    task = bot_mod._signal_tasks.get(tg_id)
    if task is not None:
        await task
    return fake_bot.calls[start:], cb


def _delivered(calls):
    return any("Currency pair" in (c["body"] or "") for c in calls)


def _snapshot(fake_db):
    """Deep-enough copy of every user row, for 'nothing else moved' checks."""
    return {tg: dict(row) for tg, row in fake_db._users.items()}


def _dirty_user(fake_db, tg_id):
    """A user mid-funnel: verified, with a UID, Premium, and quota spent."""
    fake_db._users[tg_id] = {
        "ui_msg_id": 555, "album_ids": "601,602,603", "is_premium": True,
        "signals_used_today": 12, "last_reset_date": fake_db._today[0],
        "verified": True, "uid": "123456789", "deposit": 250,
        "last_checked": "2026-08-25T10:00:00", "username": "tester",
    }
    return fake_db._users[tg_id]


async def devstart_tests(bot_mod, fake_db, config):
    print("\n[devstart] the reset is admin-only and scoped to one row")
    bot_src = open(os.path.join(ROOT, "bot.py"), encoding="utf-8").read()
    db_src = open(os.path.join(ROOT, "db.py"), encoding="utf-8").read()

    admin, outsider, bystander = 7101, 7102, 7103
    saved_admins = list(config.ADMIN_IDS)
    config.ADMIN_IDS = [admin]
    try:
        # --- a non-admin gets nothing and changes nothing --------------------
        _dirty_user(fake_db, outsider)
        _dirty_user(fake_db, bystander)
        before = _snapshot(fake_db)
        fake_bot = FakeBot()
        m = FakeMessage(outsider, "/devstart")
        state = FakeState(state="Reg:waiting_uid")
        await bot_mod.cmd_devstart(m, fake_bot, state)
        check("non-admin /devstart sends nothing at all",
              fake_bot.calls == [], str(fake_bot.calls))
        check("non-admin /devstart writes no reply", m.replies == [], str(m.replies))
        check("non-admin /devstart changes no database row",
              _snapshot(fake_db) == before, "a row moved")
        check("non-admin /devstart does not even reset its own sender",
              fake_db._users[outsider]["verified"] is True
              and fake_db._users[outsider]["uid"] == "123456789",
              str(fake_db._users[outsider]))
        check("non-admin /devstart leaves the FSM state alone",
              state.state == "Reg:waiting_uid" and state.cleared == 0,
              "%s / %s" % (state.state, state.cleared))

        # --- an admin resets their own row, and only their own ---------------
        row = _dirty_user(fake_db, admin)
        _dirty_user(fake_db, bystander)
        bystander_before = dict(fake_db._users[bystander])
        # Every row in the fake DB, not just the bystander's: the reset has to
        # be invisible to all of them, including the dozens the earlier
        # sections left behind.
        all_before = _snapshot(fake_db)
        # In-memory session state that must not survive the reset.
        bot_mod._pair_choice[admin] = "EUR/USD OTC"
        bot_mod._expiry_choice[admin] = "M7"
        bot_mod._pair_choice[bystander] = "GBP/JPY OTC"

        fake_bot = FakeBot()
        m = FakeMessage(admin, "/devstart")
        state = FakeState(state="Reg:waiting_uid")
        await bot_mod.cmd_devstart(m, fake_bot, state)
        row = fake_db._users[admin]

        check("verified becomes false", row["verified"] is False, str(row["verified"]))
        check("uid is cleared", row["uid"] is None, repr(row["uid"]))
        check("deposit is cleared", row["deposit"] == 0, str(row["deposit"]))
        check("the signal counter is reset",
              row["signals_used_today"] == 0, str(row["signals_used_today"]))
        check("the rollover date is cleared",
              row["last_reset_date"] is None, repr(row["last_reset_date"]))
        check("is_premium becomes false",
              row["is_premium"] is False, str(row["is_premium"]))
        check("album_ids is cleared", row["album_ids"] is None, repr(row["album_ids"]))
        check("last_checked is cleared alongside verified",
              row["last_checked"] is None, repr(row["last_checked"]))

        check("another user's row is untouched",
              fake_db._users[bystander] == bystander_before,
              str(fake_db._users[bystander]))
        all_after = _snapshot(fake_db)
        moved = [tg for tg in all_before
                 if all_before[tg] != all_after.get(tg)]
        check("no row other than the sender's was written",
              moved == [admin], str(moved))
        check("no new row was created", set(all_after) == set(all_before),
              str(set(all_after) - set(all_before)))

        check("the reset user is back on the Start limit",
              (await bot_mod._user_quota(admin))[1] == config.START_DAILY_SIGNALS,
              str(await bot_mod._user_quota(admin)))
        used, left = await bot_mod.db.signal_state(admin, config.START_DAILY_SIGNALS)
        check("the reset user has a full fresh allowance",
              (used, left) == (0, config.START_DAILY_SIGNALS), str((used, left)))

        # --- the old screen is torn down with the existing mechanism ---------
        deleted = [c["id"] for c in fake_bot.calls if c["kind"] == "delete"]
        check("the on-screen message is deleted", 555 in deleted, str(deleted))
        check("the album left on screen is deleted too",
              all(i in deleted for i in (601, 602, 603)), str(deleted))

        # --- and the funnel restarts exactly as it does for a new user -------
        sends = [c for c in fake_bot.calls if c["kind"] != "delete"]
        check("/devstart sends exactly one screen", len(sends) == 1,
              str([c["kind"] for c in sends]))
        gate = config.SCREENS["gate"]
        check("/devstart lands on the gate screen",
              gate["text"] in (sends[-1]["body"] or ""), repr(sends[-1]["body"]))
        check("/devstart shows the gate artwork",
              (sends[-1]["asset"] or "").replace("\\", "/").endswith("assets/gate.jpg"),
              repr(sends[-1]["asset"]))
        check("the new gate message becomes the tracked screen",
              row["ui_msg_id"] == sends[-1]["id"],
              "%s vs %s" % (row["ui_msg_id"], sends[-1]["id"]))
        check("/devstart clears the FSM state",
              state.state is None and state.cleared == 1,
              "%s / %s" % (state.state, state.cleared))
        check("the sender's in-memory selections are dropped",
              admin not in bot_mod._pair_choice and admin not in bot_mod._expiry_choice,
              str((bot_mod._pair_choice.get(admin), bot_mod._expiry_choice.get(admin))))
        check("another user's in-memory selection survives",
              bot_mod._pair_choice.get(bystander) == "GBP/JPY OTC",
              repr(bot_mod._pair_choice.get(bystander)))

        # --- /devstart and /start produce the same opening screen ------------
        fresh = 7104
        start_bot = FakeBot()
        await bot_mod.start(FakeMessage(fresh, "/start"), start_bot, FakeState())
        start_sends = [(c["kind"], c["asset"], c["body"]) for c in start_bot.calls
                       if c["kind"] != "delete"]
        dev_sends = [(c["kind"], c["asset"], c["body"]) for c in sends]
        check("/devstart opens the identical screen a new user gets",
              start_sends == dev_sends,
              str(start_sends) + " vs " + str(dev_sends))

        # --- the production /start is unchanged ------------------------------
        print("\n[devstart] /start still does not reset anything")
        keeper = 7105
        _dirty_user(fake_db, keeper)
        before = dict(fake_db._users[keeper])
        await bot_mod.start(FakeMessage(keeper, "/start"), FakeBot(), FakeState())
        after = fake_db._users[keeper]
        check("/start leaves verified alone", after["verified"] is True)
        check("/start leaves the uid alone", after["uid"] == "123456789",
              repr(after["uid"]))
        check("/start leaves Premium alone", after["is_premium"] is True)
        check("/start leaves the signal counter alone",
              after["signals_used_today"] == 12, str(after["signals_used_today"]))
        check("/start leaves the deposit alone", after["deposit"] == 250)
        check("/start still only repoints the tracked screen",
              {k: v for k, v in after.items() if k != "ui_msg_id"}
              == {k: v for k, v in before.items() if k != "ui_msg_id"},
              str(after))
        check("/start itself is never gated on ADMIN_IDS",
              not _is_admin_gated_start(bot_src),
              "the /start handler grew an ADMIN_IDS check")

        # A verified admin must still reach the menu through /start - /devstart
        # is the only thing that sends them back down the funnel.
        vip = 7106
        _dirty_user(fake_db, vip)
        menu_bot = FakeBot()
        await bot_mod.start(FakeMessage(vip, "/start"), menu_bot, FakeState())
        body = menu_bot.calls[-1]["body"] or ""
        check("a verified user's /start goes straight to the menu",
              "Go+ main menu" in body, repr(body[:60]))
        check("that menu still shows their tier",
              "\U0001F3C6 Premium" in body, repr(body))
    finally:
        config.ADMIN_IDS = saved_admins

    # --- structural guarantees the fakes cannot prove ------------------------
    print("\n[devstart] the reset statement itself")
    check("the reset is a single UPDATE, so it cannot half-apply",
          db_src.count("UPDATE users\n       SET verified") == 1
          and "_RESET_SQL" in db_src)
    check("the reset is scoped to one tg_id",
          "WHERE tg_id = $1\n    RETURNING tg_id" in db_src)
    check("every required column is in the reset",
          all(col in db_src.split("_RESET_SQL")[1].split('"""')[1]
              for col in ("verified", "uid", "deposit", "signals_used_today",
                          "last_reset_date", "is_premium", "ui_msg_id",
                          "album_ids")))
    check("/devstart is registered above the UID capture handler",
          bot_src.index('Command("devstart")') < bot_src.index("Reg.waiting_uid)"))
    check("/devstart wipes the screen before nulling the ids it needs",
          bot_src.index("await wipe(bot, tg_id)")
          < bot_src.index("await db.reset_user(tg_id)"))
    check("/devstart reuses the shared ADMIN_IDS gate",
          "if not _is_admin(tg_id):" in bot_src
          and "return tg_id in config.ADMIN_IDS" in bot_src)
    # /start is upstream's, not ours: it branches verified users to the menu.
    # These pin that shape so a later edit here cannot quietly revert it.
    check("/start keeps upstream's verified shortcut",
          'if user and user["verified"]:' in bot_src
          and "await _show_menu(bot, tg_id)" in bot_src)
    check("/start still opens the gate for everyone else",
          'await show(bot, tg_id, "gate")' in bot_src)
    check("/devstart delegates to /start rather than reimplementing it",
          "await start(m, bot, state)" in bot_src)
    check("/devstart clears the nudge before the reset nulls its id",
          bot_src.index("await _clear_nudge(bot, tg_id)")
          < bot_src.index("await db.reset_user(tg_id)"))
    check("/devstart drops the per-user panel cooldown",
          "_uid_lookup_at.pop(tg_id, None)" in bot_src)
    check("upstream's /unverify survives alongside /devstart",
          'Command("unverify")' in bot_src and "async def unverify(tg_id" in db_src)
    # The narrow helper must not clear the uid (its comment mentions uid, so
    # this looks at what the statement SETs, not at the prose around it).
    _unverify_sql = db_src.split("async def unverify(")[1].split("async def")[0]
    check("the two reset helpers stay distinct - unverify keeps the uid",
          "uid=" not in _unverify_sql and "uid =" not in _unverify_sql,
          "unverify started clearing the uid")
    check("the wide helper does clear the uid",
          "uid                = NULL" in db_src)
    check("every daily-cap check is per-user",
          "config.DAILY_SIGNAL_LIMIT" not in bot_src
          and bot_src.count("await _user_quota(tg_id)") >= 4,
          "a cap check still reads the global limit")


def _is_admin_gated_start(bot_src):
    # Only start()'s own body: stop at the next handler's decorator, or the
    # admin commands registered just below it get read as part of /start.
    body = bot_src.split("async def start(", 1)[1].split("\n@dp.", 1)[0]
    return "_is_admin" in body or "ADMIN_IDS" in body


async def level_tests(bot_mod, fake_db, config, sleeps):
    import importlib

    bot_src = open(os.path.join(ROOT, "bot.py"), encoding="utf-8").read()
    db_src = open(os.path.join(ROOT, "db.py"), encoding="utf-8").read()
    cfg_src = open(os.path.join(ROOT, "config.py"), encoding="utf-8").read()

    # --- the two limits are configuration, not source constants --------------
    print("\n[levels] both limits come from the environment")
    check("START_DAILY_SIGNALS defaults to 30", config.START_DAILY_SIGNALS == 30,
          str(config.START_DAILY_SIGNALS))
    check("PREMIUM_DAILY_SIGNALS defaults to 70",
          config.PREMIUM_DAILY_SIGNALS == 70, str(config.PREMIUM_DAILY_SIGNALS))
    check("the legacy DAILY_SIGNAL_LIMIT still resolves to the Start limit",
          config.DAILY_SIGNAL_LIMIT == config.START_DAILY_SIGNALS)
    check("daily_limit(False) is the Start limit",
          config.daily_limit(False) == config.START_DAILY_SIGNALS)
    check("daily_limit(True) is the Premium limit",
          config.daily_limit(True) == config.PREMIUM_DAILY_SIGNALS)
    check("Start renders as the green label",
          config.level_label(False) == "\U0001F7E2 Start",
          repr(config.level_label(False)))
    check("Premium renders as the trophy label",
          config.level_label(True) == "\U0001F3C6 Premium",
          repr(config.level_label(True)))
    check("bot.py resolves every limit through config.daily_limit",
          "config.daily_limit" in bot_src and "DAILY_SIGNAL_LIMIT" not in bot_src,
          "bot.py still names a limit constant of its own")
    # Scoped to the limit logic: elsewhere in config.py "70" and "30" turn up
    # inside emoji IDs, \\U escapes and ad copy, none of which is a limit.
    quota_block = cfg_src[cfg_src.index("# --- Levels and the daily signal quota"):
                          cfg_src.index("MSG_DAILY_LIMIT")]
    # Comments are allowed to mention the numbers; code is not, beyond the one
    # os.getenv default each.
    quota_code = "\n".join(l for l in quota_block.splitlines()
                           if not l.lstrip().startswith("#"))
    check("70 appears in the limit logic only as the PREMIUM_DAILY_SIGNALS default",
          quota_code.count("70") == 1
          and '_int_env("PREMIUM_DAILY_SIGNALS", 70)' in quota_code,
          "a 70 is hardcoded in the limit logic")
    check("30 appears in the limit logic only as the START_DAILY_SIGNALS default",
          quota_code.count("30") == 1
          and '_int_env("DAILY_SIGNAL_LIMIT", 30)' in quota_code,
          "a 30 is hardcoded in the limit logic")

    # --- a Start user gets exactly 30 ---------------------------------------
    print("\n[limit] Start users get exactly %d signals" % config.START_DAILY_SIGNALS)
    tg_id = 30001
    _fresh_user(fake_db, tg_id)
    premium, limit = await bot_mod._user_quota(tg_id)
    check("an is_premium=FALSE user resolves to Start",
          premium is False and limit == 30, "%s / %s" % (premium, limit))
    delivered = 0
    for _ in range(config.START_DAILY_SIGNALS):
        calls, _cb = await tap(bot_mod, FakeBot(), tg_id, "m:1", sleeps)
        delivered += 1 if _delivered(calls) else 0
    check("all 30 Start signals are delivered", delivered == 30, str(delivered))
    check("the counter stops at exactly 30",
          fake_db._users[tg_id]["signals_used_today"] == 30,
          str(fake_db._users[tg_id]["signals_used_today"]))

    calls, cb = await tap(bot_mod, FakeBot(), tg_id, "m:1", sleeps)
    check("signal 31 is refused", not _delivered(calls))
    check("the refusal is an alert, not a screen",
          cb.answers and cb.answers[-1] == (config.MSG_DAILY_LIMIT, True),
          str(cb.answers))
    check("a refused tap starts no countdown", not sleeps, str(sleeps))
    check("a refused tap touches no message", calls == [], str(calls))
    check("a refused tap does not increment the counter",
          fake_db._users[tg_id]["signals_used_today"] == 30,
          str(fake_db._users[tg_id]["signals_used_today"]))

    # --- Premium users get the configured Premium limit ----------------------
    print("\n[limit] Premium users get the configured Premium limit")
    pro = 30002
    _fresh_user(fake_db, pro, premium=True)
    premium, limit = await bot_mod._user_quota(pro)
    check("an is_premium=TRUE user resolves to Premium",
          premium is True and limit == config.PREMIUM_DAILY_SIGNALS,
          "%s / %s" % (premium, limit))
    used, left = await bot_mod.db.signal_state(pro, limit)
    check("a fresh Premium user has the full Premium allowance",
          (used, left) == (0, 70), str((used, left)))

    # A Start user's 31st tap is refused; a Premium user's is not - same code
    # path, same day, only the tier differs.
    _fresh_user(fake_db, pro, premium=True, used=30)
    calls, _cb = await tap(bot_mod, FakeBot(), pro, "m:1", sleeps)
    check("signal 31 IS delivered to a Premium user", _delivered(calls))
    _fresh_user(fake_db, pro, premium=True, used=70)
    calls, cb = await tap(bot_mod, FakeBot(), pro, "m:1", sleeps)
    check("signal 71 is refused at the Premium cap", not _delivered(calls))
    check("the Premium refusal is the same alert",
          cb.answers and cb.answers[-1] == (config.MSG_DAILY_LIMIT, True),
          str(cb.answers))

    # --- the menu and My level show the user's own tier ----------------------
    print("\n[ui] menu and My level are per-user, not static")
    _fresh_user(fake_db, tg_id)
    fake_bot = FakeBot()
    await bot_mod._show_menu(fake_bot, tg_id)
    body = fake_bot.calls[-1]["body"] or ""
    check("Start menu names the Start level", "\U0001F7E2 Start" in body, repr(body))
    check("Start menu shows 30 available",
          "Available today: 30 signals" in body, repr(body))
    check("Start menu shows 0 used and 30 left",
          "Used: 0" in body and "Left: 30" in body, repr(body))
    check("no unfilled placeholder is left on the menu caption",
          "{" not in body and "}" not in body, repr(body))

    _fresh_user(fake_db, pro, premium=True)
    fake_bot = FakeBot()
    await bot_mod._show_menu(fake_bot, pro)
    body = fake_bot.calls[-1]["body"] or ""
    check("Premium menu names the Premium level",
          "\U0001F3C6 Premium" in body, repr(body))
    check("Premium menu shows 70 available",
          "Available today: 70 signals" in body, repr(body))
    check("Premium menu never shows the Start label",
          "\U0001F7E2 Start" not in body, repr(body))

    fake_bot = FakeBot()
    await bot_mod.menu_level(FakeCB(pro, "menu:level", 700), fake_bot)
    last = fake_bot.calls[-1]
    body = last["body"] or ""
    check("My level is a text-only screen", last["kind"] == "text", last["kind"])
    check("My level leads with the Premium icon and names the tier",
          body.startswith("\U0001F3C6 <b>Your current level:</b> Premium"), repr(body))
    check("My level shows that tier's daily limit",
          "\U0001F4CA <b>Daily limit:</b> 70 signals" in body, repr(body))
    check("My level shows used today", "\U0001F4C8 <b>Used today:</b> 0" in body,
          repr(body))
    check("My level shows remaining today",
          "\U000026A1 <b>Remaining today:</b> 70" in body, repr(body))
    check("My level keeps a way back", last["markup"] is not None)
    check("My level no longer answers 'Coming soon'", "Coming soon" not in body)
    check("no unfilled placeholder is left on My level",
          "{" not in body and "}" not in body, repr(body))

    # The same screen for a Start user, in the four-line shape the spec gives.
    _fresh_user(fake_db, tg_id)
    fake_bot = FakeBot()
    await bot_mod.menu_level(FakeCB(tg_id, "menu:level", 700), fake_bot)
    body = fake_bot.calls[-1]["body"] or ""
    check("My level shows Start for a Start user",
          body.startswith("\U0001F7E2 <b>Your current level:</b> Start"), repr(body))
    check("My level shows the Start limit, not the Premium one",
          "\U0001F4CA <b>Daily limit:</b> 30 signals" in body
          and "70" not in body, repr(body))
    check("a Start user is never shown the Premium tier",
          "Premium" not in body, repr(body))

    # --- the Unlock Premium screen ------------------------------------------
    print("\n[premium] the Unlock Premium screen")
    menu_kb = config.SCREENS["menu"]["kb"]
    labels = [b[0] for row in menu_kb for b in row]
    actions = [b[1] for row in menu_kb for b in row]
    check("the VIP team button is gone from the main menu",
          not any("VIP" in l for l in labels), str(labels))
    check("no menu button still points at VIP_LINK",
          not any(config.VIP_LINK in a for a in actions), str(actions))
    check("Unlock Premium appears in its place",
          "Unlock Premium" in labels, str(labels))
    check("Unlock Premium sits where VIP team was (row 4)",
          menu_kb[3][0][0] == "Unlock Premium", str(menu_kb[3]))
    check("Unlock Premium opens a screen rather than a link",
          "cb:menu:premium" in actions, str(actions))

    # --- the blue main menu -------------------------------------------------
    print("\n[menu] every button is blue and Pocket Option is gone")
    check("the Pocket Option button is gone",
          not any("Pocket Option" in l for l in labels), str(labels))
    check("no menu button still points at REF_LINK",
          not any(config.REF_LINK in a for a in actions), str(actions))
    # REF_LINK has a second, unrelated user: the register button on the
    # verification-failure screens. Removing the menu row must not break it.
    reg = bot_mod._register_btn()
    check("the register button still uses REF_LINK",
          config.REF_LINK in reg[0][0][1], str(reg))

    styles = [(b[0], b[2] if len(b) > 2 else None) for row in menu_kb for b in row]
    check("every main-menu button is styled primary (blue)",
          all(s == "primary" for _l, s in styles), str(styles))
    check("no main-menu button is left green or unstyled",
          not any(s in ("success", "danger", None) for _l, s in styles), str(styles))
    # "primary" is the Bot API's blue; the field is real, not decoration.
    from aiogram.types import InlineKeyboardButton as _IKB
    check("style is a declared Bot API field, so blue actually renders",
          "style" in _IKB.model_fields, "aiogram dropped InlineKeyboardButton.style")
    check("icon_custom_emoji_id is a declared Bot API field too",
          "icon_custom_emoji_id" in _IKB.model_fields)

    check("the six required buttons are present, in order",
          [l for l in labels] == ["Get a signal", "My level", "Support",
                                  "Unlock Premium",
                                  "Telegram channel", "YouTube channel"],
          str(labels))
    check("the menu is now six rows", len(menu_kb) == 6, str(len(menu_kb)))

    # --- the supplied custom emoji IDs, one per button ----------------------
    print("\n[emoji] every main-menu button carries its supplied custom emoji")
    icons = {b[0]: (b[3] if len(b) > 3 else None) for row in menu_kb for b in row}
    SUPPLIED = {
        "Get a signal":              "5188481279963715781",
        "My level":                  "5244837092042750681",
        "Support":                   "5443038326535759644",
        "Unlock Premium":            "5431684550424011313",
        "Telegram channel":          "5231489647946768652",
        "YouTube channel":           "5897969921182142023",
    }
    for label, want in SUPPLIED.items():
        check("%s -> %s" % (label, want), icons.get(label) == want,
              repr(icons.get(label)))
    check("every button has an icon; none was left bare",
          all(v for v in icons.values()), str(icons))
    check("no two buttons share an icon id",
          len(set(icons.values())) == 6, str(icons))
    check("no emoji id outside the supplied set is used",
          set(icons.values()) == set(SUPPLIED.values()), str(set(icons.values())))
    # Every ID is a bare numeric string - a malformed one makes Telegram reject
    # the whole message, taking the menu down.
    check("every icon id is a plain numeric id",
          all(v.isdigit() for v in icons.values()), str(icons))

    # The leading unicode emoji had to go: Telegram draws the custom icon before
    # the label, so leaving them would render two emoji per button.
    check("labels no longer carry their own leading emoji",
          all(l[0].isalpha() for l in labels), str(labels))
    # Premium is no exception any more: its one crown is the custom emoji, so
    # the label is bare text like every other button.
    check("Unlock Premium carries exactly one crown, and it is the custom one",
          labels[3] == "Unlock Premium"
          and icons["Unlock Premium"] == "5431684550424011313",
          "%r / %r" % (labels[3], icons.get("Unlock Premium")))
    check("the second, right-hand crown is gone from the label",
          "\U0001F451" not in labels[3], repr(labels[3]))
    check("no unicode crown is left anywhere in the menu labels",
          not any("\U0001F451" in l for l in labels), str(labels))
    check("the superseded Premium emoji id is no longer referenced",
          "5433758796289685818" not in open("config.py", encoding="utf-8").read(),
          "the old Premium emoji id is still in config.py")
    check("the trailing-crown constant is gone with the crown",
          not hasattr(config, "E_MENU_PREMIUM_TRAILING"),
          "E_MENU_PREMIUM_TRAILING outlived the button it described")
    # One icon slot per button is what makes a single crown the only option.
    from aiogram.types import InlineKeyboardButton as _IKB2
    check("a button still has exactly one custom-emoji slot",
          [f for f in _IKB2.model_fields if "custom_emoji" in f]
          == ["icon_custom_emoji_id"],
          str([f for f in _IKB2.model_fields if "custom_emoji" in f]))

    # The URLs and callbacks the buttons carry must be untouched.
    wired = {b[0]: b[1] for row in menu_kb for b in row}
    check("Support still opens the support URL",
          wired["Support"] == "url:" + config.SUPPORT_URL)
    check("Telegram channel still opens the channel URL",
          wired["Telegram channel"] == "url:" + config.CHANNEL_URL)
    check("YouTube still opens the YouTube URL",
          wired["YouTube channel"] == "url:" + config.YOUTUBE_URL)
    check("Get a signal still opens the mode picker",
          wired["Get a signal"] == "cb:menu:signal")
    check("My level still opens the level screen",
          wired["My level"] == "cb:menu:level")
    check("Unlock Premium still opens the premium screen",
          wired["Unlock Premium"] == "cb:menu:premium")

    # build_kb must actually forward style/icon onto the outgoing button.
    built = bot_mod.build_kb(menu_kb)
    flat = [b for row in built.inline_keyboard for b in row]
    check("build_kb forwards style onto every rendered button",
          all(b.style == "primary" for b in flat), str([b.style for b in flat]))
    check("build_kb forwards the YouTube icon id",
          any(b.icon_custom_emoji_id == "5897969921182142023" for b in flat))
    check("no button was dropped for a bad URL", len(flat) == 6, str(len(flat)))

    _fresh_user(fake_db, tg_id)
    fake_bot = FakeBot()
    await bot_mod.menu_premium(FakeCB(tg_id, "menu:premium", 700), fake_bot)
    last = fake_bot.calls[-1]
    body = last["body"] or ""
    check("the Premium screen is text-only", last["kind"] == "text", last["kind"])
    check("it is headed Premium Level",
          body.startswith("\U0001F3C6 <b>Premium Level</b>"), repr(body))
    check("it lists the benefits", "<b>Premium benefits:</b>" in body, repr(body))
    check("it quotes the configured Premium allowance",
          "\U00002014 70 signals/day" in body, repr(body))
    check("it shows a Start viewer their own current status",
          "<b>Your status:</b> Start" in body and "30 signals/day" in body,
          repr(body))
    check("it never asks for a deposit",
          "deposit" not in body.lower(), repr(body))
    check("a Start viewer is offered a way to ask about Premium",
          last["markup"] is not None)
    check("no unfilled placeholder on the Premium screen",
          "{" not in body and "}" not in body, repr(body))

    _fresh_user(fake_db, pro, premium=True)
    fake_bot = FakeBot()
    await bot_mod.menu_premium(FakeCB(pro, "menu:premium", 700), fake_bot)
    body = fake_bot.calls[-1]["body"] or ""
    check("a Premium viewer is told Premium is active",
          "Premium is active" in body, repr(body))
    check("and is not invited to request what they already have",
          "Ask about Premium" not in str(fake_bot.calls[-1]["markup"]),
          str(fake_bot.calls[-1]["markup"]))
    check("the Premium screen quotes one number for both tiers' viewers",
          "\U00002014 70 signals/day" in body, repr(body))
    check("menu:level is registered above the menu: catch-all",
          bot_src.index('F.data == "menu:level"')
          < bot_src.index('F.data.startswith("menu:")'))

    # --- changing PREMIUM_DAILY_SIGNALS changes what is enforced -------------
    print("\n[config] PREMIUM_DAILY_SIGNALS 70 -> 100 moves the enforced cap")
    saved = os.environ.get("PREMIUM_DAILY_SIGNALS")
    saved_admins = list(config.ADMIN_IDS)
    try:
        os.environ["PREMIUM_DAILY_SIGNALS"] = "100"
        importlib.reload(config)
        check("the new value is picked up on restart",
              config.PREMIUM_DAILY_SIGNALS == 100, str(config.PREMIUM_DAILY_SIGNALS))
        check("daily_limit(True) follows the variable",
              config.daily_limit(True) == 100, str(config.daily_limit(True)))
        check("the Start limit is untouched by the Premium change",
              config.START_DAILY_SIGNALS == 30, str(config.START_DAILY_SIGNALS))

        # The same user, the same day, the same 70 already spent: refused under
        # 70, delivered under 100. This is the enforced limit moving, not the
        # displayed one.
        _fresh_user(fake_db, pro, premium=True, used=70)
        calls, _cb = await tap(bot_mod, FakeBot(), pro, "m:1", sleeps)
        check("signal 71 IS delivered once the variable says 100",
              _delivered(calls))
        _fresh_user(fake_db, pro, premium=True, used=100)
        calls, cb = await tap(bot_mod, FakeBot(), pro, "m:1", sleeps)
        check("signal 101 is refused at the new cap", not _delivered(calls))
        check("the cap alert still fires at 100",
              cb.answers and cb.answers[-1] == (config.MSG_DAILY_LIMIT, True),
              str(cb.answers))

        # A Start user must not inherit any part of the Premium change: same
        # day, same variable, still capped at 30.
        _fresh_user(fake_db, tg_id, used=30)
        calls, cb = await tap(bot_mod, FakeBot(), tg_id, "m:1", sleeps)
        check("a Start user is still refused at 30 while Premium is 100",
              not _delivered(calls), str(calls))
        _fresh_user(fake_db, tg_id)
        _, start_limit = await bot_mod._user_quota(tg_id)
        check("a Start user never receives the Premium limit",
              start_limit == 30, str(start_limit))

        # Both Premium-facing screens follow the variable too, so the number a
        # user is shown and the number enforced can never diverge.
        _fresh_user(fake_db, pro, premium=True)
        fake_bot = FakeBot()
        await bot_mod.menu_level(FakeCB(pro, "menu:level", 700), fake_bot)
        body = fake_bot.calls[-1]["body"] or ""
        check("My level shows 100 after the variable change",
              "\U0001F4CA <b>Daily limit:</b> 100 signals" in body, repr(body))
        fake_bot = FakeBot()
        await bot_mod.menu_premium(FakeCB(pro, "menu:premium", 700), fake_bot)
        body = fake_bot.calls[-1]["body"] or ""
        check("the Unlock Premium screen advertises 100, not 70",
              "\U00002014 100 signals/day" in body and "70" not in body, repr(body))

        _fresh_user(fake_db, pro, premium=True)
        fake_bot = FakeBot()
        await bot_mod._show_menu(fake_bot, pro)
        body = fake_bot.calls[-1]["body"] or ""
        check("the menu shows 100 available without a code change",
              "Available today: 100 signals" in body, repr(body))
        check("the menu shows 100 left", "Left: 100" in body, repr(body))
    finally:
        if saved is None:
            os.environ.pop("PREMIUM_DAILY_SIGNALS", None)
        else:
            os.environ["PREMIUM_DAILY_SIGNALS"] = saved
        importlib.reload(config)
        config.ADMIN_IDS = saved_admins
    check("the Premium limit returns to 70 when the variable is unset",
          config.PREMIUM_DAILY_SIGNALS == 70, str(config.PREMIUM_DAILY_SIGNALS))

    # --- only admins can change a tier --------------------------------------
    print("\n[admin] only ADMIN_IDS can change a tier")
    admin, outsider, target = 4242, 9999, 30003
    saved_admins = list(config.ADMIN_IDS)
    config.ADMIN_IDS = [admin]
    try:
        _fresh_user(fake_db, target)
        m = FakeMessage(outsider, "/premium %d" % target)
        await bot_mod.cmd_premium(m)
        check("a non-admin gets no reply at all", m.replies == [], str(m.replies))
        check("a non-admin cannot grant Premium",
              fake_db._users[target]["is_premium"] is False)
        _, limit = await bot_mod._user_quota(target)
        check("the target is still held to the Start limit", limit == 30, str(limit))

        m = FakeMessage(outsider, "/startlevel %d" % admin)
        _fresh_user(fake_db, admin, premium=True)
        await bot_mod.cmd_startlevel(m)
        check("a non-admin cannot revoke Premium either",
              fake_db._users[admin]["is_premium"] is True and m.replies == [],
              str(m.replies))

        m = FakeMessage(admin, "/premium %d" % target)
        await bot_mod.cmd_premium(m)
        check("an admin can grant Premium",
              fake_db._users[target]["is_premium"] is True)
        check("the confirmation names the level and the configured limit",
              m.replies and "\U0001F3C6 Premium" in m.replies[-1]
              and "70" in m.replies[-1], str(m.replies))
        _, limit = await bot_mod._user_quota(target)
        check("the granted user is now held to the Premium limit",
              limit == 70, str(limit))

        m = FakeMessage(admin, "/startlevel %d" % target)
        await bot_mod.cmd_startlevel(m)
        check("an admin can move a user back to Start",
              fake_db._users[target]["is_premium"] is False)
        _, limit = await bot_mod._user_quota(target)
        check("the demoted user is back on the Start limit", limit == 30, str(limit))

        m = FakeMessage(admin, "/premium 987654321")
        await bot_mod.cmd_premium(m)
        check("an unknown tg_id is reported, not silently accepted",
              m.replies and "987654321" in m.replies[-1]
              and "No user" in m.replies[-1], str(m.replies))

        m = FakeMessage(admin, "/premium")
        await bot_mod.cmd_premium(m)
        check("a malformed command answers with usage",
              m.replies and m.replies[-1].startswith("Usage:"), str(m.replies))

        m = FakeMessage(admin, "/premium notanid")
        await bot_mod.cmd_premium(m)
        check("a non-numeric tg_id answers with usage",
              m.replies and m.replies[-1].startswith("Usage:"), str(m.replies))

        check("ADMIN_IDS is empty by default, so nobody can change tiers unset",
              config.ADMIN_IDS is not None and saved_admins == [], str(saved_admins))
    finally:
        config.ADMIN_IDS = saved_admins

    # --- the daily rollover --------------------------------------------------
    print("\n[quota] usage resets at the start of a new day")
    roll = 30004
    _fresh_user(fake_db, roll, used=config.START_DAILY_SIGNALS)
    calls, cb = await tap(bot_mod, FakeBot(), roll, "m:1", sleeps)
    check("a capped user is refused before the rollover", not _delivered(calls))

    fake_db._today[0] = "2026-08-26"        # midnight UTC passes
    used, left = await bot_mod.db.signal_state(roll, 30)
    check("the new day reads as 0 used", used == 0, str(used))
    check("the new day restores the full allowance", left == 30, str(left))
    check("the stored counter itself was rewritten, not just the display",
          fake_db._users[roll]["signals_used_today"] == 0
          and fake_db._users[roll]["last_reset_date"] == "2026-08-26",
          str(fake_db._users[roll]))
    calls, _cb = await tap(bot_mod, FakeBot(), roll, "m:1", sleeps)
    check("signals flow again on the new day", _delivered(calls))
    check("the new day's first signal counts as 1",
          fake_db._users[roll]["signals_used_today"] == 1,
          str(fake_db._users[roll]["signals_used_today"]))

    # A Premium user rolls over the same way and comes back to the Premium
    # allowance, not the Start one.
    _fresh_user(fake_db, pro, premium=True, used=70, day="2026-08-26")
    fake_db._today[0] = "2026-08-27"
    _, limit = await bot_mod._user_quota(pro)
    used, left = await bot_mod.db.signal_state(pro, limit)
    check("a Premium user rolls over to the Premium allowance",
          (used, left) == (0, 70), str((used, left)))

    # The stub above only mirrors the real statements; these keep the SQL that
    # actually enforces and resets from drifting out from under it.
    print("\n[sql] the statements the stub mirrors are still in db.py")
    check("the reset is still keyed on the Postgres CURRENT_DATE",
          "last_reset_date IS DISTINCT FROM CURRENT_DATE" in db_src)
    check("the cap is still inside the atomic UPDATE's WHERE",
          "signals_used_today < $2" in db_src)
    check("the limit is still a parameter, never a literal, in db.py",
          "30" not in db_src and "70" not in db_src, "db.py names a limit")
    check("is_premium is added idempotently, defaulting existing rows to Start",
          "ADD COLUMN IF NOT EXISTS is_premium BOOLEAN NOT NULL DEFAULT FALSE"
          in db_src)
    check("set_premium reports an unknown tg_id instead of succeeding",
          "RETURNING is_premium" in db_src)


async def main():
    fake_db = _install_stub_modules()
    bot_mod = _load_bot()
    import config

    # Record the requested delay instead of sitting through it.
    real_sleep = asyncio.sleep
    sleeps = []

    async def fake_sleep(delay, *a, **k):
        sleeps.append(delay)
        return await real_sleep(0)

    asyncio.sleep = fake_sleep
    try:
        wait_label = bot_mod._wait_label(config.SIGNAL_COUNTDOWN)
        print("SIGNAL_COUNTDOWN = %s  ->  wait label %r"
              % (config.SIGNAL_COUNTDOWN, wait_label))
        check("countdown is 30 seconds", config.SIGNAL_COUNTDOWN == 30,
              str(config.SIGNAL_COUNTDOWN))
        check("wait label renders as 00:30", wait_label == "00:30", repr(wait_label))

        # --- the expiration screen itself must be untouched ------------------
        print("\n[ui] expiration selection screen is unchanged")
        exp = config.SCREENS["test_menu"]
        check("expiration screen still shows expiration_time.jpg",
              exp["photo"] == "expiration_time", repr(exp["photo"]))
        check("no waiting-image constant is left to point an asset at",
              not hasattr(config, "SIGNAL_WAIT_PHOTO"),
              "config.SIGNAL_WAIT_PHOTO still exists")
        actions = [b[1] for row in exp["kb"] for b in row]
        m_opts = sorted(int(a.split(":")[2]) for a in actions if a.startswith("cb:m:"))
        s_opts = sorted(int(a.split(":")[2]) for a in actions if a.startswith("cb:s:"))
        check("all ten M expirations still offered",
              m_opts == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10], str(m_opts))
        check("all nine S expirations still offered",
              s_opts == [5, 10, 15, 20, 25, 30, 45, 50, 55], str(s_opts))
        check("expiration screen button count unchanged", len(actions) == 19,
              str(len(actions)))

        # --- path 1: every unlocked M button on the expiration screen --------
        print("\n[path] m_action - every expiration button")
        for n in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10):
            tg_id = 5000 + n
            fake_bot = FakeBot()
            cb = FakeCB(tg_id, "m:%d" % n, message_id=900 + n)
            fake_db._users[tg_id] = {"ui_msg_id": 900 + n, "album_ids": None}
            calls = await drive(bot_mod, fake_bot, cb, sleeps)
            assert_layout("M%d" % n, calls, config, wait_label)
            check("M%d: waited the full countdown, not the expiration" % n,
                  len(sleeps) == 1 and 29 < sleeps[0] <= config.SIGNAL_COUNTDOWN,
                  str(sleeps))
            check("M%d: expiration reaches the result screen" % n,
                  any("M%d" % n in (c["body"] or "") for c in calls))
            check("M%d: selected expiration is still stored internally" % n,
                  bot_mod._expiry_choice.get(tg_id) == "M%d" % n,
                  repr(bot_mod._expiry_choice.get(tg_id)))

        # --- path 2: "New Signal" opens the currency-pair picker -------------
        # It used to repeat the previous signal immediately. It now sends the
        # user back to pair selection, so there is no countdown on this path.
        print("\n[path] new_signal - opens the pair picker at page 1")
        tg_id = 6001
        fake_bot = FakeBot()
        fake_db._users[tg_id] = {"ui_msg_id": 950, "album_ids": None}
        sleeps.clear()
        cb = FakeCB(tg_id, "new_signal", message_id=950)
        await bot_mod.new_signal(cb, fake_bot)
        calls = fake_bot.calls
        check("new_signal: starts no countdown",
              bot_mod._signal_tasks.get(tg_id) is None)
        check("new_signal: schedules no sleep", not sleeps, str(sleeps))
        check("new_signal: removes the tapped signal screen",
              any(c["kind"] == "delete" and c["id"] == 950 for c in calls),
              str([(c["kind"], c["id"]) for c in calls]))
        check("new_signal: renders the pair screen image",
              any(config.SCREENS["pairs"]["photo"] in (c["asset"] or "")
                  for c in calls),
              str([(c["kind"], c["asset"]) for c in calls]))
        check("new_signal: pager reset to page 1",
              any(("1/%d" % config.PAIR_PAGES) in str(c["markup"] or "")
                  for c in calls))
        signal_assets = [k for _, k in config.SIGNAL_DIRECTIONS]
        check("new_signal: produces no signal result",
              not any(any(a in (c["asset"] or "") for a in signal_assets)
                      for c in calls),
              str([(c["kind"], c["asset"]) for c in calls]))

        # --- the daily cap still gates this path -----------------------------
        # New Signal no longer runs through _start_signal, so it carries its own
        # cap check. Without it the button would be a way around the limit.
        print("\n[edge] new_signal still respects the daily cap")
        tg_id = 6002
        fake_bot = FakeBot()
        fake_db._users[tg_id] = {"ui_msg_id": 951, "album_ids": None}
        # fake_db is the same module object as sys.modules["db"], which the
        # later cap test reaches for as db_mod - that name is not bound yet here.
        real_state = fake_db.signal_state

        async def exhausted(_tg_id, limit):
            return limit, 0

        fake_db.signal_state = exhausted
        try:
            cb = FakeCB(tg_id, "new_signal", message_id=951)
            await bot_mod.new_signal(cb, fake_bot)
        finally:
            fake_db.signal_state = real_state
        check("capped new_signal: shows the limit alert",
              cb.answers and cb.answers[0][0] == config.MSG_DAILY_LIMIT
              and cb.answers[0][1] is True, str(cb.answers))
        check("capped new_signal: does not open the pair picker",
              not fake_bot.calls,
              str([(c["kind"], c["asset"]) for c in fake_bot.calls]))

        # --- BUY and SELL each get their own artwork -------------------------
        print("\n[result] direction artwork")
        check("both directions are paired with an asset",
              all(isinstance(d, tuple) and len(d) == 2
                  for d in config.SIGNAL_DIRECTIONS),
              str(config.SIGNAL_DIRECTIONS))
        for label, key in config.SIGNAL_DIRECTIONS:
            check("%s artwork assets/%s.jpg exists" % (label.split()[0], key),
                  os.path.exists(os.path.join("assets", key + ".jpg")),
                  "assets/" + key + ".jpg is missing")
        mapping = {d.split()[0]: k for d, k in config.SIGNAL_DIRECTIONS}
        check("BUY maps to the green buy.jpg", mapping.get("BUY") == "buy",
              repr(mapping))
        check("SELL maps to the red sell.jpg", mapping.get("SELL") == "sell",
              repr(mapping))

        real_random = bot_mod.random
        for idx, (want_label, want_key) in enumerate(config.SIGNAL_DIRECTIONS):
            word = want_label.split()[0]
            tg_id = 9100 + idx
            fake_bot = FakeBot()
            fake_db._users[tg_id] = {"ui_msg_id": 990 + idx, "album_ids": None}
            # Force the draw so both branches are exercised, not just whichever
            # one random happened to pick.
            bot_mod.random = types.SimpleNamespace(choice=lambda seq, i=idx: seq[i])
            try:
                calls = await drive(bot_mod, fake_bot,
                                    FakeCB(tg_id, "m:5", 990 + idx), sleeps)
            finally:
                bot_mod.random = real_random
            assert_layout(word, calls, config, wait_label)
            sends = [c for c in calls if c["kind"] != "delete"]
            result = sends[-1]
            check("%s: result screen is a photo" % word, result["kind"] == "photo",
                  result["kind"])
            check("%s: result photo is assets/%s.jpg" % (word, want_key),
                  (result["asset"] or "").replace("\\", "/").endswith(
                      "assets/" + want_key + ".jpg"),
                  repr(result["asset"]))
            check("%s: caption rides on the image, not a second message" % word,
                  len(sends) == 3 and result["body"] is not None,
                  str([c["kind"] for c in sends]))
            check("%s: caption names the direction" % word,
                  want_label in (result["body"] or ""), repr(result["body"]))
            check("%s: New Signal button still attached" % word,
                  result["markup"] is not None)
            other = "sell" if want_key == "buy" else "buy"
            check("%s: the other direction's artwork never appears" % word,
                  not any(other + ".jpg" in (c["asset"] or "") for c in calls),
                  str([c["asset"] for c in calls if c["asset"]]))

        # --- the daily-limit screen must not show the BUY board --------------
        print("\n[edge] daily limit screen stays text-only")
        tg_id = 9300
        fake_bot = FakeBot()
        fake_db._users[tg_id] = {"ui_msg_id": 995, "album_ids": None}
        db_mod = sys.modules["db"]
        real_consume = db_mod.consume_signal

        async def capped(tg, limit):
            return False, limit, 0

        db_mod.consume_signal = capped
        try:
            calls = await drive(bot_mod, fake_bot, FakeCB(tg_id, "m:5", 995), sleeps)
        finally:
            db_mod.consume_signal = real_consume
        sends = [c for c in calls if c["kind"] != "delete"]
        check("limit screen sends no image at all",
              all(c["kind"] == "text" for c in sends),
              str([(c["kind"], c["asset"]) for c in sends]))
        check("limit screen shows the limit message",
              config.MSG_DAILY_LIMIT in (sends[-1]["body"] or ""),
              repr(sends[-1]["body"]))

        # --- every currency pair takes the same route ------------------------
        print("\n[coverage] every currency pair produces the same text-only stage")
        pairs = sorted(set(config.PAIR_CODES.values()))
        print("  %d pairs" % len(pairs))
        bad_pairs = []
        for i, pair in enumerate(pairs):
            tg_id = 20000 + i
            fake_bot = FakeBot()
            fake_db._users[tg_id] = {"ui_msg_id": 800 + i, "album_ids": None}
            bot_mod._pair_choice[tg_id] = pair
            calls = await drive(bot_mod, fake_bot, FakeCB(tg_id, "m:1", 800 + i),
                                sleeps)
            sends = [c for c in calls if c["kind"] != "delete"]
            # Two text messages, then the delivered signal naming this pair.
            if (len(sends) != 3
                    or [c["kind"] for c in sends[:2]] != ["text", "text"]
                    or any(c["asset"] for c in sends[:2])
                    or pair not in (sends[2]["body"] or "")):
                bad_pairs.append(pair)
        check("all %d currency pairs use the text-only analysis stage" % len(pairs),
              not bad_pairs, str(bad_pairs[:5]))

        # --- the tapped screen is torn down before the wait screen -----------
        print("\n[edge] tapped screen is removed before the wait screen goes up")
        tg_id = 8002
        fake_bot = FakeBot()
        fake_db._users[tg_id] = {"ui_msg_id": 980, "album_ids": None}
        calls = await drive(bot_mod, fake_bot, FakeCB(tg_id, "m:1", 980), sleeps)
        first = calls[0]
        check("tapped screen deleted first",
              first["kind"] == "delete" and first["id"] == 980, str(first))
        check("tapped screen deleted exactly once",
              len([c for c in calls if c["kind"] == "delete" and c["id"] == 980]) == 1)

        # --- levels, limits and the daily rollover ---------------------------
        await level_tests(bot_mod, fake_db, config, sleeps)

        # --- the admin-only development reset --------------------------------
        await devstart_tests(bot_mod, fake_db, config)
    finally:
        asyncio.sleep = real_sleep

    print("\n%d checks, %d failed" % (CHECKS[0], len(FAILURES)))
    if FAILURES:
        for f in FAILURES:
            print("  FAILED: " + f)
        return 1
    print("PASS - every signal path uses the two-message, text-only analysis stage.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
