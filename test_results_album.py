"""Verification test for the reviews album behind "See real results".

The production failure this file pins down:

    cb:results -> results handler -> send_media_group(...)
    Bad Request: failed to send message #1 with the error message
    "Wrong file identifier/HTTP URL specified"

Every item handed to send_media_group was a cached file_id STRING, loaded at
boot from the media_cache table. A file_id issued to a PREVIOUS bot token is
never valid for the new one, so Telegram failed the whole group on message #1.

Single-photo screens already survive this: send_media() catches the rejection,
calls _forget(), and re-uploads. The album is the one screen that cannot use
send_media() - it goes out through send_media_group in a single call - so it
had no equivalent, nothing dropped the stale rows, and the handler died after
cb.answer() had already cleared the spinner. The user saw a tap that did
nothing at all.

This file checks the media list the handler actually builds, and that a stale
file_id now heals instead of killing the screen. Media is validated WITHOUT
sending: an item is acceptable to send_media_group only if it is a non-empty
file_id string or an upload whose file is really in the image.

Run from the repo root:  python test_results_album.py

No network and no database: db and panelbot are stubbed through the helpers in
test_signal_flow, which are also what let bot.py be imported at all.
"""

import asyncio
import os
import sys
import types

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import config
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


STALE_ERROR = ('failed to send message #1 with the error message '
               '"Wrong file identifier/HTTP URL specified"')

STALE_PREFIX = "OLD_BOT_FILE_ID_"


class AlbumBot(H.FakeBot):
    """FakeBot whose send_media_group can reject cached file_ids.

    Every attempt's media list is recorded, so the test can inspect exactly
    what would have gone over the wire without sending anything.
    """

    def __init__(self, bot_mod, fail_on_cached=False):
        super().__init__()
        self._bot_mod = bot_mod
        self.fail_on_cached = fail_on_cached
        self.attempts = []

    async def send_media_group(self, chat_id, media):
        self.attempts.append(list(media))
        if self.fail_on_cached and any(isinstance(i.media, str) for i in media):
            raise self._bot_mod.TelegramBadRequest(method=None,
                                                   message=STALE_ERROR)
        out = []
        for item in media:
            mid = self._mid()
            self.calls.append({"kind": "album", "id": mid, "body": None,
                               "markup": None, "parse_mode": None,
                               "asset": str(getattr(item, "media", item))})
            # A real album reply carries photo sizes; remember() reads the last
            # one, which is how fresh file_ids get written back to the cache.
            msg = H.FakeMsg(mid)
            msg.photo = [types.SimpleNamespace(file_id="NEW_%d" % mid)]
            out.append(msg)
        return out


class AlwaysFails(AlbumBot):
    """Rejects every attempt, cached or not - a genuine upload failure."""

    async def send_media_group(self, chat_id, media):
        self.attempts.append(list(media))
        raise self._bot_mod.TelegramBadRequest(method=None, message=STALE_ERROR)


def item_is_valid(item):
    """Would send_media_group accept this item, without actually sending it?

    Telegram takes either a file_id string or an upload. An FSInputFile whose
    file is not in the image is rejected at send time, so the path is checked
    rather than assumed.
    """
    media = item.media
    if isinstance(media, str):
        return bool(media)
    path = getattr(media, "path", None)
    return bool(path) and os.path.exists(path)


def seed_stale_cache(bot_mod):
    """Exactly what load_media_cache() leaves behind after a token change:
    (file_id, content_hash) with the hash matching disk, so cached_id() trusts
    the entry and hands the dead id straight to InputMediaPhoto."""
    for k in config.REVIEWS:
        entry = (STALE_PREFIX + k,
                 bot_mod.content_hash(bot_mod.asset_path(k, "jpg")))
        bot_mod._photo_cache[k] = entry
        bot_mod.db._media_cache[k] = entry


def cold_cache_tests(bot_mod):
    print("[album] media built from a cold cache")
    bot_mod._photo_cache.clear()
    fake = AlbumBot(bot_mod)
    asyncio.run(bot_mod.results(H.FakeCB(55501, "results", 700), fake))

    check("send_media_group was called exactly once",
          len(fake.attempts) == 1, str(len(fake.attempts)))
    items = fake.attempts[0] if fake.attempts else []
    check("the album carries all five reviews", len(items) == 5, str(len(items)))
    check("every item is valid input for send_media_group",
          all(item_is_valid(i) for i in items),
          str([str(getattr(i.media, "path", i.media)) for i in items]))
    check("with no cache every item is an upload, not a file_id",
          all(not isinstance(i.media, str) for i in items),
          str([type(i.media).__name__ for i in items]))
    check("the uploads are reviews1..reviews5, in config order",
          [getattr(i.media, "path", None) for i in items]
          == [bot_mod.asset_path(k, "jpg") for k in config.REVIEWS],
          str([getattr(i.media, "path", None) for i in items]))
    for k in config.REVIEWS:
        check("asset %s is present in the repo" % k,
              os.path.exists(bot_mod.asset_path(k, "jpg")),
              bot_mod.asset_path(k, "jpg"))

    kinds = [c["kind"] for c in fake.calls]
    check("the album is followed by the results message",
          kinds == ["album"] * 5 + ["text"], str(kinds))
    text_call = [c for c in fake.calls if c["kind"] == "text"]
    check("that message is the results caption",
          bool(text_call)
          and text_call[0]["body"] == config.SCREENS["results"]["text"],
          repr(text_call[0]["body"][:40]) if text_call else "<none>")
    check("it is sent as HTML so the caption entities resolve",
          bool(text_call) and text_call[0]["parse_mode"] == "HTML",
          repr(text_call[0]["parse_mode"]) if text_call else "<none>")
    if text_call:
        labels = [b.text for row in text_call[0]["markup"].inline_keyboard
                  for b in row]
        check("it still carries Get access then Open Telegram channel",
              labels == ["Get access to Go +", "Open Telegram channel"],
              str(labels))


def stale_cache_tests(bot_mod):
    print("\n[album] a file_id from the previous bot no longer kills the screen")
    bot_mod._photo_cache.clear()
    seed_stale_cache(bot_mod)

    stale = AlbumBot(bot_mod, fail_on_cached=True)
    raised = None
    try:
        asyncio.run(bot_mod.results(H.FakeCB(55502, "results", 701), stale))
    except Exception as e:                  # noqa: BLE001 - reported as a check
        raised = e

    check("the handler no longer raises out of the callback",
          raised is None, "%s: %s" % (type(raised).__name__, raised))
    check("it retried exactly once after the rejection",
          len(stale.attempts) == 2, str(len(stale.attempts)))
    if len(stale.attempts) == 2:
        first, second = stale.attempts
        check("the first attempt used the cached file_ids",
              all(isinstance(i.media, str) for i in first),
              str([type(i.media).__name__ for i in first]))
        check("the retry carries no file_id at all",
              all(not isinstance(i.media, str) for i in second),
              str([type(i.media).__name__ for i in second]))
        check("the retry uploads all five from disk",
              [getattr(i.media, "path", None) for i in second]
              == [bot_mod.asset_path(k, "jpg") for k in config.REVIEWS],
              str([getattr(i.media, "path", None) for i in second]))
        check("every retry item is valid input for send_media_group",
              all(item_is_valid(i) for i in second),
              str([getattr(i.media, "path", None) for i in second]))

    for k in config.REVIEWS:
        check("stale id for %s left the in-memory cache" % k,
              bot_mod._photo_cache.get(k, ("", ""))[0] != STALE_PREFIX + k,
              str(bot_mod._photo_cache.get(k)))
        check("stale row for %s left media_cache" % k,
              bot_mod.db._media_cache.get(k, ("", ""))[0] != STALE_PREFIX + k,
              str(bot_mod.db._media_cache.get(k)))

    kinds = [c["kind"] for c in stale.calls]
    check("the user still gets five photos then the message",
          kinds == ["album"] * 5 + ["text"], str(kinds))


def guard_tests(bot_mod):
    print("\n[album] a genuine upload failure is reported, not retried forever")
    bot_mod._photo_cache.clear()
    hard = AlwaysFails(bot_mod)
    err = None
    try:
        asyncio.run(bot_mod.results(H.FakeCB(55503, "results", 702), hard))
    except Exception as e:                  # noqa: BLE001 - asserted below
        err = e
    check("a fresh upload that fails propagates instead of looping",
          isinstance(err, bot_mod.TelegramBadRequest),
          "%s: %s" % (type(err).__name__, err))
    check("and it was attempted exactly once",
          len(hard.attempts) == 1, str(len(hard.attempts)))


def unchanged_tests(bot_mod):
    print("\n[album] the callback, its source and its destination are unchanged")
    check("the ai screen still routes here with cb:results",
          [b[1] for row in config.SCREENS["ai"]["kb"] for b in row]
          == ["cb:results"], str(config.SCREENS["ai"]["kb"]))
    check("the handler is still named results",
          callable(getattr(bot_mod, "results", None)))
    check("the album still comes from config.REVIEWS",
          config.REVIEWS == ["reviews1", "reviews2", "reviews3",
                             "reviews4", "reviews5"], str(config.REVIEWS))
    check("send_review_album reuses the existing cache helpers",
          callable(bot_mod.send_review_album)
          and callable(bot_mod._forget)
          and callable(bot_mod.photo_for))
    check("no invented file_id lives in the source",
          STALE_PREFIX not in open(os.path.join(ROOT, "bot.py"),
                                   encoding="utf-8").read())
    # The single-photo path this fix was modelled on must be untouched.
    check("send_media still handles its own stale file_id",
          "cached file_id rejected for" in open(
              os.path.join(ROOT, "bot.py"), encoding="utf-8").read())


def main():
    H._install_stub_modules()
    bot_mod = H._load_bot()

    cold_cache_tests(bot_mod)
    stale_cache_tests(bot_mod)
    guard_tests(bot_mod)
    unchanged_tests(bot_mod)

    print("\n%d checks, %d failed" % (CHECKS[0], len(FAILURES)))
    if FAILURES:
        for f in FAILURES:
            print("  FAILED: " + f)
        return 1
    print("PASS - the reviews album builds valid media and heals a stale file_id.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
