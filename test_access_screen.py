"""Verification test for the activation screen.

This is SCREENS["access"] - the screen reached from the results screen's
"Get access" button, carrying the "Activate Bot" button that starts the
register/UID flow. It is a VIDEO screen, not a photo one.

Eleven distinct custom emoji appear in the caption (twelve entities: the
pointing finger is used twice, opening and closing the last line). All of them
go through pe(); none may be left as a bare unicode literal.

Two things about this caption are easy to break and are pinned exactly:

  * bold - only two spans, the headline figure and "Go Plus". Any third bold
    span, or either of these losing its tags, fails here.
  * spacing - deliberately uneven. The robot butts straight against "Go Plus",
    the two hands sit together before one space, and the index finger and bolt
    take no space at all, while the siren, watch, house and pointing fingers
    do. Each is asserted in both directions.

The button is NOT part of this change. Its full payload is pinned so a future
caption edit cannot quietly disturb it.

Run from the repo root:  python test_access_screen.py

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


# (name, id, glyph, space after it?) in the order they must appear.
# The pointing finger appears twice; only its first use is listed here and the
# closing one is checked separately.
CAPTION_EMOJI = [
    ("siren", "5395695537687123235", "\U0001F6A8", True),
    ("money", "5224257782013769471", "\U0001F4B0", False),   # ends its line
    ("robot", "5188678912883827293", "\U0001F916", False),
    ("check", "5206607081334906820", "✔️", False),           # ends its line
    ("spock", "5364297939478921851", "\U0001F596", False),   # hands sit together
    ("ok",    "5364237234411160303", "\U0001F44C", True),
    ("watch", "5240379491515126100", "⌚️", True),
    ("house", "5416041192905265756", "\U0001F3E0", True),
    ("up",    "5019759554234156094", "☝", False),
    ("bolt",  "5303488362278050480", "⚡️", False),
    ("down",  "5305522282695768654", "\U0001F447", True),
]

EXPECT_ID_ORDER = [i for _, i, _, _ in CAPTION_EMOJI] + ["5305522282695768654"]

EXPECT_PLAIN = ("\U0001F6A8 +$18,400 \U00002014 In One Trade. \U0001F4B0\n"
                "No charts. No courses. No stress.\n\n"
                "\U0001F916Go Plus sends the signal \U00002192 you tap "
                "\U00002192 you profit. ✔️\n\n"
                "\U0001F596\U0001F44C Dream car.\n"
                "⌚️ Dream watch.\n"
                "\U0001F3E0 Dream life.\n\n"
                "☝All one click closer.\n\n"
                "⚡️Stop watching others win.\n\n"
                "\U0001F447 Activate the bot now \U0001F447")

# The button, exactly as it was before this change.
BUTTON = {
    "text": "Activate Bot",
    "icon_custom_emoji_id": "6280525956771745921",
    "style": "success",
    "callback_data": "go:register",
}


def strip_all(html):
    return re.sub(r"<[^>]+>", "", html)


def main():
    screen = config.SCREENS["access"]
    text = screen["text"]
    plain = strip_all(text)

    # --- the caption --------------------------------------------------------
    print("[access] caption content")
    check("rendered caption matches the specified copy exactly",
          plain == EXPECT_PLAIN, repr(plain))
    check("headline figure is present",
          "+$18,400" in plain, repr(plain))
    check("real em dash, not a hyphen",
          "$18,400 \U00002014 In One Trade." in plain, repr(plain))
    check("no hyphen stands in for the em dash",
          "$18,400 - In" not in plain, repr(plain))
    check("both arrows are real U+2192",
          plain.count("\U00002192") == 2, str(plain.count("\U00002192")))
    check("no unfilled placeholder",
          "{" not in text and "}" not in text, repr(text))

    lines = plain.split("\n")
    check("caption is 14 lines including the five blanks",
          len(lines) == 14, str(len(lines)))
    for idx in (2, 4, 8, 10, 12):
        check("line %d is blank" % idx, lines[idx] == "", repr(lines[idx]))
    check("the two headline lines are adjacent, no blank between",
          lines[1] == "No charts. No courses. No stress.", repr(lines[1]))
    check("the three dream lines are adjacent",
          lines[5].endswith("Dream car.") and lines[6].endswith("Dream watch.")
          and lines[7].endswith("Dream life."), repr(lines[5:8]))
    check("closing line is last",
          lines[13].endswith("Activate the bot now \U0001F447"), repr(lines[13]))

    # --- bold ----------------------------------------------------------------
    print("\n[access] exactly two bold spans")
    bolds = re.findall(r"<b>(.*?)</b>", text)
    check("exactly two bold spans", len(bolds) == 2, str(bolds))
    check("first bold is the headline figure",
          bolds[0] == "+$18,400 \U00002014 In One Trade." if bolds else False,
          str(bolds[:1]))
    check("second bold is 'Go Plus'",
          bolds[1] == "Go Plus" if len(bolds) > 1 else False, str(bolds[1:2]))
    check("no unclosed bold tag",
          text.count("<b>") == text.count("</b>") == 2,
          "%d open / %d close" % (text.count("<b>"), text.count("</b>")))
    check("nothing else is bold - the dream lines are plain",
          "<b>" not in text.split("Dream car")[0].rsplit("</b>", 1)[-1],
          repr(text))

    # --- custom emoji --------------------------------------------------------
    print("\n[access] all eleven custom emoji, in order")
    ids = re.findall(r'emoji-id="(\d+)"', text)
    check("twelve entities (the pointing finger is used twice)",
          len(ids) == 12, str(len(ids)))
    check("eleven distinct ids", len(set(ids)) == 11, str(len(set(ids))))
    check("the ids appear in the specified order",
          ids == EXPECT_ID_ORDER, str(ids))
    for name, emoji_id, glyph, _ in CAPTION_EMOJI:
        check("%s: id %s is attached to its own glyph" % (name, emoji_id),
              ('emoji-id="%s">%s</tg-emoji>' % (emoji_id, glyph)) in text,
              "%s / %s" % (emoji_id, ascii(glyph)))
    # Nothing may bypass pe(): every glyph must sit inside an entity.
    for name, _, glyph, _ in CAPTION_EMOJI:
        want = 2 if name == "down" else 1
        check("%s: glyph appears only inside its entity" % name,
              text.count(glyph) == want,
              "%d occurrences, expected %d" % (text.count(glyph), want))

    # --- the deliberate spacing ---------------------------------------------
    print("\n[access] spacing after every emoji")
    for name, _, glyph, spaced in CAPTION_EMOJI:
        if spaced:
            check("%s: HAS a space after it" % name,
                  (glyph + " ") in plain, repr(plain))
        else:
            check("%s: has NO space after it" % name,
                  (glyph + " ") not in plain, repr(plain))
    check("robot butts straight against Go Plus",
          "\U0001F916Go Plus" in plain, repr(plain))
    check("one space between the bold sentence and the money bag",
          "In One Trade. \U0001F4B0" in plain, repr(plain))
    check("the two hands sit together",
          "\U0001F596\U0001F44C Dream car." in plain, repr(plain))
    check("check mark follows a space after 'profit.'",
          "you profit. ✔️" in plain, repr(plain))
    check("closing line has a space each side of the text",
          "\U0001F447 Activate the bot now \U0001F447" in plain, repr(plain))

    # --- the button is untouched ---------------------------------------------
    print("\n[access] the Activate Bot button did not move")
    H._install_stub_modules()
    bot_mod = H._load_bot()
    kb = bot_mod.build_kb(screen["kb"])
    flat = [b for row in kb.inline_keyboard for b in row]

    check("exactly one button on this screen", len(flat) == 1, str(len(flat)))
    if flat:
        payload = flat[0].model_dump(exclude_none=True)
        check("button payload is completely unchanged",
              payload == BUTTON, str(payload))
        for key, want in BUTTON.items():
            check("button %s is %r" % (key, want),
                  payload.get(key) == want, repr(payload.get(key)))
        check("it is a callback button, not a URL",
              flat[0].url is None, repr(getattr(flat[0], "url", None)))

    # --- the media is untouched ----------------------------------------------
    check("this screen is still a VIDEO screen",
          screen.get("video") == "access", repr(screen.get("video")))
    check("no photo key was introduced",
          "photo" not in screen, str(sorted(screen)))

    # --- nothing outside this screen moved -----------------------------------
    print("\n[access] no other screen or configuration changed")
    NEW_IDS = {"5395695537687123235", "5206607081334906820",
               "5364297939478921851", "5364237234411160303",
               "5240379491515126100", "5416041192905265756",
               "5019759554234156094", "5303488362278050480"}
    for key, other in config.SCREENS.items():
        if key == "access":
            continue
        body = other.get("text") or ""
        for nid in sorted(NEW_IDS):
            check("screen %r does not use the new id %s" % (key, nid[:8]),
                  nid not in body, key)
        for row in (other.get("kb") or []):
            for item in row:
                icon = item[3] if len(item) > 3 else None
                check("screen %r button %r kept its own icon"
                      % (key, item[0][:18]), icon not in NEW_IDS, str(icon))

    for name, want in (("E_MONEY", "5224257782013769471"),
                       ("E_MENU_HEADER", "5188678912883827293"),
                       ("E_BACK", "5305522282695768654")):
        check("%s still holds its id" % name, getattr(config, name) == want,
              getattr(config, name))
    # The results screen's index finger is a DIFFERENT sticker id - the two
    # must not have been merged.
    check("the results screen keeps its own index-finger id",
          config.E_RES_UP == "5370740716840425754" and config.E_ACC_UP
          == "5019759554234156094",
          "%s / %s" % (config.E_RES_UP, config.E_ACC_UP))

    # Screens either side of "access" in the funnel are intact.
    check("the results screen still leads here",
          [b[1] for row in config.SCREENS["results"]["kb"] for b in row]
          == ["cb:go:access", "url:" + config.CHANNEL_URL],
          str(config.SCREENS["results"]["kb"]))
    check("the results caption is unchanged",
          "Real feedback from active Go+ traders." in
          config.SCREENS["results"]["text"])
    check("the register screen this leads to is unchanged",
          "register" in config.SCREENS, str(sorted(config.SCREENS)))
    check("the ai caption is unchanged",
          "THE TECHNOLOGY BEHIND GO+" in config.SCREENS["ai"]["text"])
    check("the tech caption is unchanged",
          "IT IS SIMPLE" in config.SCREENS["tech"]["text"])
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
    print("PASS - the access screen renders the specified copy and emoji.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
