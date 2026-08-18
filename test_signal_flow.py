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


# --- stub out everything bot.py imports but this test does not exercise ------

def _install_stub_modules():
    db = types.ModuleType("db")
    db._users = {}

    def _u(tg_id):
        return db._users.setdefault(tg_id, {"ui_msg_id": None, "album_ids": None})

    async def get_user(tg_id):
        return dict(_u(tg_id))

    async def set_ui_msg(tg_id, msg_id):
        _u(tg_id)["ui_msg_id"] = msg_id

    async def set_album(tg_id, ids):
        _u(tg_id)["album_ids"] = ids

    async def signal_state(tg_id, limit):
        return 0, limit

    async def consume_signal(tg_id, limit):
        return True, 1, limit - 1

    for fn in (get_user, set_ui_msg, set_album, signal_state, consume_signal):
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
    def __init__(self, tg_id):
        self.id = tg_id


class FakeCB:
    """Minimal CallbackQuery: what the two signal handlers actually touch."""

    def __init__(self, tg_id, data, message_id):
        self.from_user = FakeUser(tg_id)
        self.data = data
        self.message = FakeMsg(message_id)
        self.answers = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))


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
