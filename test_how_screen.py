"""Verification test for the "Why traders choose Go+" screen.

This is the screen reached from Welcome's Start button: a headline, five
feature lines, and a single "How Does It Work" button.

Its caption emoji and its button emoji use two different mechanisms:

  * caption emoji are <tg-emoji> entities produced by pe(), and resolve only
    when the message is sent with parse_mode="HTML"
  * the button emoji is InlineKeyboardButton.icon_custom_emoji_id, the 4th
    element of a button tuple, which Telegram draws BEFORE the label

A button label is plain text with no entities, so the unicode question mark
must NOT appear in it - the custom emoji supplies the glyph, and a second one
in the label would render twice. That is asserted against the payload
bot.build_kb actually produces, not against the config tuple.

The spacing in this caption is load-bearing and deliberately inconsistent: the
headline, the assets line and the closing line have a space after their emoji;
the four middle feature lines do not. Each line is pinned exactly, because
"tidying" that up is the most likely accidental edit.

Three of the seven ids are shared with other constants (the chart with
E_CHART, the hourglass with E_SIG_GLASS, the pointing finger with E_BACK and
the gate/welcome screens). This file checks those originals still hold their
values, so the sharing stays deliberate.

Run from the repo root:  python test_how_screen.py

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


# (name, id, glyph, the text that must follow the entity on its line)
CAPTION_EMOJI = [
    ("sparkles",  "5325547803936572038", "\U00002728", " WHY TRADERS CHOOSE GO+"),
    ("chart",     "5231200819986047254", "\U0001F4CA", " 100+ trading assets"),
    ("globe",     "5447410659077661506", "\U0001F310", "OTC and exchange pairs"),
    ("target",    "5461009483314517035", "\U0001F3AF", "2 trading modes"),
    ("bolt",      "5992366958681527437", "\U000026A1", "Instant chart analysis"),
    ("hourglass", "5386367538735104399", "\U0000231B", "Available 24/7, any device"),
    ("point",     "5305522282695768654", "\U0001F447", " See how it works"),
]

BUTTON_ICON = "5436113877181941026"

EXPECT_PLAIN = ("\U00002728 WHY TRADERS CHOOSE GO+\n\n"
                "\U0001F4CA 100+ trading assets\n"
                "\U0001F310OTC and exchange pairs\n"
                "\U0001F3AF2 trading modes\n"
                "\U000026A1Instant chart analysis\n"
                "\U0000231BAvailable 24/7, any device\n\n"
                "\U0001F447 See how it works")


def strip_entities(html):
    return re.sub(r"<tg-emoji [^>]*>(.*?)</tg-emoji>", r"\1", html)


def main():
    screen = config.SCREENS["how"]
    text = screen["text"]
    plain = strip_entities(text)

    # --- the caption --------------------------------------------------------
    print("[how] caption content")
    check("rendered caption matches the specified copy exactly",
          plain == EXPECT_PLAIN, repr(plain))
    check("headline reads WHY TRADERS CHOOSE GO+",
          "WHY TRADERS CHOOSE GO+" in plain, repr(plain))
    check("no bold markup was introduced", "<b>" not in text, repr(text))
    check("no em dash in this caption", "\U00002014" not in plain, repr(plain))
    check("no unfilled placeholder",
          "{" not in text and "}" not in text, repr(text))

    # Line structure, including the two blank lines.
    lines = plain.split("\n")
    check("caption is 9 lines including the two blanks",
          len(lines) == 9, str(len(lines)))
    check("blank line after the headline", lines[1] == "", repr(lines[:3]))
    check("blank line before the closing line", lines[7] == "", repr(lines[6:]))
    check("five feature lines between the blanks",
          len([l for l in lines[2:7] if l]) == 5, repr(lines[2:7]))

    # --- the deliberate spacing ---------------------------------------------
    print("\n[how] spacing after each emoji is exactly as specified")
    for name, emoji_id, glyph, tail in CAPTION_EMOJI:
        check("%s: followed by %r" % (name, tail[:22]),
              (glyph + tail) in plain, repr(plain))
    # Stated as a positive and a negative so neither direction can pass by
    # accident: a spaced emoji must be followed by a space, and an unspaced one
    # must never be. Tidying a stray space in either direction fails here.
    for name, _, glyph, tail in CAPTION_EMOJI:
        if tail.startswith(" "):
            check("%s: HAS a space after the emoji" % name,
                  (glyph + " ") in plain, repr(plain))
        else:
            check("%s: has NO space after the emoji" % name,
                  (glyph + " ") not in plain, repr(plain))

    # --- caption custom emoji ------------------------------------------------
    print("\n[how] caption custom emoji entities")
    for name, emoji_id, glyph, _ in CAPTION_EMOJI:
        entity = config.pe(emoji_id, glyph)
        check("%s: entity with id %s is present" % (name, emoji_id),
              entity in text, entity)
        check("%s: the id is attached to its own glyph" % name,
              ('emoji-id="%s">%s</tg-emoji>' % (emoji_id, glyph)) in text,
              glyph)
    check("exactly seven custom emoji in the caption",
          text.count("<tg-emoji") == 7, str(text.count("<tg-emoji")))
    positions = [text.index(config.pe(i, g)) for _, i, g, _ in CAPTION_EMOJI]
    check("the seven entities appear in the specified order",
          positions == sorted(positions), str(positions))
    check("the sparkles lead the caption",
          text.startswith(config.pe("5325547803936572038", "\U00002728")),
          repr(text[:60]))

    # --- the button, as actually built --------------------------------------
    print("\n[how] button payload as built by bot.build_kb")
    H._install_stub_modules()
    bot_mod = H._load_bot()
    kb = bot_mod.build_kb(screen["kb"])
    flat = [b for row in kb.inline_keyboard for b in row]

    check("exactly one button on this screen", len(flat) == 1, str(len(flat)))
    if flat:
        payload = flat[0].model_dump(exclude_none=True)
        check("label is exactly 'How Does It Work'",
              payload.get("text") == "How Does It Work", repr(payload.get("text")))
        check("icon_custom_emoji_id is %s" % BUTTON_ICON,
              payload.get("icon_custom_emoji_id") == BUTTON_ICON,
              "got " + repr(payload.get("icon_custom_emoji_id", "<ABSENT>")))
        # No duplicate glyph: the icon supplies the question mark.
        check("no unicode question-mark emoji in the label",
              "\U00002753" not in payload.get("text", "")
              and "\U00002754" not in payload.get("text", ""),
              repr(payload.get("text")))
        check("label is pure ASCII, so no glyph doubles up",
              all(ord(ch) < 128 for ch in payload.get("text", "")),
              repr(payload.get("text")))
        check("callback_data is unchanged (go:tech)",
              payload.get("callback_data") == "go:tech",
              repr(payload.get("callback_data")))
        check("style is unchanged (primary)",
              payload.get("style") == "primary", repr(payload.get("style")))
        check("it is a callback button, not a URL",
              flat[0].url is None, repr(getattr(flat[0], "url", None)))
    check("the button icon comes from E_QMARK",
          config.E_QMARK == BUTTON_ICON, config.E_QMARK)
    check("the screen still renders its own artwork",
          screen["photo"] == "how", repr(screen.get("photo")))

    # --- nothing outside this screen moved -----------------------------------
    print("\n[how] no other screen or button changed")
    # The four ids new to this screen must appear nowhere else.
    NEW_IDS = {"5325547803936572038", "5447410659077661506",
               "5461009483314517035", "5992366958681527437"}
    for key, other in config.SCREENS.items():
        if key == "how":
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

    # The shared ids must still hold their original values elsewhere.
    check("E_CHART still holds its id", config.E_CHART == "5231200819986047254")
    check("E_SIG_GLASS still holds its id",
          config.E_SIG_GLASS == "5386367538735104399")
    check("E_BACK still holds its id", config.E_BACK == "5305522282695768654")

    # Screens either side of "how" in the funnel are intact.
    check("the welcome screen still leads here",
          [b[1] for row in config.SCREENS["welcome"]["kb"] for b in row]
          == ["cb:go:how"], str(config.SCREENS["welcome"]["kb"]))
    check("the welcome caption is unchanged",
          "Welcome to Go+" in config.SCREENS["welcome"]["text"])
    check("the tech screen this leads to is unchanged",
          [b[1] for row in config.SCREENS["tech"]["kb"] for b in row]
          == ["cb:go:ai"], str(config.SCREENS["tech"]["kb"]))
    check("the gate still has its two buttons, in order",
          [b[1] for row in config.SCREENS["gate"]["kb"] for b in row]
          == ["url:" + config.CHANNEL_URL, "cb:check_sub"],
          str(config.SCREENS["gate"]["kb"]))
    check("the main menu still has six buttons",
          sum(len(r) for r in config.SCREENS["menu"]["kb"]) == 6,
          str(sum(len(r) for r in config.SCREENS["menu"]["kb"])))
    check("the mode screen buttons are untouched",
          [b[1] for row in config.SCREENS["mode"]["kb"] for b in row]
          == ["cb:mode:manual", "cb:mode:auto", "cb:go:menu"],
          str(config.SCREENS["mode"]["kb"]))

    print("\n%d checks, %d failed" % (CHECKS[0], len(FAILURES)))
    if FAILURES:
        for f in FAILURES:
            print("  FAILED: " + f)
        return 1
    print("PASS - the how screen renders the specified copy, emoji and button.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
