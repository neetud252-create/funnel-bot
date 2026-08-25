"""Verification test for the registration screen.

This is SCREENS["register"] - the screen that asks the user to register through
the referral link and then send their account ID. It carries three URL buttons.

This test verifies COPY AND PRESENTATION ONLY. It does not exercise the
registration or UID-capture flow, and it changes no production behaviour: it
reads config and builds a keyboard, nothing more.

Six caption emoji go through pe(); the three button emoji ride on the 4th tuple
element as icon_custom_emoji_id. Labels stay bare text so Telegram renders
exactly one premium emoji per button.

Two structural details are pinned because they are easy to break:

  * button 3 has NO style, and must keep having none. It passes None for style
    so it can carry an icon in the 4th slot; build_kb skips a falsy style, so
    the payload must come out with no "style" key at all.
  * the URLs, callbacks and order are behaviour, not presentation, and are
    asserted against the values captured before the copy change.

Run from the repo root:  python test_register_screen.py

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


# (name, id, glyph, space after it?) in the order they must appear
CAPTION_EMOJI = [
    ("lock",  "5350619413533958825", "\U0001F510", False),
    ("link",  "5271604874419647061", "\U0001F517", True),
    ("arrow", "5435955998479102657", "➡️", False),
    ("down",  "5447644880824181073", "\U0001F447", False),   # ends its line
    ("warn",  "5406745015365943482", "⚠️", False),
    ("downarrow", "5406745015365943482", "⬇️", False),       # ends its line
]

REG_URL = "https://shorturl.at/2fu2t"
HOWTO_URL = "https://youtu.be/uJHBwXZVnNI?si=bhC7oMFLvoJfiQy"

# Captured from the live config BEFORE the copy change. Only text and
# icon_custom_emoji_id were allowed to move.
BASELINE = [
    {"url": REG_URL, "style": "success"},
    {"url": HOWTO_URL, "style": "primary"},
    {"url": config.SUPPORT_URL, "style": None},   # button 3 had no style
]

EXPECTED = [
    ("Register & Get Access", "5836690092306992715"),
    ("How to Register", "5222444124698853913"),
    ("Support", "5443038326535759644"),
]

EXPECT_PLAIN = ("\U0001F510To access Go+, register for a new Pocket Option "
                "account using my link:\n\n"
                "\U0001F517 " + REG_URL + "\n\n"
                "➡️Once you register, send your new account ID in the text "
                "box below \U0001F447\n\n"
                "⚠️Please note: Your ID must contain numbers only "
                "\U00002014 no extra symbols ⬇️\n\n"
                "Example: 123456789")


def strip_all(html):
    return re.sub(r"<[^>]+>", "", html)


def main():
    screen = config.SCREENS["register"]
    text = screen["text"]
    plain = strip_all(text)

    # --- the caption --------------------------------------------------------
    print("[register] caption content")
    check("rendered caption matches the specified copy exactly",
          plain == EXPECT_PLAIN, repr(plain))
    check("no bold markup was introduced", "<b>" not in text, repr(text))
    check("no unfilled placeholder",
          "{" not in text and "}" not in text, repr(text))
    check("real em dash, not a hyphen",
          "numbers only \U00002014 no extra symbols" in plain, repr(plain))
    check("the example line is exact",
          plain.endswith("Example: 123456789"), repr(plain[-40:]))

    lines = plain.split("\n")
    check("caption is 9 lines including the four blanks",
          len(lines) == 9, str(len(lines)))
    for idx in (1, 3, 5, 7):
        check("line %d is blank" % idx, lines[idx] == "", repr(lines[idx]))

    # --- the URL -------------------------------------------------------------
    print("\n[register] the referral URL")
    check("the URL is exactly %s" % REG_URL, REG_URL in plain, repr(plain))
    check("it is a bare link, so Telegram auto-links it as before",
          "<a " not in text, repr(text))
    check("one space between the link emoji and the URL",
          "\U0001F517 " + REG_URL in plain, repr(plain))
    check("the caption URL matches the Register button's URL",
          REG_URL in str(screen["kb"][0][0][1]), str(screen["kb"][0][0]))
    # Photo caption: there is no link preview to preserve either way.
    check("the screen is still a photo screen",
          screen.get("photo") == "register", repr(screen.get("photo")))
    check("no video key was introduced", "video" not in screen, str(sorted(screen)))

    # --- caption custom emoji ------------------------------------------------
    print("\n[register] all six caption entities")
    ids = re.findall(r'emoji-id="(\d+)"', text)
    check("exactly six entities", len(ids) == 6, str(len(ids)))
    check("the ids appear in the specified order",
          ids == [i for _, i, _, _ in CAPTION_EMOJI], str(ids))
    for name, emoji_id, glyph, _ in CAPTION_EMOJI:
        check("%s: id %s is attached to its own glyph" % (name, emoji_id),
              ('emoji-id="%s">%s</tg-emoji>' % (emoji_id, glyph)) in text,
              "%s / %s" % (emoji_id, ascii(glyph)))
    # Nothing may bypass pe(): every glyph must sit inside an entity.
    for name, _, glyph, _ in CAPTION_EMOJI:
        check("%s: glyph appears only inside its entity" % name,
              text.count(glyph) == 1, str(text.count(glyph)))
    # As supplied, the warning and the down arrow share one id. Recorded here
    # so the duplication stays a decision rather than becoming a silent bug.
    check("warning and down-arrow share an id, as supplied",
          CAPTION_EMOJI[4][1] == CAPTION_EMOJI[5][1], "ids diverged")

    # --- the deliberate spacing ---------------------------------------------
    print("\n[register] spacing after every emoji")
    for name, _, glyph, spaced in CAPTION_EMOJI:
        if spaced:
            check("%s: HAS a space after it" % name,
                  (glyph + " ") in plain, repr(plain))
        else:
            check("%s: has NO space after it" % name,
                  (glyph + " ") not in plain, repr(plain))
    check("one space before the finger closing the register line",
          "text box below \U0001F447" in plain, repr(plain))
    check("one space before the arrow closing the note line",
          "no extra symbols ⬇️" in plain, repr(plain))

    # --- the buttons ---------------------------------------------------------
    print("\n[register] button payloads as built by bot.build_kb")
    H._install_stub_modules()
    bot_mod = H._load_bot()
    kb = bot_mod.build_kb(screen["kb"])
    flat = [b for row in kb.inline_keyboard for b in row]

    check("exactly three buttons", len(flat) == 3, str(len(flat)))
    check("each button is on its own row",
          [len(r) for r in kb.inline_keyboard] == [1, 1, 1],
          str([len(r) for r in kb.inline_keyboard]))
    check("no button was dropped for a bad URL",
          len(flat) == sum(len(r) for r in screen["kb"]))

    for i, ((want_text, want_icon), base) in enumerate(zip(EXPECTED, BASELINE)):
        if i >= len(flat):
            continue
        payload = flat[i].model_dump(exclude_none=True)
        n = i + 1
        check("button %d: text is exactly %r" % (n, want_text),
              payload.get("text") == want_text, repr(payload.get("text")))
        check("button %d: icon_custom_emoji_id is %s" % (n, want_icon),
              payload.get("icon_custom_emoji_id") == want_icon,
              "got " + repr(payload.get("icon_custom_emoji_id", "<ABSENT>")))
        check("button %d: label is bare text, no unicode emoji" % n,
              all(ord(ch) < 128 for ch in payload.get("text", "")),
              repr(payload.get("text")))
        # Behaviour, captured before the change.
        check("button %d: URL unchanged" % n,
              payload.get("url") == base["url"], repr(payload.get("url")))
        if base["style"] is None:
            check("button %d: still has NO style" % n,
                  "style" not in payload, str(payload))
        else:
            check("button %d: style unchanged (%s)" % (n, base["style"]),
                  payload.get("style") == base["style"], repr(payload.get("style")))
        check("button %d: is a URL button, no callback_data" % n,
              payload.get("callback_data") is None,
              repr(payload.get("callback_data")))

    check("button order is Register, How to Register, Support",
          [b.text for b in flat]
          == ["Register & Get Access", "How to Register", "Support"],
          str([b.text for b in flat]))
    check("the three button icons are all different",
          len({b.icon_custom_emoji_id for b in flat}) == 3,
          str([b.icon_custom_emoji_id for b in flat]))
    check("the Support button still points at SUPPORT_URL",
          flat[2].url == config.SUPPORT_URL, repr(flat[2].url))

    # --- nothing outside this screen moved -----------------------------------
    print("\n[register] no other screen or configuration changed")
    NEW_IDS = {"5350619413533958825", "5271604874419647061",
               "5435955998479102657", "5836690092306992715",
               "5222444124698853913"}
    for key, other in config.SCREENS.items():
        if key == "register":
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

    for name, want in (("E_EXP_DOWN", "5406745015365943482"),
                       ("E_MENU_SUPPORT", "5443038326535759644")):
        check("%s still holds its id" % name, getattr(config, name) == want,
              getattr(config, name))
    check("the env-backed links are untouched",
          config.SUPPORT_URL == "https://t.me/" + config.SUPPORT.lstrip("@"),
          config.SUPPORT_URL)

    # Screens either side of "register" in the funnel are intact.
    check("the access screen still leads here",
          [b[1] for row in config.SCREENS["access"]["kb"] for b in row]
          == ["cb:go:register"], str(config.SCREENS["access"]["kb"]))
    check("the access caption is unchanged",
          "In One Trade." in config.SCREENS["access"]["text"])
    check("the results caption is unchanged",
          "Real feedback from active Go+ traders." in
          config.SCREENS["results"]["text"])
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
    print("PASS - the register screen renders the specified copy and buttons.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
