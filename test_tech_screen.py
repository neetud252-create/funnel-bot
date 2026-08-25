"""Verification test for the "It is simple" screen.

This is the step list reached from the How Does It Work button on the previous
screen: a headline, five numbered steps, and a closing line, with a single
"See the technology" button underneath.

Nine caption emoji are <tg-emoji> entities produced by pe(). The button is NOT
touched by this screen's copy change and is asserted here purely to prove it
did not move.

The spacing in this caption is load-bearing and deliberately uneven: the
clipboard and all five keycaps butt straight against their text, and on the
signal line the only space is BEFORE the green circle - "BUY 🟢or SELL🔴".
Each of those is pinned both positively and negatively, because normalising
that spacing is the most likely accidental future edit.

Seven of the nine ids are shared with constants used elsewhere (the keycaps
with E_N1..E_N5, which the mode/type/asset screens also use; the green circle
with E_GREEN; the robot with E_MENU_HEADER). This file checks those originals
still hold their values, so the sharing stays deliberate rather than accidental.

Run from the repo root:  python test_tech_screen.py

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


# (name, id, glyph, the text that must immediately follow the entity)
CAPTION_EMOJI = [
    ("clipboard", "5352765106180610755", "\U0001F4CB", "IT IS SIMPLE"),
    ("one",       "5778373820930858379", "1️⃣", "Select an asset"),
    ("two",       "5778382698628256004", "2️⃣", "Choose the expiration time"),
    ("three",     "5778338052443213984", "3️⃣", "Get a signal \U00002014 BUY "),
    ("green",     "5188234920639632382", "\U0001F7E2", "or SELL"),
    ("red",       "5411225014148014586", "\U0001F534", "\n"),
    ("four",      "5778346006722646362", "4️⃣", "Open a trade"),
    ("five",      "5778205144680239810", "5️⃣", "Track the result"),
    ("robot",     "5188678912883827293", "\U0001F916", " I handle the market analysis"),
]

EXPECT_PLAIN = ("\U0001F4CBIT IS SIMPLE\n\n"
                "1️⃣Select an asset\n"
                "2️⃣Choose the expiration time\n"
                "3️⃣Get a signal \U00002014 BUY \U0001F7E2or SELL\U0001F534\n"
                "4️⃣Open a trade\n"
                "5️⃣Track the result\n\n"
                "\U0001F916 I handle the market analysis \U00002014 "
                "you decide when to act.")


def strip_entities(html):
    return re.sub(r"<tg-emoji [^>]*>(.*?)</tg-emoji>", r"\1", html)


def main():
    screen = config.SCREENS["tech"]
    text = screen["text"]
    plain = strip_entities(text)

    # --- the caption --------------------------------------------------------
    print("[tech] caption content")
    check("rendered caption matches the specified copy exactly",
          plain == EXPECT_PLAIN, repr(plain))
    check("headline reads IT IS SIMPLE", "IT IS SIMPLE" in plain, repr(plain))
    check("no bold markup was introduced", "<b>" not in text, repr(text))
    check("no unfilled placeholder",
          "{" not in text and "}" not in text, repr(text))

    lines = plain.split("\n")
    check("caption is 9 lines including the two blanks",
          len(lines) == 9, str(len(lines)))
    check("blank line after the headline", lines[1] == "", repr(lines[:3]))
    check("blank line before the closing robot line",
          lines[7] == "", repr(lines[6:]))
    check("five numbered steps between the blanks",
          len([l for l in lines[2:7] if l]) == 5, repr(lines[2:7]))
    check("the closing line is last",
          lines[8].endswith("you decide when to act."), repr(lines[8]))

    # Two em dashes, both the real character.
    check("em dash on the signal line",
          "Get a signal \U00002014 BUY" in plain, repr(plain))
    check("em dash on the closing line",
          "analysis \U00002014 you decide" in plain, repr(plain))
    check("exactly two em dashes", plain.count("\U00002014") == 2,
          str(plain.count("\U00002014")))
    check("no hyphen stands in for an em dash", " - " not in plain, repr(plain))

    # --- the deliberate spacing ---------------------------------------------
    print("\n[tech] spacing is exactly as specified")
    for name, _, glyph, tail in CAPTION_EMOJI:
        check("%s: followed by %r" % (name, tail[:26]),
              (glyph + tail) in plain, repr(plain))

    # Stated as a negative too, so a "tidying" edit that adds a space fails.
    NO_SPACE_AFTER = ["clipboard", "one", "two", "three", "four", "five", "green"]
    for name, _, glyph, _ in CAPTION_EMOJI:
        if name in NO_SPACE_AFTER:
            check("%s: has NO space after the emoji" % name,
                  (glyph + " ") not in plain, repr(plain))
    check("robot HAS a space after it", "\U0001F916 I handle" in plain, repr(plain))

    # The signal line, character by character - the fiddliest part of the copy.
    print("\n[tech] the BUY/SELL line, exactly")
    check("space BEFORE the green circle",
          "BUY \U0001F7E2" in plain, repr(plain))
    check("NO space after the green circle",
          "\U0001F7E2or SELL" in plain, repr(plain))
    check("NO space before the red circle",
          "SELL\U0001F534" in plain, repr(plain))
    check("the red circle ends the line",
          "\U0001F534\n" in plain, repr(plain))
    check("BUY and SELL are both present, in that order",
          plain.index("BUY") < plain.index("SELL"), repr(plain))

    # --- caption custom emoji ------------------------------------------------
    print("\n[tech] all nine custom emoji entities")
    for name, emoji_id, glyph, _ in CAPTION_EMOJI:
        check("%s: id %s is attached to its own glyph" % (name, emoji_id),
              ('emoji-id="%s">%s</tg-emoji>' % (emoji_id, glyph)) in text,
              "%s / %s" % (emoji_id, ascii(glyph)))
    check("exactly nine custom emoji in the caption",
          text.count("<tg-emoji") == 9, str(text.count("<tg-emoji")))
    positions = [text.index(config.pe(i, g)) for _, i, g, _ in CAPTION_EMOJI]
    check("the nine entities appear in the specified order",
          positions == sorted(positions), str(positions))
    check("all nine ids are distinct",
          len({i for _, i, _, _ in CAPTION_EMOJI}) == 9)
    check("no plain unicode emoji escaped the pe() mechanism",
          plain.count("\U0001F4CB") == 1 and text.count("\U0001F4CB") == 1,
          "clipboard appears outside an entity")

    # --- the button is completely unchanged ----------------------------------
    print("\n[tech] the button did not move")
    H._install_stub_modules()
    bot_mod = H._load_bot()
    kb = bot_mod.build_kb(screen["kb"])
    flat = [b for row in kb.inline_keyboard for b in row]

    check("exactly one button on this screen", len(flat) == 1, str(len(flat)))
    if flat:
        payload = flat[0].model_dump(exclude_none=True)
        check("label is still 'See the technology'",
              payload.get("text") == "See the technology", repr(payload.get("text")))
        check("icon is still E_GEAR (%s)" % config.E_GEAR,
              payload.get("icon_custom_emoji_id") == config.E_GEAR,
              repr(payload.get("icon_custom_emoji_id")))
        check("callback_data is still go:ai",
              payload.get("callback_data") == "go:ai",
              repr(payload.get("callback_data")))
        check("style is still primary",
              payload.get("style") == "primary", repr(payload.get("style")))
        check("it is a callback button, not a URL",
              flat[0].url is None, repr(getattr(flat[0], "url", None)))
    check("the screen still renders its own artwork",
          screen["photo"] == "tech", repr(screen.get("photo")))

    # --- nothing outside this screen moved -----------------------------------
    print("\n[tech] no other screen or configuration changed")
    NEW_IDS = {"5352765106180610755", "5411225014148014586"}
    for key, other in config.SCREENS.items():
        if key == "tech":
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
    for name, want in (("E_N1", "5778373820930858379"),
                       ("E_N2", "5778382698628256004"),
                       ("E_N3", "5778338052443213984"),
                       ("E_N4", "5778346006722646362"),
                       ("E_N5", "5778205144680239810"),
                       ("E_GREEN", "5188234920639632382"),
                       ("E_MENU_HEADER", "5188678912883827293")):
        check("%s still holds its id" % name, getattr(config, name) == want,
              getattr(config, name))

    # Screens either side of "tech" in the funnel are intact.
    check("the how screen still leads here",
          [b[1] for row in config.SCREENS["how"]["kb"] for b in row]
          == ["cb:go:tech"], str(config.SCREENS["how"]["kb"]))
    check("the how caption is unchanged",
          "WHY TRADERS CHOOSE GO+" in config.SCREENS["how"]["text"])
    check("the ai screen this leads to is unchanged",
          [b[1] for row in config.SCREENS["ai"]["kb"] for b in row]
          == ["cb:results"], str(config.SCREENS["ai"]["kb"]))
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
    print("PASS - the tech screen renders the specified copy and emoji.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
