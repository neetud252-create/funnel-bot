"""Verification test for the "The technology behind Go+" screen.

This is the screen reached from the tech screen's "See the technology" button:
a headline, four capability lines, a closing line, and a single "See real
results" button.

Two mechanisms are in play and they are not interchangeable:

  * caption emoji are <tg-emoji> entities produced by pe(), and resolve only
    when the message is sent with parse_mode="HTML"
  * the button emoji is InlineKeyboardButton.icon_custom_emoji_id, the 4th
    element of a button tuple, which Telegram draws BEFORE the label

This button carried NO icon before this change - it was a 3-tuple - so the icon
being present at all is part of what is asserted here. Its label was already
correct and must stay plain: the unicode chart must not appear in it, or the
glyph would render twice.

Spacing is deliberate and uneven: every caption emoji takes one space after it
EXCEPT the bar chart, which butts straight against "Hundreds". Both directions
are pinned, because normalising that is the most likely accidental edit.

Four of the seven ids are shared with constants used elsewhere (the gear with
E_GEAR, the chart with E_CHART, the pointing finger with E_BACK, and the button
icon with E_MENU_LEVEL). This file checks those originals still hold their
values, so the sharing stays deliberate.

Run from the repo root:  python test_ai_screen.py

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


# (name, id, glyph, text that must immediately follow, space after the emoji?)
CAPTION_EMOJI = [
    ("gear",  "5341715473882955310", "⚙️", " THE TECHNOLOGY BEHIND GO+", True),
    ("cat",   "5796185041717433060", "\U0001F63A",
     " AI that processes market data in seconds", True),
    ("chart", "5231200819986047254", "\U0001F4CA",
     "Hundreds of indicators and price patterns", False),
    ("lens",  "5231012545799666522", "\U0001F50D",
     " Spots setups that are easy to miss", True),
    ("bolt",  "5274182275704039686", "\U000026A1",
     " Every signal comes from calculations, not guesswork", True),
    ("point", "5305522282695768654", "\U0001F447", " See it in action", True),
]

BUTTON_ICON = "5244837092042750681"

EXPECT_PLAIN = ("⚙️ THE TECHNOLOGY BEHIND GO+\n\n"
                "\U0001F63A AI that processes market data in seconds\n"
                "\U0001F4CAHundreds of indicators and price patterns\n"
                "\U0001F50D Spots setups that are easy to miss\n"
                "\U000026A1 Every signal comes from calculations, not guesswork\n\n"
                "\U0001F447 See it in action")


def strip_entities(html):
    return re.sub(r"<tg-emoji [^>]*>(.*?)</tg-emoji>", r"\1", html)


def main():
    screen = config.SCREENS["ai"]
    text = screen["text"]
    plain = strip_entities(text)

    # --- the caption --------------------------------------------------------
    print("[ai] caption content")
    check("rendered caption matches the specified copy exactly",
          plain == EXPECT_PLAIN, repr(plain))
    check("headline reads THE TECHNOLOGY BEHIND GO+",
          "THE TECHNOLOGY BEHIND GO+" in plain, repr(plain))
    check("no bold markup was introduced", "<b>" not in text, repr(text))
    check("no unfilled placeholder",
          "{" not in text and "}" not in text, repr(text))
    check("the comma in 'calculations, not guesswork' is kept",
          "calculations, not guesswork" in plain, repr(plain))
    check("no em dash was introduced", "\U00002014" not in plain, repr(plain))

    lines = plain.split("\n")
    check("caption is 8 lines including the two blanks",
          len(lines) == 8, str(len(lines)))
    check("blank line after the headline", lines[1] == "", repr(lines[:3]))
    check("blank line before the closing line", lines[6] == "", repr(lines[5:]))
    check("four capability lines between the blanks",
          len([l for l in lines[2:6] if l]) == 4, repr(lines[2:6]))
    check("the closing line is last",
          lines[7].endswith("See it in action"), repr(lines[7]))

    # --- the deliberate spacing ---------------------------------------------
    print("\n[ai] spacing after every emoji")
    for name, _, glyph, tail, spaced in CAPTION_EMOJI:
        check("%s: followed by %r" % (name, tail[:28]),
              (glyph + tail) in plain, repr(plain))
        if spaced:
            check("%s: HAS one space after the emoji" % name,
                  (glyph + " ") in plain, repr(plain))
        else:
            check("%s: has NO space after the emoji" % name,
                  (glyph + " ") not in plain, repr(plain))

    # --- caption custom emoji ------------------------------------------------
    print("\n[ai] all six custom emoji entities")
    for name, emoji_id, glyph, _, _ in CAPTION_EMOJI:
        check("%s: id %s is attached to its own glyph" % (name, emoji_id),
              ('emoji-id="%s">%s</tg-emoji>' % (emoji_id, glyph)) in text,
              "%s / %s" % (emoji_id, ascii(glyph)))
    check("exactly six custom emoji in the caption",
          text.count("<tg-emoji") == 6, str(text.count("<tg-emoji")))
    positions = [text.index(config.pe(i, g)) for _, i, g, _, _ in CAPTION_EMOJI]
    check("the six entities appear in the specified order",
          positions == sorted(positions), str(positions))
    check("all six ids are distinct",
          len({i for _, i, _, _, _ in CAPTION_EMOJI}) == 6)
    # Nothing may bypass pe(): every glyph in the caption must sit inside an
    # entity, so a plain unicode copy left behind would fail here.
    for name, emoji_id, glyph, _, _ in CAPTION_EMOJI:
        check("%s: glyph appears only inside its entity" % name,
              text.count(glyph) == 1, str(text.count(glyph)))

    # --- the button ----------------------------------------------------------
    print("\n[ai] button payload as built by bot.build_kb")
    H._install_stub_modules()
    bot_mod = H._load_bot()
    kb = bot_mod.build_kb(screen["kb"])
    flat = [b for row in kb.inline_keyboard for b in row]

    check("exactly one button on this screen", len(flat) == 1, str(len(flat)))
    if flat:
        payload = flat[0].model_dump(exclude_none=True)
        check("label is exactly 'See real results'",
              payload.get("text") == "See real results",
              repr(payload.get("text")))
        # This button had NO icon before, so its presence is the change.
        check("icon_custom_emoji_id is %s" % BUTTON_ICON,
              payload.get("icon_custom_emoji_id") == BUTTON_ICON,
              "got " + repr(payload.get("icon_custom_emoji_id", "<ABSENT>")))
        check("no unicode chart emoji in the label",
              "\U0001F4C8" not in payload.get("text", "")
              and "\U0001F4CA" not in payload.get("text", ""),
              repr(payload.get("text")))
        check("label is pure ASCII, so no glyph doubles up",
              all(ord(ch) < 128 for ch in payload.get("text", "")),
              repr(payload.get("text")))
        check("callback_data is unchanged (results)",
              payload.get("callback_data") == "results",
              repr(payload.get("callback_data")))
        check("style is unchanged (primary)",
              payload.get("style") == "primary", repr(payload.get("style")))
        check("it is a callback button, not a URL",
              flat[0].url is None, repr(getattr(flat[0], "url", None)))
    check("the screen still renders its own artwork",
          screen["photo"] == "ai", repr(screen.get("photo")))

    # --- nothing outside this screen moved -----------------------------------
    print("\n[ai] no other screen or configuration changed")
    NEW_IDS = {"5796185041717433060", "5231012545799666522",
               "5274182275704039686"}
    for key, other in config.SCREENS.items():
        if key == "ai":
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

    for name, want in (("E_GEAR", "5341715473882955310"),
                       ("E_CHART", "5231200819986047254"),
                       ("E_BACK", "5305522282695768654"),
                       ("E_MENU_LEVEL", "5244837092042750681")):
        check("%s still holds its id" % name, getattr(config, name) == want,
              getattr(config, name))
    check("the menu's My level button still carries E_MENU_LEVEL",
          any(len(b) > 3 and b[3] == config.E_MENU_LEVEL
              for row in config.SCREENS["menu"]["kb"] for b in row),
          str(config.SCREENS["menu"]["kb"]))

    # Screens either side of "ai" in the funnel are intact.
    check("the tech screen still leads here",
          [b[1] for row in config.SCREENS["tech"]["kb"] for b in row]
          == ["cb:go:ai"], str(config.SCREENS["tech"]["kb"]))
    check("the tech caption is unchanged",
          "IT IS SIMPLE" in config.SCREENS["tech"]["text"])
    check("the tech button icon is unchanged",
          config.SCREENS["tech"]["kb"][0][0][3] == config.E_GEAR)
    check("the results screen this leads to is unchanged",
          [b[1] for row in config.SCREENS["results"]["kb"] for b in row]
          == ["cb:go:access", "url:" + config.CHANNEL_URL],
          str(config.SCREENS["results"]["kb"]))
    check("the how caption is unchanged",
          "WHY TRADERS CHOOSE GO+" in config.SCREENS["how"]["text"])
    check("the welcome caption is unchanged",
          "Welcome to Go+" in config.SCREENS["welcome"]["text"])
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
    print("PASS - the ai screen renders the specified copy, emoji and button.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
