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

The SAME two ids are also the buttons' icons, and that is a genuinely different
mechanism which this file asserts separately:

    A) <tg-emoji emoji-id="..."> inside the caption text, rendered by pe()
    B) InlineKeyboardButton.icon_custom_emoji_id, the emoji Telegram draws
       before a button's label

An InlineKeyboardButton label is plain text with no entities and no parse_mode,
so a custom emoji CANNOT be put inside the label - (B) is the only way to get
one onto a button. The two were out of sync once: the caption carried the right
ids while the buttons still showed plain unicode, and every caption-only check
passed while the live buttons were wrong. So (B) is asserted against the real
payload built by bot.build_kb, not against the config tuple it came from.

Because icon_custom_emoji_id draws its emoji BEFORE the label, a leading unicode
emoji in the label would render a second glyph next to it. The labels are
therefore bare words, and that is asserted too.

Run from the repo root:  python test_mode_emoji.py

No network and no database: db and panelbot are stubbed through the helpers in
test_signal_flow, which are also what let bot.py be imported at all (it ends in
asyncio.run(main())).
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

import config
import test_signal_flow as _harness


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

    # --- (B) the button icons, asserted on the real payload -----------------
    # Built through bot.build_kb rather than read off the config tuple: the
    # tuple is only the input, and it is build_kb that decides whether the 4th
    # element becomes icon_custom_emoji_id at all (it drops a falsy icon, and
    # it strips the "cb:" prefix off callback_data). Asserting the tuple would
    # re-test the fixture; asserting the model tests what Telegram receives.
    print("\n[mode] button payload as built by bot.build_kb")
    _harness._install_stub_modules()
    bot_mod = _harness._load_bot()
    kb = bot_mod.build_kb(config.SCREENS["mode"]["kb"])
    buttons = {}
    for row in kb.inline_keyboard:
        for button in row:
            buttons[button.callback_data] = button

    want_buttons = {
        "mode:manual": ("Manual", "success", "5258011929993026890"),
        "mode:auto":   ("Automatic", "primary", "4943239162758169437"),
    }
    for callback, (want_text, want_style, want_icon) in want_buttons.items():
        button = buttons.get(callback)
        check("%s: button exists" % callback,
              button is not None, "callbacks: " + str(sorted(buttons)))
        if button is None:
            continue
        payload = button.model_dump(exclude_none=True)

        check("%s: text is %r" % (callback, want_text),
              payload.get("text") == want_text, ascii(payload.get("text")))
        check("%s: style is %r" % (callback, want_style),
              payload.get("style") == want_style, ascii(payload.get("style")))
        check("%s: callback_data is %r" % (callback, callback),
              payload.get("callback_data") == callback,
              ascii(payload.get("callback_data")))
        # The check this file exists for. It was <ABSENT> on the live bot while
        # every caption assertion passed.
        check("%s: icon_custom_emoji_id is %s" % (callback, want_icon),
              payload.get("icon_custom_emoji_id") == want_icon,
              "got " + ascii(payload.get("icon_custom_emoji_id", "<ABSENT>")))

        # icon_custom_emoji_id draws BEFORE the label, so a leading unicode
        # emoji would show a second glyph beside the custom one. U+270B and
        # U+1F513 are the two that used to be there.
        label = payload.get("text") or ""
        check("%s: label has no leading unicode emoji" % callback,
              all(ord(ch) < 128 for ch in label), ascii(label))
        check("%s: label free of the old U+270B / U+1F513" % callback,
              "\U0000270B" not in label and "\U0001F513" not in label,
              ascii(label))

    # Same-id-on-both-buttons is a distinct failure from same-id-in-both-lines.
    manual, auto = buttons.get("mode:manual"), buttons.get("mode:auto")
    if manual is not None and auto is not None:
        check("buttons carry different icon ids",
              manual.icon_custom_emoji_id != auto.icon_custom_emoji_id,
              ascii(manual.icon_custom_emoji_id))

    # The Back button is deliberately untouched: no style, no icon.
    back = buttons.get("go:menu")
    check("Back button still present", back is not None)
    if back is not None:
        back_payload = back.model_dump(exclude_none=True)
        check("Back button carries no icon",
              "icon_custom_emoji_id" not in back_payload, str(back_payload))
        check("Back button keeps its unicode label",
              back_payload.get("text", "").startswith("\U000000AB"),
              ascii(back_payload.get("text")))

    check("mode screen still has exactly 3 rows of 1 button",
          [len(r) for r in kb.inline_keyboard] == [1, 1, 1],
          str([len(r) for r in kb.inline_keyboard]))
    check("button order is Manual, Automatic, Back",
          [b.callback_data for row in kb.inline_keyboard for b in row]
          == ["mode:manual", "mode:auto", "go:menu"],
          str([b.callback_data for row in kb.inline_keyboard for b in row]))

    print("\n%d checks, %d failed" % (CHECKS[0], len(FAILURES)))
    if FAILURES:
        for f in FAILURES:
            print("  FAILED: " + f)
        return 1
    print("PASS - the mode screen pins the expected Manual and Automatic emoji.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
