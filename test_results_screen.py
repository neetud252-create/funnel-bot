"""Verification test for the Results / real-feedback screen.

This is the screen reached from the AI screen's "See real results" button: a
bold title, two lines of copy, the channel handle, and two buttons.

Two mechanisms are in play and they are not interchangeable:

  * caption emoji are <tg-emoji> entities produced by pe(), and resolve only
    when the message is sent with parse_mode="HTML"
  * the first button's emoji is InlineKeyboardButton.icon_custom_emoji_id, the
    4th element of a button tuple, which Telegram draws BEFORE the label

The label must therefore stay free of the unicode padlock, or the glyph would
render twice. That is asserted against the payload bot.build_kb actually
produces.

Before this change two of the caption emoji were plain unicode literals rather
than entities; all four go through pe() now, and this file checks that none of
them slipped back to a bare glyph.

The SECOND button is not part of this change. Its full payload is pinned here
byte-for-byte so a future edit to the first button cannot quietly disturb it.

Three of the five ids are shared with constants used elsewhere (the pointing
finger with E_POINT, the speaker with E_GATE_SOUND, the padlock with
E_GATE_LOCK). This file checks those originals still hold their values.

Run from the repo root:  python test_results_screen.py

No network and no database: db and panelbot are stubbed through the helpers in
test_signal_flow, which are also what let bot.py be imported at all.
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

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


# (name, id, glyph) in the order they must appear
CAPTION_EMOJI = [
    ("up",      "5370740716840425754", "☝️"),
    ("shake",   "5451876269719308814", "\U0001F91D"),
    ("point",   "5415758949129404605", "\U0001F449"),
    ("speaker", "5247187233722607160", "\U0001F50A"),
]

FIRST_ICON = "5296369303661067030"

# The second button, exactly as it was before this change.
SECOND_BUTTON = {
    "text": "Open Telegram channel",
    "icon_custom_emoji_id": "5220069871072583573",
    "style": "primary",
    "url": config.CHANNEL_URL,
}

EXPECT_PLAIN = ("Real feedback from active Go+ traders.\n\n"
                "☝️ The screenshots above are just a tiny fraction of the results.\n\n"
                "\U0001F91D More feedback is published on our trading channel:\n\n"
                "\U0001F449 \U0001F50A" + config.CHANNEL_MENTION)


def strip_all(html):
    return re.sub(r"<[^>]+>", "", html)


def main():
    screen = config.SCREENS["results"]
    text = screen["text"]
    plain = strip_all(text)

    # --- the caption --------------------------------------------------------
    print("[results] caption content")
    check("rendered caption matches the specified copy exactly",
          plain == EXPECT_PLAIN, repr(plain))
    check("title is bold",
          text.startswith("<b>Real feedback from active Go+ traders.</b>"),
          repr(text[:70]))
    check("the bold tag wraps only the title",
          text.count("<b>") == 1 and text.count("</b>") == 1,
          "%d open / %d close" % (text.count("<b>"), text.count("</b>")))
    check("screenshots line is present",
          "The screenshots above are just a tiny fraction of the results."
          in plain, repr(plain))
    check("channel line is present",
          "More feedback is published on our trading channel:" in plain,
          repr(plain))
    check("no unfilled placeholder",
          "{" not in text and "}" not in text, repr(text))

    lines = plain.split("\n")
    check("caption is 7 lines including the three blanks",
          len(lines) == 7, str(len(lines)))
    check("blank line after the title", lines[1] == "", repr(lines[:3]))
    check("blank line after the screenshots line", lines[3] == "", repr(lines[2:5]))
    check("blank line before the handle line", lines[5] == "", repr(lines[4:]))

    # --- the channel handle --------------------------------------------------
    print("\n[results] the channel handle")
    check("the channel handle is present",
          config.CHANNEL_MENTION in plain, repr(plain))
    check("it is the derived CHANNEL_MENTION, not a second hardcoded copy",
          config.CHANNEL_MENTION in text, config.CHANNEL_MENTION)
    check("the handle resolves to a public t.me channel URL",
          config.CHANNEL_URL.startswith("https://t.me/"), config.CHANNEL_URL)
    check("handle and CHANNEL_URL name the same channel",
          config.CHANNEL_URL.rsplit("/", 1)[-1]
          == config.CHANNEL_MENTION.lstrip("@"),
          "%s vs %s" % (config.CHANNEL_URL, config.CHANNEL_MENTION))
    check("the mention is bare, so Telegram auto-links it",
          "<a " not in text, repr(text))
    check("no space between the speaker and the handle",
          "\U0001F50A" + config.CHANNEL_MENTION in plain, repr(plain))
    check("one space between the pointing finger and the speaker",
          "\U0001F449 \U0001F50A" in plain, repr(plain))
    # This screen is a photo caption, which has no link preview either way.
    check("the screen is still a photo caption (no link preview to preserve)",
          screen.get("photo") == "welcome", repr(screen.get("photo")))

    # --- caption custom emoji ------------------------------------------------
    print("\n[results] all four custom emoji entities")
    for name, emoji_id, glyph in CAPTION_EMOJI:
        check("%s: id %s is attached to its own glyph" % (name, emoji_id),
              ('emoji-id="%s">%s</tg-emoji>' % (emoji_id, glyph)) in text,
              "%s / %s" % (emoji_id, ascii(glyph)))
    check("exactly four custom emoji in the caption",
          text.count("<tg-emoji") == 4, str(text.count("<tg-emoji")))
    positions = [text.index(config.pe(i, g)) for _, i, g in CAPTION_EMOJI]
    check("the four entities appear in the specified order",
          positions == sorted(positions), str(positions))
    check("all four ids are distinct",
          len({i for _, i, _ in CAPTION_EMOJI}) == 4)
    # Two of these used to be bare unicode; none may have slipped back.
    for name, _, glyph in CAPTION_EMOJI:
        check("%s: glyph appears only inside its entity" % name,
              text.count(glyph) == 1, str(text.count(glyph)))
    check("the old plain heart literal is gone",
          "\U0001F49F" not in text, repr(text))

    # --- the buttons ---------------------------------------------------------
    print("\n[results] button payloads as built by bot.build_kb")
    H._install_stub_modules()
    bot_mod = H._load_bot()
    kb = bot_mod.build_kb(screen["kb"])
    flat = [b for row in kb.inline_keyboard for b in row]

    check("exactly two buttons on this screen", len(flat) == 2, str(len(flat)))
    check("each button is on its own row",
          [len(r) for r in kb.inline_keyboard] == [1, 1],
          str([len(r) for r in kb.inline_keyboard]))

    if len(flat) == 2:
        first = flat[0].model_dump(exclude_none=True)
        check("first button text is exactly 'Get access to Go +'",
              first.get("text") == "Get access to Go +", repr(first.get("text")))
        check("first button icon_custom_emoji_id is %s" % FIRST_ICON,
              first.get("icon_custom_emoji_id") == FIRST_ICON,
              "got " + repr(first.get("icon_custom_emoji_id", "<ABSENT>")))
        check("first button label carries no unicode padlock",
              "\U0001F512" not in first.get("text", ""), repr(first.get("text")))
        check("first button label is pure ASCII, so no glyph doubles up",
              all(ord(ch) < 128 for ch in first.get("text", "")),
              repr(first.get("text")))
        check("first button callback_data is unchanged (go:access)",
              first.get("callback_data") == "go:access",
              repr(first.get("callback_data")))
        check("first button style is unchanged (success)",
              first.get("style") == "success", repr(first.get("style")))
        check("first button is a callback button, not a URL",
              flat[0].url is None, repr(getattr(flat[0], "url", None)))

        # The second button must be byte-for-byte what it was.
        second = flat[1].model_dump(exclude_none=True)
        check("second button payload is completely unchanged",
              second == SECOND_BUTTON, str(second))
        for key, want in SECOND_BUTTON.items():
            check("second button %s is %r" % (key, want),
                  second.get(key) == want, repr(second.get(key)))
        check("second button has no callback_data",
              second.get("callback_data") is None, repr(second.get("callback_data")))

    check("button order is Get access then Open Telegram channel",
          [b.text for b in flat]
          == ["Get access to Go +", "Open Telegram channel"],
          str([b.text for b in flat]))

    # --- nothing outside this screen moved -----------------------------------
    print("\n[results] no other screen or configuration changed")
    NEW_IDS = {"5451876269719308814"}
    for key, other in config.SCREENS.items():
        if key == "results":
            continue
        body = other.get("text") or ""
        for nid in NEW_IDS:
            check("screen %r does not use the new id %s" % (key, nid[:8]),
                  nid not in body, key)
        for row in (other.get("kb") or []):
            for item in row:
                icon = item[3] if len(item) > 3 else None
                check("screen %r button %r kept its own icon"
                      % (key, item[0][:18]), icon not in NEW_IDS, str(icon))

    for name, want in (("E_POINT", "5415758949129404605"),
                       ("E_GATE_SOUND", "5247187233722607160"),
                       ("E_GATE_LOCK", "5296369303661067030")):
        check("%s still holds its id" % name, getattr(config, name) == want,
              getattr(config, name))
    check("the gate headline still uses its own padlock",
          config.pe(config.E_GATE_LOCK, "\U0001F512")
          in config.SCREENS["gate"]["text"])
    check("the gate's speaker is still in its caption",
          config.pe(config.E_GATE_SOUND, "\U0001F50A")
          in config.SCREENS["gate"]["text"])

    # Screens either side of "results" in the funnel are intact.
    check("the ai screen still leads here",
          [b[1] for row in config.SCREENS["ai"]["kb"] for b in row]
          == ["cb:results"], str(config.SCREENS["ai"]["kb"]))
    check("the ai caption is unchanged",
          "THE TECHNOLOGY BEHIND GO+" in config.SCREENS["ai"]["text"])
    # The access screen carries a menu now; what matters to THIS test is only
    # that it still routes onward into the register flow, once.
    check("the access screen still routes into the register flow",
          [b[1] for row in config.SCREENS["access"]["kb"]
           for b in row].count("cb:go:register") == 1,
          str(config.SCREENS["access"]["kb"]))
    check("the tech caption is unchanged",
          "IT IS SIMPLE" in config.SCREENS["tech"]["text"])
    check("the welcome caption is unchanged",
          "Welcome to Go+" in config.SCREENS["welcome"]["text"])
    check("the gate still has its two buttons, in order",
          [b[1] for row in config.SCREENS["gate"]["kb"] for b in row]
          == ["url:" + config.CHANNEL_URL, "cb:check_sub"],
          str(config.SCREENS["gate"]["kb"]))
    check("the main menu still has six buttons",
          sum(len(r) for r in config.SCREENS["menu"]["kb"]) == 6,
          str(sum(len(r) for r in config.SCREENS["menu"]["kb"])))

    print("\n%d checks, %d failed" % (CHECKS[0], len(FAILURES)))
    if FAILURES:
        for f in FAILURES:
            print("  FAILED: " + f)
        return 1
    print("PASS - the results screen renders the specified copy, emoji and buttons.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
