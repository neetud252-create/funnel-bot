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

        # --- path 2: the "New Signal" button on the result screen ------------
        print("\n[path] new_signal - repeat from the result screen")
        tg_id = 6001
        fake_bot = FakeBot()
        bot_mod._expiry_choice[tg_id] = "M7"
        fake_db._users[tg_id] = {"ui_msg_id": 950, "album_ids": None}
        cb = FakeCB(tg_id, "new_signal", message_id=950)
        calls = await drive(bot_mod, fake_bot, cb, sleeps)
        assert_layout("new_signal", calls, config, wait_label)
        check("new_signal: waited the full countdown",
              len(sleeps) == 1 and 29 < sleeps[0] <= config.SIGNAL_COUNTDOWN,
              str(sleeps))
        check("new_signal: reuses the last expiration",
              any("M7" in (c["body"] or "") for c in calls))

        # --- both paths agree ------------------------------------------------
        print("\n[cross-check] both paths produce the same layout")
        tg_a, tg_b = 7001, 7002
        bot_a, bot_b = FakeBot(), FakeBot()
        fake_db._users[tg_a] = {"ui_msg_id": 960, "album_ids": None}
        fake_db._users[tg_b] = {"ui_msg_id": 961, "album_ids": None}
        bot_mod._expiry_choice[tg_b] = "M3"
        a = await drive(bot_mod, bot_a, FakeCB(tg_a, "m:3", 960), sleeps)
        b = await drive(bot_mod, bot_b, FakeCB(tg_b, "new_signal", 961), sleeps)
        shape_a = [c["kind"] for c in a]
        shape_b = [c["kind"] for c in b]
        check("m_action and new_signal emit an identical call sequence",
              shape_a == shape_b, str(shape_a) + " vs " + str(shape_b))

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
