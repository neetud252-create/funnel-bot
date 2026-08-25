"""Verification test for the trading-mode screen's custom emoji.

The Manual and Automatic entities are premium custom emoji embedded in the mode
screen's caption text through pe(), which renders <tg-emoji emoji-id="...">.
Two things about them are easy to break and expensive when broken, so both are
pinned here:

    Manual    -> 5258011929993026890   wrapping U+1F464 (bust in silhouette)
    Automatic -> 4943239162758169437   wrapping U+1F929 (star-struck)

The wrapped glyph matters as much as the id. Telegram rejects the WHOLE message
with "Bad Request: ENTITY_TEXT_INVALID" if the text inside a <tg-emoji> tag is
not a single valid emoji - an em dash there is what previously blanked the mode
screen, which is why config.py puts the emoji its sticker depicts in that slot
rather than the dash it replaced. A test that checked only the ids would miss a
regression that takes the screen down entirely.

These are message-body entities, NOT button icons: an InlineKeyboardButton label
is plain text with no entities, so the mode buttons carry plain unicode and are
deliberately not asserted on here.

Run from the repo root:  python test_mode_emoji.py

No network, no database and no stubs - config.py is pure data.
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

import config


FAILURES = []
CHECKS = [0]


def check(label, condition, detail=""):
    CHECKS[0] += 1
    if condition:
        print("  ok   " + label)
    else:
        print("  FAIL " + label + ((" -- " + detail) if detail else ""))
        FAILURES.append(label)


# label in the copy -> (expected custom emoji id, expected wrapped glyph)
EXPECTED = [
    ("Manual",    "5258011929993026890", "\U0001F464"),
    ("Automatic", "4943239162758169437", "\U0001F929"),
]

TG_EMOJI = re.compile(r'<tg-emoji emoji-id="(\d+)">(.*?)</tg-emoji>', re.S)


def main():
    print("[mode] trading-mode screen custom emoji")
    text = config.SCREENS["mode"]["text"]

    for name, want_id, want_glyph in EXPECTED:
        # The entity sits immediately after the bolded word it belongs to, so
        # anchoring on that pins the id to the right line - a bare "is this id
        # present" check would pass even if the two were swapped.
        want = '<b>%s</b> <tg-emoji emoji-id="%s">%s</tg-emoji>' % (
            name, want_id, want_glyph)
        check("%s: id %s wrapping U+%04X, on the %s line"
              % (name, want_id, ord(want_glyph), name),
              want in text, "not found in " + ascii(text))

        check("%s: id appears exactly once" % name,
              text.count(want_id) == 1,
              "found %d times" % text.count(want_id))

    # Swapping one id onto both lines would satisfy neither anchored check
    # above, but assert it directly too - it is the failure this screen has
    # actually seen before.
    ids = [want_id for _, want_id, _ in EXPECTED]
    check("Manual and Automatic use different custom emoji ids",
          len(set(ids)) == len(ids), str(ids))

    # --- the ENTITY_TEXT_INVALID guard --------------------------------------
    # Every <tg-emoji> in this screen must wrap exactly one emoji. A dash or a
    # word in that slot takes the whole screen down, not just the one entity.
    #
    # "no ASCII" would be the obvious rule and it is wrong: a keycap such as
    # 1 U+FE0F U+20E3 legitimately begins with an ASCII digit, and T_N1/T_N2 on
    # this screen are keycaps. So allow digits, and reject what actually breaks
    # it - dashes (the em dash is non-ASCII, so a bare non-ASCII test would let
    # the original bug straight through) and letters.
    print("\n[mode] every entity wraps a single valid emoji")
    DASHES = "-‐‑‒–—―−"
    found = TG_EMOJI.findall(text)
    check("caption contains custom emoji entities", bool(found))
    for emoji_id, glyph in found:
        check("id %s wraps a non-empty glyph" % emoji_id,
              bool(glyph), ascii(glyph))
        check("id %s wraps no dash" % emoji_id,
              not any(ch in DASHES for ch in glyph), ascii(glyph))
        check("id %s wraps no letters" % emoji_id,
              not any(ch.isalpha() for ch in glyph), ascii(glyph))
        check("id %s wraps more than bare ASCII" % emoji_id,
              any(ord(ch) > 127 for ch in glyph), ascii(glyph))

    print("\n%d checks, %d failed" % (CHECKS[0], len(FAILURES)))
    if FAILURES:
        for f in FAILURES:
            print("  FAILED: " + f)
        return 1
    print("PASS - the mode screen pins the expected Manual and Automatic emoji.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
